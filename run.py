"""
run.py — main entry point.

Handles: login, challenge/accept/ladder modes, the battle loop.
Consolidates: websocket_client, run_battle, and the old main loop.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import traceback
from copy import deepcopy

import requests
import websockets

import constants
from config import FoulPlayConfig, SaveReplay, BotModes, Format, init_logging
from src.battle import Battle, Player
from src.helpers import normalize_name
from src.search import perform_searches_and_select_move
from teams import load_team, TeamListIterator
from data.mods.apply_mods import apply_mods

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class LoginError(Exception):
    pass


# ---------------------------------------------------------------------------
# WebSocket client
# ---------------------------------------------------------------------------

class PSWebsocketClient:
    def __init__(self) -> None:
        self.websocket = None
        self.address: str = ""
        self.login_uri: str = ""
        self.username: str = ""
        self.password: str | None = None
        self.last_message: str = ""
        self.last_challenge_time: float = 0.0

    @classmethod
    async def create(cls, username: str, password: str | None, address: str) -> "PSWebsocketClient":
        self = cls()
        self.username = username
        self.password = password
        self.address = address
        self.websocket = await websockets.connect(address)
        self.login_uri = (
            "https://play.pokemonshowdown.com/api/login"
            if password
            else "https://play.pokemonshowdown.com/action.php?"
        )
        return self

    async def receive_message(self) -> str:
        msg = await self.websocket.recv()
        logger.debug("recv: %s", msg)
        return msg

    async def send_message(self, room: str, message_list: list[str]) -> None:
        msg = room + "|" + "|".join(message_list)
        logger.debug("send: %s", msg)
        await self.websocket.send(msg)
        self.last_message = msg

    async def join_room(self, room_name: str) -> None:
        await self.send_message("", [f"/join {room_name}"])

    async def close(self) -> None:
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
            resp = requests.post(self.login_uri, data={"act": "getassertion", "userid": self.username, "challstr": combined})
        else:
            resp = requests.post(self.login_uri, data={"name": self.username, "pass": self.password, "challstr": combined})

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
        logger.info("logged in as %s", self.username)
        return self.username if self.password is None else json.loads(resp.text[1:])["curuser"]["userid"]

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
                    logger.info("avatar set to %s", avatar)
                else:
                    logger.warning("could not set avatar to %s, got %s", avatar, details.get("avatar"))
                break

    async def challenge_user(self, target: str, fmt: Format) -> None:
        logger.info("challenging %s", target)
        await self.send_message("", [f"/challenge {target},{fmt.value}"])
        self.last_challenge_time = time.time()

    async def accept_challenge(self, fmt: Format, room_name: str | None) -> None:
        if room_name is not None:
            await self.join_room(room_name)
        logger.info("waiting for %s challenge", fmt.value)
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
        logger.info("searching ladder for %s", fmt.value)
        await self.send_message("", [f"/search {fmt.value}"])

    async def leave_battle(self, tag: str) -> None:
        await self.send_message("", [f"/leave {tag}"])
        while True:
            msg = await self.receive_message()
            if tag in msg and "deinit" in msg:
                return

    async def save_replay(self, tag: str) -> None:
        await self.send_message(tag, ["/savereplay"])


# ---------------------------------------------------------------------------
# Battle loop helpers
# ---------------------------------------------------------------------------

def _format_decision(battle: Battle, decision: str) -> list[str]:
    if decision.startswith(constants.SWITCH_STRING + " "):
        switch_name = normalize_name(decision.split("switch ")[-1])
        our_side = battle.public.side(0)
        slot = None
        for pos in range(6):
            storage_idx = our_side.order[pos]
            name = normalize_name(battle._name_to_slot[0].get(storage_idx, ""))
            # reverse lookup: name_to_slot maps name->idx; invert to find name for idx
            for pname, pidx in battle._name_to_slot[0].items():
                if pidx == storage_idx and pname == switch_name:
                    slot = pos + 1
                    break
            if slot is not None:
                break
        if slot is None:
            logger.warning("could not find switch slot for %s, defaulting to 2", switch_name)
            slot = 2
        message = f"/choose switch {slot}"
    else:
        message = f"/choose {decision}"
    return [message, str(battle.rqid)]


def _battle_finished(tag: str, msg: str) -> bool:
    return (
        msg.startswith(f">{tag}")
        and (constants.WIN_STRING in msg or constants.TIE_STRING in msg)
        and constants.CHAT_STRING not in msg
    )


async def _pick_move(battle: Battle) -> list[str]:
    loop = asyncio.get_event_loop()
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor() as pool:
        decision = await loop.run_in_executor(pool, perform_searches_and_select_move, deepcopy(battle))
    return _format_decision(battle, decision)


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


async def _wait_for_first_request(client: PSWebsocketClient, battle: Battle) -> None:
    while True:
        msg = await client.receive_message()
        for line in msg.split("\n"):
            parts = line.split("|")
            if len(parts) >= 3 and parts[1].strip() == "request" and parts[2].strip():
                battle.parse_request(parts)
                return


async def _run_battle(client: PSWebsocketClient, fmt: Format, team_dict) -> str | None:
    tag, opp_name = await _get_battle_tag_and_opponent(client)

    p1 = Player(user=FoulPlayConfig.username)
    p2 = Player(user=opp_name)
    battle = Battle(tag, p1, p2)
    battle.format = fmt

    # identify which slot we are (p1 or p2)
    while True:
        msg = await client.receive_message()
        if "|player|" in msg and battle.p2.user in msg:
            parts = msg.split("|")
            battle.p2.user = parts[2]
            battle.p1.user = constants.ID_LOOKUP[battle.p2.user]
            break

    # wait for |start|
    while True:
        if constants.START_STRING in msg:
            battle.started = True
            battle.msg_lines = [
                m for m in msg.split(constants.START_STRING)[1].strip().split("\n")
                if m and not m.startswith(f"|switch|{battle.p1.user}")
            ]
            break
        msg = await client.receive_message()

    await _wait_for_first_request(client, battle)
    battle.process_msg_lines_and_clear()

    await asyncio.sleep(random.randint(3, 7))
    await client.send_message(tag, ["/timer on"])

    if not battle.wait:
        move = await _pick_move(battle)
        await client.send_message(tag, move)

    # main battle loop
    while True:
        msg = await client.receive_message()
        if _battle_finished(tag, msg):
            winner = (
                msg.split(constants.WIN_STRING)[-1].split("\n")[0].strip()
                if constants.WIN_STRING in msg
                else None
            )
            logger.info("winner: %s", winner)
            cfg = FoulPlayConfig
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
            move = await _pick_move(battle)
            await client.send_message(tag, move)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    FoulPlayConfig.configure()
    init_logging(FoulPlayConfig.log_level, FoulPlayConfig.log_to_file)
    apply_mods(FoulPlayConfig.format)

    client = await PSWebsocketClient.create(
        FoulPlayConfig.username, FoulPlayConfig.password, FoulPlayConfig.websocket_uri
    )
    FoulPlayConfig.user_id = await client.login()

    if FoulPlayConfig.avatar is not None:
        await client.avatar(FoulPlayConfig.avatar)

    team_iter = (
        TeamListIterator(FoulPlayConfig.team_list)
        if FoulPlayConfig.team_list is not None
        else None
    )

    wins = losses = ties = battles_run = 0

    while True:
        if FoulPlayConfig.requires_team():
            team_name = (
                team_iter.get_next_team() if team_iter else FoulPlayConfig.team_name
            )
            team_packed, team_dict, team_file = load_team(team_name)
            await client.update_team(team_packed)
        else:
            team_dict = None
            team_file = "None"
            await client.update_team("None")

        mode = FoulPlayConfig.bot_mode
        if mode == BotModes.challenge_user:
            await client.challenge_user(FoulPlayConfig.user_to_challenge, FoulPlayConfig.format)
        elif mode == BotModes.accept_challenge:
            await client.accept_challenge(FoulPlayConfig.format, FoulPlayConfig.room_name)
        elif mode == BotModes.search_ladder:
            await client.search_for_match(FoulPlayConfig.format)
        else:
            raise ValueError(f"unknown bot mode: {mode}")

        winner = await _run_battle(client, FoulPlayConfig.format, team_dict)

        if winner == FoulPlayConfig.username:
            wins += 1
            logger.info("won with %s", team_file)
        elif winner is None:
            ties += 1
            logger.info("tied with %s", team_file)
        else:
            losses += 1
            logger.info("lost with %s", team_file)

        logger.info("W:%d L:%d T:%d", wins, losses, ties)
        battles_run += 1
        if battles_run >= FoulPlayConfig.run_count:
            break

    await client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        logger.error(traceback.format_exc())
        raise
