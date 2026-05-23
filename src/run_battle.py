import json
import asyncio
import random
import concurrent.futures
from copy import deepcopy
import logging

from data.pkmn_sets import SmogonSets, TeamDatasets
import constants
from fp.battle import Battle, Player
from constants import BattleType
from config import FoulPlayConfig, SaveReplay, Format
from fp.helpers import normalize_name
from fp.search.main import (
    perform_searches_and_select_move_oak,
)

from fp.websocket_client import PSWebsocketClient

logger = logging.getLogger(__name__)


def format_decision(battle, decision) -> [str, str]:
    if decision.startswith(constants.SWITCH_STRING + " "):
        switch_pokemon = normalize_name(decision.split("switch ")[-1])
        # find the showdown slot (1-indexed) from order
        our_side = battle.public.side(0)
        slot = None
        for order_pos in range(6):
            storage_idx = our_side.order[order_pos]
            name = normalize_name(battle._names.get((0, storage_idx), ""))
            if name == switch_pokemon:
                slot = order_pos + 1  # showdown is 1-indexed
                break
        if slot is None:
            raise ValueError("Tried to switch to: {}".format(switch_pokemon))
        message = "/choose switch {}".format(slot)
    else:
        message = "/choose {}".format(decision)

    return [message, str(battle.rqid)]


def battle_is_finished(battle_tag, msg):
    return (
        msg.startswith(">{}".format(battle_tag))
        and (constants.WIN_STRING in msg or constants.TIE_STRING in msg)
        and constants.CHAT_STRING not in msg
    )


async def async_pick_move(battle):
    battle_copy = deepcopy(battle)

    loop = asyncio.get_event_loop()
    selected_move: str = None
    with concurrent.futures.ThreadPoolExecutor() as pool:
        selected_move = await loop.run_in_executor(
            pool, perform_searches_and_select_move_oak, battle_copy
        )
    return format_decision(battle_copy, selected_move)


async def get_battle_tag_and_opponent(ps_websocket_client: PSWebsocketClient):

    battle_tag = None
    p1 = None
    p2 = None

    while True:
        msg = await ps_websocket_client.receive_message()
        # --- LEVEL 1: room header ---
        if msg.startswith(">"):
            header, *body = msg.split("\n", 1)
            battle_tag = header[1:]
            lines = body[0].split("\n") if body else []
        else:
            lines = msg.split("\n")
        # --- LEVEL 2: protocol parsing ---
        for line in lines:
            if not line:
                continue
            parts = line.split("|")
            if len(parts) < 2:
                continue
            cmd = parts[1]
            if cmd == "init":
                # |init|battle
                pass
            elif cmd == "title":
                p1, p2 = parts[2].split(" vs. ")

            if battle_tag and p1 and p2:
                return battle_tag, p2


async def get_first_request_json(
    ps_websocket_client: PSWebsocketClient, battle: Battle
):
    while True:
        msg = await ps_websocket_client.receive_message()
        for line in msg.split("\n"):
            parts = line.split("|")
            if len(parts) >= 3 and parts[1].strip() == "request" and parts[2].strip():
                battle.parse_request(parts)
                return


async def start_battle_common(
    ps_websocket_client: PSWebsocketClient, battle_format: Format
):
    battle_tag, opponent_name = await get_battle_tag_and_opponent(ps_websocket_client)
    if FoulPlayConfig.log_to_file:
        FoulPlayConfig.file_log_handler.do_rollover(
            "{}_{}.log".format(battle_tag, opponent_name)
        )

    p1 = Player()
    p2 = Player()
    p1.user = FoulPlayConfig.username
    print(opponent_name)
    p2.user = opponent_name
    battle = Battle(battle_tag, p1, p2)
    battle.format = battle_format

    # wait until the opponent's identifier is received. This will be `p1` or `p2`.
    #
    # e.g.
    # '>battle-gen9randombattle-44733
    # |player|p1|OpponentName|2|'
    while True:
        msg = await ps_websocket_client.receive_message()
        if "|player|" in msg and battle.p2.user in msg:
            battle.p2.user = msg.split("|")[2]
            battle.p1.user = constants.ID_LOOKUP[battle.p2.user]
            break

    return battle, msg


async def start_standard_battle(
    ps_websocket_client: PSWebsocketClient, battle_format: Format, team_dict
):
    battle, msg = await start_battle_common(ps_websocket_client, battle_format)
    battle.team_dict = team_dict
    battle.battle_type = BattleType.STANDARD_BATTLE

    while True:
        if constants.START_STRING in msg:
            battle.started = True
            battle.msg_lines = [
                m
                for m in msg.split(constants.START_STRING)[1].strip().split("\n")
                if m and not m.startswith(f"|switch|{battle.p1.user}")
            ]
            break
        msg = await ps_websocket_client.receive_message()

    await get_first_request_json(ps_websocket_client, battle)

    SmogonSets.initialize(battle_format, set())
    TeamDatasets.initialize(battle_format, set())

    battle.process_msg_lines_and_clear()

    if not battle.wait:
        best_move = await async_pick_move(battle)
        await ps_websocket_client.send_message(battle.tag, best_move)

    return battle


async def start_battle(ps_websocket_client, battle_format: Format, team_dict):
    battle = await start_standard_battle(ps_websocket_client, battle_format, team_dict)
    await asyncio.sleep(random.randint(3, 7))
    await ps_websocket_client.send_message(battle.tag, ["/timer on"])
    return battle


async def pokemon_battle(
    ps_websocket_client, battle_format: Format, team_dict
) -> str | None:
    battle = await start_battle(ps_websocket_client, battle_format, team_dict)
    while True:
        msg = await ps_websocket_client.receive_message()
        if battle_is_finished(battle.tag, msg):
            winner = (
                msg.split(constants.WIN_STRING)[-1].split("\n")[0].strip()
                if constants.WIN_STRING in msg
                else None
            )
            logger.info("Winner: {}".format(winner))
            if (
                FoulPlayConfig.save_replay == SaveReplay.always
                or (
                    FoulPlayConfig.save_replay == SaveReplay.on_loss
                    and winner != FoulPlayConfig.username
                )
                or (
                    FoulPlayConfig.save_replay == SaveReplay.on_win
                    and winner == FoulPlayConfig.username
                )
            ):
                await ps_websocket_client.save_replay(battle.tag)
            await ps_websocket_client.leave_battle(battle.tag)
            return winner
        else:
            action_required = battle.update(msg)
            if action_required and not battle.wait:
                best_move = await async_pick_move(battle)
                await ps_websocket_client.send_message(battle.tag, best_move)
