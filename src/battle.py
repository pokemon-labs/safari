"""
class Battle:

Representation of the de-facto, imperfect information game from the agent's perspective

The features of the underlying sim battle are revealed from the protocol log, 
which essentially consists of the public history and a small portion of agent's private history

TODO explain how we leave slots/move-slots tails blank to represent unrevealed

In all perfect info games, the public signals encode information about what the opponent selected.
This is important strategic information that informs the players about each others 'types'

In Pokemon, the private information is immutable after the start of the battle and is slowly revealed.

Battle.public has incomplete slots/move-sets. The n-th pokemon to be revealed is at index (not slot) n -1.
Therefore the order array is always [*s_k, 0, ... 0], where s_k is a permutation on [1, k] and k <= n
In this way all opp switch histories have uniquely corresponding pkmn_choice sequences.
"""

from __future__ import annotations

import re
import json
import logging
from copy import deepcopy
from dataclasses import dataclass

import constants
from data import all_move_json
from src.helpers import normalize_name

logger = logging.getLogger(__name__)

import oak


WRAP_MOVES = {
    name
    for name, data in all_move_json.items()
    if data.get(constants.VOLATILE_STATUS) == constants.PARTIALLY_TRAPPED
}

_STATUS_BYTE = {
    constants.BURN: 0x10,
    constants.FROZEN: 0x20,
    constants.PARALYZED: 0x40,
    constants.POISON: 0x08,
    constants.TOXIC: 0x08,
    constants.SLEEP: 0x04,
}

_STAT_ABBREV_TO_BOOST_PROP = {
    "atk": "atk",
    "def": "def_",
    "spa": "spc",
    "spd": "spc",
    "spe": "spe",
    "accuracy": "acc",
    "evasion": "eva",
}


@dataclass
class Player:
    user: str = ""
    avatar: int | None = None
    rating: int | None = None
    pokemon: int = 6


def _species_id(name: str) -> int:
    normalized = normalize_name(name)
    for i, n in enumerate(oak.species_names):
        if normalize_name(n) == normalized:
            return i
    return 0


def _move_id(name: str) -> int:
    normalized = normalize_name(name)
    for i, n in enumerate(oak.move_names):
        if normalize_name(n) == normalized:
            return i
    return 0


class Battle:
    def __init__(self, tag: str, p1: Player, p2: Player):
        self.tag = tag
        self.p1 = p1  # us — always p1
        self.p2 = p2  # opponent — always p2

        self.rules: list[str] = []
        self.format: str = ""

        # both sides revealed from protocol
        self.public = oak.Battle(bytes(384))
        self.durations = oak.Durations(bytes(8))
        # our side determined from request; overlays public.side(0)
        self.private = oak.Battle(bytes(384))

        self.request: dict | None = None
        self.msg_lines: list[str] = []

        self.started: bool = False
        self.rqid: int | None = None
        self.force_switch: bool = False
        self.wait: bool = False
        self.time_remaining: int | None = None
        self.team_dict = None

        # tracking state not in the oak struct
        self._max_hp: dict[tuple[int, int], int] = {}
        self._name_to_slot: dict[int, dict[str, int]] = {0: {}, 1: {}}
        self._seen: dict[int, int] = {0: 0, 1: 0}
        self._active_slot: dict[int, int] = {0: 0, 1: 0}
        self._bind_turns: dict[int, int] = {0: 0, 1: 0}

    
    def determinize(self, use_private: bool = True) -> oak.Battle:
        """Produce a fully-determined oak.Battle for search.

        Side 0 is filled from self.private (or completed) and side 1 is
        filled via TeamPredictor. Oak sides are C++ proxies and cannot be
        assigned directly; we copy bytes and reconstruct.
        """
        return deepcopy(self.public)

    # -----------------------------------------------------------------------
    # Oak side/active shortcuts that keep public + private in sync
    # -----------------------------------------------------------------------

    def sides(self, split_msg):
        """Return (our_pub, our_priv, opp_pub, opp_priv) sides."""
        if self._is_opponent(split_msg):
            return (
                self.public.side(1),
                self.private.side(1),
                self.public.side(0),
                self.private.side(0),
            )
        return (
            self.public.side(0),
            self.private.side(0),
            self.public.side(1),
            self.private.side(1),
        )

    def actives(self, split_msg):
        u_pub, u_priv, o_pub, o_priv = self.sides(split_msg)
        return u_pub.active(), u_priv.active(), o_pub.active(), o_priv.active()

    def volatiles(self, split_msg):
        u_pub, u_priv, o_pub, o_priv = self.sides(split_msg)
        return (
            u_pub.active().volatiles(),
            u_priv.active().volatiles(),
            o_pub.active().volatiles(),
            o_priv.active().volatiles(),
        )

    # -----------------------------------------------------------------------
    # Public interface
    # -----------------------------------------------------------------------

    def update(self, msg: str):
        for line in msg.split("\n"):
            split_msg = line.split("|")
            if len(split_msg) < 2:
                continue
            action = split_msg[1].strip()
            if action == "request":
                self.parse_request(split_msg)
                self.process_msg_lines_and_clear()
                return not self.wait
            else:
                self.msg_lines.append(line)

    def process_msg_lines_and_clear(self):
        for line in self.msg_lines:
            split_msg = line.split("|")
            if len(split_msg) < 2:
                continue
            action = split_msg[1].strip()
            fn = self._HANDLERS.get(action)
            if fn:
                try:
                    fn(self, split_msg)
                except Exception as e:
                    logger.warning(f"handler '{action}' raised: {e}")
        self.msg_lines.clear()

    # -----------------------------------------------------------------------
    # Request
    # -----------------------------------------------------------------------

    def parse_request(self, split_msg: list[str]):
        if len(split_msg) < 3:
            return
        raw = split_msg[2].strip("'")
        if not raw:
            return
        req: dict = json.loads(raw)
        self.request = req
        self.rqid = req.get(constants.RQID)
        self.force_switch = bool(req.get(constants.FORCE_SWITCH))
        self.wait = bool(req.get(constants.WAIT))
        self._apply_request()

    def _apply_request(self):
        """Write request JSON into self.private (and sync into self.public side 0)."""
        if not self.request:
            return
        side_data = self.request.get("side", {})
        pokemon_list = side_data.get("pokemon", [])
        our_side_idx = 0  # we are always p1

        for storage_idx, poke_json in enumerate(pokemon_list[:6]):
            details = poke_json.get("details", "")
            species_name, level = _parse_details(details)

            condition = poke_json.get("condition", "")
            hp, max_hp, status_str = _parse_condition(condition)
            self._max_hp[(our_side_idx, storage_idx)] = max_hp

            name_key = normalize_name(species_name)
            if name_key not in self._name_to_slot[our_side_idx]:
                self._name_to_slot[our_side_idx][name_key] = storage_idx
            if storage_idx >= self._seen[our_side_idx]:
                self._seen[our_side_idx] = storage_idx + 1

            for battle in (self.public, self.private):
                pkmn = battle.side(our_side_idx).pokemon(storage_idx)
                pkmn.species = _species_id(species_name)
                pkmn.level = level
                pkmn.hp = hp
                pkmn.status = _status_byte(status_str, hp)

            # moves come from side.pokemon[].moves (id strings like "return102")
            moves = poke_json.get("moves", [])
            for move_idx, move_id in enumerate(moves[:4]):
                mid = _move_id(normalize_name(move_id))
                for battle in (self.public, self.private):
                    battle.side(our_side_idx).pokemon(storage_idx).move(
                        move_idx
                    ).id = mid

            # pp comes from active[0].moves (has actual pp values)
            # only valid for the currently active pokemon
            if poke_json.get("active", False):
                self._active_slot[our_side_idx] = storage_idx
                active_moves = self.request.get("active", [{}])[0].get("moves", [])
                for move_idx, move_json in enumerate(active_moves[:4]):
                    pp = move_json.get("pp", 0)
                    for battle in (self.public, self.private):
                        battle.side(our_side_idx).pokemon(storage_idx).move(
                            move_idx
                        ).pp = pp

        # set order: active first, rest in roster order
        active_slot = self._active_slot[our_side_idx]
        order = [active_slot] + [i for i in range(6) if i != active_slot]
        for battle in (self.public, self.private):
            battle.side(our_side_idx).order = order

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _is_opponent(self, split_msg) -> bool:
        if len(split_msg) < 3:
            return False
        # p1 is us, p2 is opponent
        return split_msg[2].startswith("p2")

    def _resolve_slot(self, side_idx: int, ident_or_details: str) -> int:
        name = normalize_name(ident_or_details.split(":")[-1].split(",")[0].strip())
        if name not in self._name_to_slot[side_idx]:
            slot = self._seen[side_idx]
            self._seen[side_idx] += 1
            self._name_to_slot[side_idx][name] = slot
        return self._name_to_slot[side_idx][name]

    def _boost_add(self, side_idx: int, stat: str, delta: int):
        prop = _STAT_ABBREV_TO_BOOST_PROP.get(stat)
        if not prop:
            return
        for battle in (self.public, self.private):
            b = battle.side(side_idx).active().boosts()
            cur = getattr(b, prop)
            setattr(b, prop, max(-6, min(6, cur + delta)))

    def _boost_set(self, side_idx: int, stat: str, value: int):
        prop = _STAT_ABBREV_TO_BOOST_PROP.get(stat)
        if not prop:
            return
        for battle in (self.public, self.private):
            b = battle.side(side_idx).active().boosts()
            setattr(b, prop, max(-6, min(6, value)))

    def _clear_boosts(self, side_idx: int):
        for battle in (self.public, self.private):
            b = battle.side(side_idx).active().boosts()
            b.atk = b.def_ = b.spe = b.spc = b.acc = b.eva = 0

    def _set_vol(self, side_idx: int, **kwargs):
        for battle in (self.public, self.private):
            v = battle.side(side_idx).active().volatiles()
            for attr, val in kwargs.items():
                setattr(v, attr, val)

    def _clear_switch_volatiles(self, side_idx: int):
        for battle in (self.public, self.private):
            v = battle.side(side_idx).active().volatiles()
            v.bits = 0
            b = battle.side(side_idx).active().boosts()
            b.atk = b.def_ = b.spe = b.spc = b.acc = b.eva = 0
        self._bind_turns[side_idx] = 0

    def _set_hp(self, side_idx: int, storage_idx: int, hp: int):
        for battle in (self.public, self.private):
            battle.side(side_idx).pokemon(storage_idx).hp = hp

    def _set_status(self, side_idx: int, storage_idx: int, byte: int):
        for battle in (self.public, self.private):
            battle.side(side_idx).pokemon(storage_idx).status = byte

    def _set_order(self, side_idx: int, order: list):
        for battle in (self.public, self.private):
            battle.side(side_idx).order = order

    # -----------------------------------------------------------------------
    # Protocol handlers
    # -----------------------------------------------------------------------

    def switch_or_drag(self, split_msg):
        side_idx = 1 if self._is_opponent(split_msg) else 0
        details = split_msg[3] if len(split_msg) > 3 else ""
        condition = split_msg[4] if len(split_msg) > 4 else ""

        species_name, level = _parse_details(details)
        storage_slot = self._resolve_slot(side_idx, details)
        self._active_slot[side_idx] = storage_slot

        hp, max_hp, status_str = _parse_condition(condition)
        if max_hp:
            self._max_hp[(side_idx, storage_slot)] = max_hp
        else:
            max_hp = self._max_hp.get((side_idx, storage_slot), 100)
            # opponent HP is given as percentage
            if side_idx == 1 and max_hp:
                hp = int(max_hp * hp / 100)

        for battle in (self.public, self.private):
            pkmn = battle.side(side_idx).pokemon(storage_slot)
            pkmn.species = _species_id(species_name)
            pkmn.level = level
            pkmn.hp = hp
            pkmn.status = _status_byte(status_str, hp)

        order = [storage_slot] + [i for i in range(6) if i != storage_slot]
        self._set_order(side_idx, order)
        self._clear_switch_volatiles(side_idx)

    def faint(self, split_msg):
        side_idx = 1 if self._is_opponent(split_msg) else 0
        storage_slot = self._active_slot[side_idx]
        self._set_hp(side_idx, storage_slot, 0)

    def heal_or_damage(self, split_msg):
        side_idx = 1 if self._is_opponent(split_msg) else 0
        condition = split_msg[3] if len(split_msg) > 3 else ""
        storage_slot = self._active_slot[side_idx]
        max_hp = self._max_hp.get((side_idx, storage_slot), 100)

        hp, new_max, status_str = _parse_condition(condition)
        if side_idx == 1:
            hp = int(max_hp * hp / 100)
        else:
            if new_max:
                self._max_hp[(side_idx, storage_slot)] = new_max

        self._set_hp(side_idx, storage_slot, hp)
        if status_str:
            self._set_status(side_idx, storage_slot, _status_byte(status_str, hp))

        # gen1: hitting self in confusion releases opponent binding
        other_idx = 1 - side_idx
        if len(split_msg) > 4 and "[from] confusion" in split_msg[-1]:
            self._set_vol(other_idx, binding=False)
            self._bind_turns[other_idx] = 0

    def sethp(self, split_msg):
        self.heal_or_damage(split_msg)

    def fail(self, _split_msg):
        pass

    def move(self, split_msg):
        side_idx = 1 if self._is_opponent(split_msg) else 0
        other_idx = 1 - side_idx
        move_name = normalize_name(split_msg[3]) if len(split_msg) > 3 else ""
        missed = any(s.strip() == "[miss]" for s in split_msg)

        if move_name in WRAP_MOVES and not missed:
            self._bind_turns[other_idx] += 1
            self._set_vol(other_idx, binding=True)

        # moving frees self from binding
        if self._bind_turns[side_idx] > 0:
            self._set_vol(side_idx, binding=False)
            self._bind_turns[side_idx] = 0

        # reveal opponent move
        if side_idx == 1 and move_name and move_name != "struggle":
            mid = _move_id(move_name)
            for battle in (self.public, self.private):
                act = battle.side(1).active()
                for mi in range(4):
                    slot = act.move(mi)
                    if slot.id == mid:
                        if slot.pp > 0:
                            slot.pp -= 1
                        break
                    if slot.id == 0:
                        slot.id = mid
                        break

    def boost(self, split_msg):
        side_idx = 1 if self._is_opponent(split_msg) else 0
        stat = split_msg[3].strip() if len(split_msg) > 3 else ""
        amount = int(split_msg[4].strip()) if len(split_msg) > 4 else 0
        self._boost_add(side_idx, stat, amount)

    def unboost(self, split_msg):
        side_idx = 1 if self._is_opponent(split_msg) else 0
        stat = split_msg[3].strip() if len(split_msg) > 3 else ""
        amount = int(split_msg[4].strip()) if len(split_msg) > 4 else 0
        self._boost_add(side_idx, stat, -amount)

    def setboost(self, split_msg):
        side_idx = 1 if self._is_opponent(split_msg) else 0
        stat = split_msg[3].strip() if len(split_msg) > 3 else ""
        amount = int(split_msg[4].strip()) if len(split_msg) > 4 else 0
        self._boost_set(side_idx, stat, amount)

    def clearnegativeboost(self, split_msg):
        side_idx = 1 if self._is_opponent(split_msg) else 0
        for battle in (self.public, self.private):
            b = battle.side(side_idx).active().boosts()
            for attr in ("atk", "def_", "spe", "spc", "acc", "eva"):
                if getattr(b, attr) < 0:
                    setattr(b, attr, 0)

    def clearboost(self, split_msg):
        side_idx = 1 if self._is_opponent(split_msg) else 0
        self._clear_boosts(side_idx)

    def clearallboost(self, _split_msg):
        self._clear_boosts(0)
        self._clear_boosts(1)

    def status(self, split_msg):
        side_idx = 1 if self._is_opponent(split_msg) else 0
        status_str = split_msg[3].strip() if len(split_msg) > 3 else ""
        storage_slot = self._active_slot[side_idx]
        byte = _STATUS_BYTE.get(status_str, 0)
        if status_str == constants.SLEEP:
            # rest gives 3 hidden turns
            if len(split_msg) > 4 and split_msg[4].strip() == "[from] move: Rest":
                byte = 0x04 | 3
            else:
                byte = 0x04
        self._set_status(side_idx, storage_slot, byte)

    def curestatus(self, split_msg):
        side_idx = 1 if self._is_opponent(split_msg) else 0
        # try to find the right storage slot from ident; fall back to active
        ident = split_msg[2] if len(split_msg) > 2 else ""
        slot = (
            self._resolve_slot(side_idx, ident)
            if ident
            else self._active_slot[side_idx]
        )
        self._set_status(side_idx, slot, 0)

    def start_volatile_status(self, split_msg):
        u_vol, u_vol_priv, *_ = self.volatiles(split_msg)
        vol = normalize_name(split_msg[3].split(":")[-1]) if len(split_msg) > 3 else ""

        side_idx = 1 if self._is_opponent(split_msg) else 0

        if vol == constants.REFLECT:
            self._set_vol(side_idx, reflect=True)
        elif vol == constants.LIGHT_SCREEN:
            self._set_vol(side_idx, light_screen=True)
        elif vol == constants.MIST:
            self._set_vol(side_idx, mist=True)
        elif vol == constants.SUBSTITUTE:
            for battle in (self.public, self.private):
                act = battle.side(side_idx).active()
                v = act.volatiles()
                v.substitute = True
                v.substitute_hp = act.stats().hp // 4
        elif vol == constants.CONFUSION:
            self._set_vol(side_idx, confusion=True, confusion_left=3)
        elif vol == "focusenergy":
            self._set_vol(side_idx, focus_energy=True)
        elif vol == "leechseed":
            self._set_vol(side_idx, leech_seed=True)
        else:
            assert False, f"Unexpected vol start: {vol}"

    def end_volatile_status(self, split_msg):
        side_idx = 1 if self._is_opponent(split_msg) else 0
        vol = normalize_name(split_msg[3].split(":")[-1]) if len(split_msg) > 3 else ""

        if vol == constants.SUBSTITUTE:
            self._set_vol(side_idx, substitute=False, substitute_hp=0)
        elif vol == constants.CONFUSION:
            self._set_vol(side_idx, confusion=False, confusion_left=0)
        elif vol == "leechseed":
            self._set_vol(side_idx, leech_seed=False)
        elif vol == constants.PARTIALLY_TRAPPED:
            self._set_vol(side_idx, binding=False)
            self._bind_turns[side_idx] = 0
        elif vol == "mustrecharge":
            self._set_vol(side_idx, recharging=False)
        elif vol == constants.REFLECT:
            self._set_vol(side_idx, reflect=False)
        elif vol == constants.LIGHT_SCREEN:
            self._set_vol(side_idx, light_screen=False)
        elif vol == constants.MIST:
            self._set_vol(side_idx, mist=False)
        elif vol == constants.TRANSFORM:
            self._set_vol(side_idx, transform=False)
        else:
            assert False, f"Unexpected vol end: {vol}"

    def mustrecharge(self, split_msg):
        side_idx = 1 if self._is_opponent(split_msg) else 0
        self._set_vol(side_idx, recharging=True)

    def singleturn(self, _split_msg):
        pass  # no protect in gen1

    def transform(self, split_msg):
        side_idx = 1 if self._is_opponent(split_msg) else 0
        other_idx = 1 - side_idx
        self._set_vol(side_idx, transform=True)

        for battle in (self.public, self.private):
            sb = battle.side(side_idx).active().boosts()
            ob = battle.side(other_idx).active().boosts()
            sb.atk = ob.atk
            sb.def_ = ob.def_
            sb.spe = ob.spe
            sb.spc = ob.spc
            v = battle.side(side_idx).active().volatiles()
            v.transform_species = battle.side(other_idx).active().species & 0xF

    def activate(self, split_msg):
        side_idx = 1 if self._is_opponent(split_msg) else 0
        if len(split_msg) < 4:
            return
        effect = split_msg[3]
        if effect.lower().startswith("move: "):
            move_name = normalize_name(effect.split(":")[-1])
            if move_name in WRAP_MOVES:
                self._set_vol(side_idx, binding=True)

    def prepare(self, split_msg):
        side_idx = 1 if self._is_opponent(split_msg) else 0
        move_name = normalize_name(split_msg[3]) if len(split_msg) > 3 else ""
        if move_name in ("solarbeam", "skyattack", "razorwind"):
            self._set_vol(side_idx, charging=True)
        else:
            assert False, f"Prepare unexpected move: {move_name}"

    def cant(self, split_msg):
        side_idx = 1 if self._is_opponent(split_msg) else 0
        other_idx = 1 - side_idx
        if len(split_msg) < 4:
            return
        reason = split_msg[3].strip()

        if reason == "recharge":
            self._set_vol(side_idx, recharging=False)

        if reason == constants.PARALYZED:
            # gen1: full paralysis releases partial trap on other side
            for battle in (self.public, self.private):
                ov = battle.side(other_idx).active().volatiles()
                if ov.binding:
                    ov.binding = False
                    self._bind_turns[other_idx] = 0

        if reason == constants.SLEEP:
            # dec rest turns; cure on wake
            storage_slot = self._active_slot[side_idx]
            for battle in (self.public, self.private):
                pkmn = battle.side(side_idx).pokemon(storage_slot)
                status = pkmn.status
                turns = status & 0x07
                if turns > 0:
                    new_turns = turns - 1
                    pkmn.status = (status & ~0x07) | new_turns
                    if new_turns == 0:
                        pkmn.status = 0

    def upkeep(self, _split_msg):
        pass

    def turn(self, split_msg):
        if len(split_msg) > 2:
            try:
                self.public.turn = int(split_msg[2])
                self.private.turn = int(split_msg[2])
            except ValueError:
                pass

    def noinit(self, split_msg):
        if len(split_msg) > 3 and split_msg[2] == "rename":
            self.tag = split_msg[3]

    def inactive(self, split_msg):
        if len(split_msg) > 2 and split_msg[2].startswith(constants.TIME_LEFT):
            m = re.search(r"(\d+) sec this turn", split_msg[2])
            if m:
                try:
                    self.time_remaining = int(m.group(1))
                except ValueError:
                    pass

    def inactiveoff(self, _split_msg):
        self.time_remaining = None

    def immune(self, _split_msg):
        pass

    def anim(self, _split_msg):
        pass

    _HANDLERS = {
        "switch": switch_or_drag,
        "drag": switch_or_drag,
        "faint": faint,
        "-fail": fail,
        "-heal": heal_or_damage,
        "-damage": heal_or_damage,
        "-sethp": sethp,
        "move": move,
        "-setboost": setboost,
        "-boost": boost,
        "-unboost": unboost,
        "-clearnegativeboost": clearnegativeboost,
        "-clearboost": clearboost,
        "-clearallboost": clearallboost,
        "-status": status,
        "-curestatus": curestatus,
        "-activate": activate,
        "-anim": anim,
        "-prepare": prepare,
        "-start": start_volatile_status,
        "-singlemove": start_volatile_status,
        "-end": end_volatile_status,
        "-immune": immune,
        "-transform": transform,
        "-clearnegativeboost": clearnegativeboost,
        "-singleturn": singleturn,
        "-mustrecharge": mustrecharge,
        "upkeep": upkeep,
        "cant": cant,
        "inactive": inactive,
        "inactiveoff": inactiveoff,
        "turn": turn,
        "noinit": noinit,
    }


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_details(details: str):
    """Return (species_name_str, level_int) from a showdown details string."""
    parts = details.split(",")
    species = parts[0].strip()
    level = 100
    for part in parts[1:]:
        p = part.strip()
        if p.startswith("L"):
            try:
                level = int(p[1:])
            except ValueError:
                pass
    return species, level


def _parse_condition(condition: str):
    """Return (hp, max_hp, status_str) from a condition string like '100/200 brn' or 'fnt'."""
    if not condition or constants.FNT in condition:
        return 0, 0, None
    parts = condition.split("/")
    try:
        hp = int(parts[0])
    except ValueError:
        return 0, 0, None
    max_hp = 0
    status_str = None
    if len(parts) > 1:
        rhs = parts[1].split()
        try:
            max_hp = int(rhs[0])
        except (ValueError, IndexError):
            pass
        if len(rhs) > 1:
            status_str = rhs[1]
    return hp, max_hp, status_str


def _status_byte(status_str: str | None, hp: int = 1) -> int:
    if not status_str or hp == 0:
        return 0
    return _STATUS_BYTE.get(status_str, 0)
