from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import random
import time
import traceback
from collections import defaultdict
from copy import deepcopy
from enum import Enum, auto

import requests
import websockets
import websockets.asyncio.client

from src.config import Config, SaveReplay, BotModes, Format, init_logging, Policy, Selection
from src.battle import PSBattle, PSPlayer, normalize_name
from src.teams import TeamPredictor, to_packed, get_teams_and_probs, team_to_string
from src.search import Player, Search
from src.vis import DebugViz

_viz: DebugViz | None = None

logger = logging.getLogger(__name__)

START_STRING = "|start"
WIN_STRING = "|win|"
TIE_STRING = "|tie"
CHAT_STRING = "|c|"


class LoginError(Exception):
    pass


class PSWebsocketClient:
    def __init__(self) -> None:
        self.websocket: websockets.asyncio.client.ClientConnection | None = None
        self.address: str = ""
        self.login_uri: str = ""
        self.username: str = ""
        self.password: str | None = None
        self.last_message: str = ""
        self.last_challenge_time: float = 0.0

    @classmethod
    async def create(
        cls, username: str, password: str | None, address: str, login_address: str
    ) -> "PSWebsocketClient":
        self = cls()
        self.username = username
        self.password = password
        self.address = address
        self.websocket = await websockets.connect(address)
        login_default = (
            "https://play.pokemonshowdown.com/api/login"
            if password
            else "https://play.pokemonshowdown.com/action.php?"
        )
        self.login_uri = login_address if login_address else login_default
        return self

    async def receive_message(self) -> str:
        assert self.websocket is not None
        raw = await self.websocket.recv()
        msg = raw if isinstance(raw, str) else raw.decode()
        logger.debug(f"recv: {msg}")
        return msg

    async def send_message(self, room: str, message_list: list[str]) -> None:
        assert self.websocket is not None
        msg = room + "|" + "|".join(message_list)
        logger.debug(f"send: {msg}")
        await self.websocket.send(msg)
        self.last_message = msg

    async def join_room(self, room_name: str) -> None:
        await self.send_message("", [f"/join {room_name}"])

    async def close(self) -> None:
        assert self.websocket is not None
        await self.websocket.close()

    async def _get_challstr(self) -> tuple[str, str]:
        while True:
            msg = await self.receive_message()
            parts = msg.split("|")
            if len(parts) >= 4 and parts[1] == "challstr":
                return parts[2], parts[3]

    async def login(self) -> str:
        logger.info("logging in...")
        client_id, challstr = await self._get_challstr()
        combined = "|".join([client_id, challstr])

        if self.password is None:
            resp = requests.post(
                self.login_uri,
                data={
                    "act": "getassertion",
                    "userid": self.username,
                    "challstr": combined,
                },
            )
        else:
            resp = requests.post(
                self.login_uri,
                data={
                    "act": "login",
                    "name": self.username,
                    "pass": self.password,
                    "challstr": combined,
                },
            )

        if resp.status_code != 200:
            raise LoginError(f"HTTP {resp.status_code}: {resp.content}")

        if self.password is None:
            assertion = resp.text
        else:
            data = json.loads(resp.text[1:])
            if "actionsuccess" not in data:
                raise LoginError(f"login failed: {data}")
            assertion = data["assertion"]

        await self.send_message("", [f"/trn {self.username},0,{assertion}"])
        await asyncio.sleep(3)
        logger.info(f"logged in as {self.username}")
        return (
            self.username
            if self.password is None
            else json.loads(resp.text[1:])["curuser"]["userid"]
        )

    async def update_team(self, team: str) -> None:
        await self.send_message("", [f"/utm {team}"])

    async def avatar(self, avatar: str) -> None:
        await self.send_message("", [f"/avatar {avatar}"])
        await self.send_message("", [f"/cmd userdetails {self.username}"])
        while True:
            msg = await self.receive_message()
            parts = msg.split("|")
            if len(parts) >= 4 and parts[1] == "queryresponse":
                details = json.loads(parts[3])
                if details.get("avatar") == avatar:
                    logger.info(f"avatar set to {avatar}")
                else:
                    logger.warning(
                        f"could not set avatar to {avatar}, got {details.get('avatar')}"
                    )
                break

    async def challenge_user(self, target: str, fmt: Format) -> None:
        logger.info(f"challenging {target}")
        await self.send_message("", [f"/challenge {target},{fmt.value}"])
        self.last_challenge_time = time.time()

    async def accept_challenge(self, fmt: Format, room_name: str | None) -> None:
        if room_name is not None:
            await self.join_room(room_name)
        logger.info(f"waiting for {fmt.value} challenge")
        username = None
        while username is None:
            msg = await self.receive_message()
            parts = msg.split("|")
            if (
                len(parts) == 9
                and parts[1] == "pm"
                and parts[3].strip().replace("!", "").replace("‽", "") == self.username
                and parts[4].startswith("/challenge")
                and parts[5] == fmt.value
            ):
                username = parts[2].strip()
        await self.send_message("", [f"/accept {username}"])

    async def search_for_match(self, fmt: Format) -> None:
        logger.info(f"searching ladder for {fmt.value}")
        await self.send_message("", [f"/search {fmt.value}"])

    async def leave_battle(self, tag: str) -> None:
        await self.send_message("", [f"/leave {tag}"])
        while True:
            msg = await self.receive_message()
            if tag in msg and "deinit" in msg:
                return

    async def save_replay(self, tag: str) -> None:
        await self.send_message(tag, ["/savereplay"])


def _battle_finished(tag: str, msg: str) -> bool:
    return (
        msg.startswith(f">{tag}")
        and (WIN_STRING in msg or TIE_STRING in msg)
        and CHAT_STRING not in msg
    )


async def _pick_move(battle: PSBattle, predictor: TeamPredictor) -> tuple[str, str]:
    p1_teams, p1_probs = get_teams_and_probs(
        battle.public.side(0), predictor, Config.p1_types, battle.team
    )
    p2_teams, p2_probs = get_teams_and_probs(
        battle.public.side(1), predictor, Config.p2_types
    )

    p1_player = Player(battle.public.side(0), p1_teams, p1_probs)
    p2_player = Player(battle.public.side(1), p2_teams, p2_probs)
    search = Search(battle, p1_player, p2_player)
    search.init_battles()
    search.run()
    search.solve()

    policy = search.p1.strategies[0][Config.policy]
     

    # choose p1 move
    eps = 1e-3
    actual_strategy = [float(x) if float(x) > eps else 0 for x in a[0]]
    as_sum = sum(actual_strategy)
    actual_strategy = [x / as_sum for x in actual_strategy]
    output = search.outputs[(0, 0)]
    c = random.choices(
        output["p1_choices"][: output["m"]], weights=actual_strategy[: output["m"]], k=1
    )[0]
    pending_move = search.parse_pkmn_choice(c)

    if _viz is not None:
        _viz.push(battle, search, a, b, pending_move)
        override = _viz.get_move_override()
        if override is not None:
            return (override, str(battle.rqid))

    return (pending_move, str(battle.rqid))


async def _get_battle_tag_and_opponent(client: PSWebsocketClient) -> tuple[str, str]:
    tag = p1 = p2 = None
    while True:
        msg = await client.receive_message()
        if msg.startswith(">"):
            header, *rest = msg.split("\n", 1)
            tag = header[1:]
            lines = rest[0].split("\n") if rest else []
        else:
            lines = msg.split("\n")
        for line in lines:
            parts = line.split("|")
            if len(parts) >= 2 and parts[1] == "title":
                p1, p2 = parts[2].split(" vs. ")
        if tag and p1 and p2:
            return tag, p2


async def _wait_for_first_request(client: PSWebsocketClient, battle: PSBattle) -> None:
    while True:
        msg = await client.receive_message()
        for line in msg.split("\n"):
            parts = line.split("|")
            if len(parts) >= 3 and parts[1].strip() == "request" and parts[2].strip():
                battle.parse_request(parts)
                return


class Result(Enum):
    none = auto()
    win = auto()
    lose = auto()
    tie = auto()
    error = auto()


async def _run_battle(
    client: PSWebsocketClient,
    fmt: Format,
    predictor: TeamPredictor,
    selected_team: Team,
) -> str | None:

    tag, opp_name = await _get_battle_tag_and_opponent(client)
    p1 = PSPlayer(user=Config.username)
    p2 = PSPlayer(user=opp_name)
    battle = PSBattle(tag, p1, p2)
    battle.format = fmt.value
    battle.team = selected_team
    while battle.us is None:
        msg = await client.receive_message()
        for line in msg.split("\n"):
            parts = line.split("|")
            if len(parts) >= 4 and parts[1] == "player":
                slot, uname = parts[2], parts[3]
                if normalize_name(uname) == normalize_name(Config.username):
                    if slot == "p1":
                        battle.us = "p1"
                        pass
                    elif slot == "p2":
                        battle.us = "p2"
                        battle.p1, battle.p2 = battle.p2, battle.p1
                    else:
                        assert (
                            False
                        ), f"Bad slot deduction, expected p1/p2 but got {slot}"

    # wait for |start| (may already be in msg from above)
    while True:
        if START_STRING in msg:
            battle.started = True
            after_start = msg.split(START_STRING, 1)[1].strip()
            battle.msg_lines = [
                m
                for m in after_start.split("\n")
                if m and not m.startswith(f"|switch|{battle.p1.user}")
            ]
            break
        msg = await client.receive_message()

    await _wait_for_first_request(client, battle)
    battle.process_msg_lines_and_clear()

    # await client.send_message(tag, ["/timer on"])

    # TODO ask pmarg why this is here
    if not battle.wait:
        move = await _pick_move(battle, predictor)
        await client.send_message(tag, move)

    # main battle loop
    while True:
        msg = await client.receive_message()

        if _battle_finished(tag, msg):
            winner = (
                msg.split(WIN_STRING)[-1].split("\n")[0].strip()
                if WIN_STRING in msg
                else None
            )
            logger.info(f"winner: {winner}")
            cfg = Config
            if (
                cfg.save_replay == SaveReplay.always
                or (cfg.save_replay == SaveReplay.on_loss and winner != cfg.username)
                or (cfg.save_replay == SaveReplay.on_win and winner == cfg.username)
            ):
                await client.save_replay(tag)
            await client.leave_battle(tag)
            return winner

        action_required = battle.update(msg)
        if action_required and not battle.wait:
            move = await _pick_move(battle, predictor)
            await client.send_message(tag, move)


async def main() -> None:
    Config.configure()
    init_logging(Config.log_level, Config.log_to_file)

    client = await PSWebsocketClient.create(
        Config.username, Config.password, Config.websocket_uri, Config.login_uri
    )
    Config.user_id = await client.login()

    if Config.avatar is not None:
        await client.avatar(Config.avatar)

    if Config.vis:
        global _viz
        _viz = DebugViz()
        _viz.start()
        logger.info("visualizer at http://localhost:8765")

    user_teams = TeamPredictor(Config.teams)
    predictor = TeamPredictor(Config.predictor_teams, Config.predictor_ratio)

    battles_run = 0
    record: dict[Result, int] = defaultdict(lambda: 0)
    while True:

        selected_team = random.choice(user_teams.teams)
        await client.update_team(to_packed(selected_team))

        mode = Config.bot_mode
        if mode == BotModes.challenge_user:
            await client.challenge_user(Config.user_to_challenge, Config.format)
        elif mode == BotModes.accept_challenge:
            await client.accept_challenge(Config.format, Config.room_name)
        elif mode == BotModes.search_ladder:
            await client.search_for_match(Config.format)
        else:
            raise ValueError(f"unknown bot mode: {mode}")

        winner = await _run_battle(client, Config.format, predictor, selected_team)

        if winner == Config.username:
            record[Result.win] += 1
            logger.info(f"won with {team_to_string(selected_team)}")
        elif winner is None:
            record[Result.tie] += 1
            logger.info(f"tied with {team_to_string(selected_team)}")
        else:
            record[Result.lose] += 1
            logger.info(f"lost with {team_to_string(selected_team)}")

        # logger.info(f"W:{wins} L:{losses} T:{ties}")
        battles_run += 1
        if battles_run >= Config.run_count:
            break

    await client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        logger.error(traceback.format_exc())
        raise
