from __future__ import annotations

import re
import json
import logging
from copy import deepcopy
from dataclasses import dataclass

import src.constants as constants

logger = logging.getLogger(__name__)

import oak

type Msg = list[str]


def normalize_name(name):
    return (
        name.replace(" ", "")
        .replace("-", "")
        .replace(".", "")
        .replace("'", "")
        .replace("%", "")
        .replace("*", "")
        .replace(":", "")
        .replace("(", "")
        .replace(")", "")
        .strip()
        .lower()
        .encode("ascii", "ignore")
        .decode("utf-8")
    )


BINDING_MOVES = {"bind", "clamp", "firespin", "wrap"}

# fmt: off
_STATUS_BYTE = {
    constants.SLEEP:     0b00000001,
    constants.BURN:      0b00010000,
    constants.FROZEN:    0b00100000,
    constants.PARALYZED: 0b01000000,
    constants.POISON:    0b00001000,
    constants.TOXIC:     0b10001000,
    "rest":              0b10000010, # This is Rest2 TODO check
}
# fmt: on

_STAT_ABBREV_TO_BOOST_PROP = {
    "atk": "atk",
    "def": "def",
    "spa": "spc",
    "spd": "non",  # Showdown sends boost msg for spa and spd so we just ignore spd
    "spe": "spe",
    "accuracy": "acc",
    "evasion": "eva",
}


@dataclass
class PSPlayer:
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


class PSBattle:
    def __init__(self, tag: str, p1: PSPlayer, p2: PSPlayer):
        self.tag = tag
        self.p1 = p1
        self.p2 = p2
        self.us: str | None = None  # "p1" if we are p1, p2 otherwise.
        # I think we are always p1 if we send a challenge and p2 if accepting. And random if ladder?

        self.rules: list[str] = []
        self.format: str = ""

        self.team: Team | None = None
        self.public = oak.Battle(bytes(384))
        self.durations = oak.Durations(bytes(8))

        self.request: dict | None = None
        self.msg_lines: list[str] = []

        self.started: bool = False
        self.rqid: int | None = None
        self.force_switch: bool = False
        self.wait: bool = False
        self.time_remaining: int | None = None

    def sides(self, split_msg) -> tuple[oak.Side, oak.Side]:
        """Return (our_pub, our_priv, opp_pub, opp_priv) sides."""
        if self._is_opponent(split_msg):
            return (
                self.public.side(1),
                self.public.side(0),
            )
        return (
            self.public.side(0),
            self.public.side(1),
        )

    def actives(self, split_msg):
        return (side.active for side in self.sides(split_msg))

    def volatiles(self, split_msg):
        return (side.active.volatiles() for side in self.sides(split_msg))

    def get_durations(self, split_msg):
        if self._is_opponent(split_msg):
            return (self.durations.get(1), self.durations.get(0))
        else:
            return (self.durations.get(0), self.durations.get(1))

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
        print("Message Lines:")
        for line in self.msg_lines:
            print(line)
        for line in self.msg_lines:
            split_msg = line.split("|")
            if len(split_msg) < 2:
                continue
            action = split_msg[1].strip()
            fn = self._HANDLERS.get(action)
            if fn:
                fn(self, split_msg)
                if action not in ("inactive",):
                    print(f"args: {split_msg}")
                    print(
                        f"|{action}|: \n{oak.battle_string(self.public, self.durations)}"
                    )
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
        # Not clear if we even need this anymore

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _is_opponent(self, split_msg) -> bool:
        if len(split_msg) < 3:
            assert False, "is_opp called with no thingy"
            return False
        return not split_msg[2].startswith(self.us)

    def _clear_boosts(self, side_idx: int):
        for battle in (self.public, self.private):
            b = battle.side(side_idx).active.boosts()
            b.atk = b.def_ = b.spe = b.spc = b.acc = b.eva = 0

    # -----------------------------------------------------------------------
    # Protocol handlers
    # -----------------------------------------------------------------------

    def switch_or_drag(self, split_msg: Msg) -> None:
        # TODO also permute sleep durations
        side, opp_side = self.sides(split_msg)
        duration, _ = self.get_durations(split_msg)
        details = split_msg[3] if len(split_msg) > 3 else ""  # Jynx
        condition = split_msg[4] if len(split_msg) > 4 else ""  # 100/100

        species_name, level = _parse_details(details)
        species: int = oak.id_to_species(species_name)
        assert 0 < species <= 151, f"Failed to parse species {species_name}"

        index: int | None = None
        for i in range(6):
            pokemon = side.pokemon(i)
            if pokemon.species == species:
                index = i
                break
        if index is None:
            for i in range(6):
                pokemon = side.pokemon(i)
                if pokemon.species == 0:
                    index = i
                    break
            assert index is not None, "New species but no empty slots"
            s = oak.Set()
            s.species = species
            s.level = 100  # TODO actually parse
            oak.complete_pokemon_from_set(side.pokemon(index), s)
            assert side.order[index] == 0, "Unexpected slot in order"

        # Update order
        order = list(side.order)
        order[index] = index + 1
        order[0], order[index] = order[index], order[0]
        side.order = order

        # Durations
        old_sleep = duration.sleep(0)
        duration.set_sleep(0, duration.sleep(index))
        duration.set_sleep(index, old_sleep)
        duration.confusion = 0
        duration.disable = 0
        duration.attacking = 0
        duration.binding = 0

        # use raw index instead of stored()
        oak.switch_in(side.pokemon(index), side.active)
        opp_side.active.volatiles.binding = False  # found in mechanics

    def faint(self, split_msg):
        side, _ = self.sides(split_msg)
        side.stored().hp = 0

    def heal_or_damage(self, split_msg):
        is_opp: bool = self._is_opponent(split_msg)
        side, opp_side = self.sides(split_msg)
        condition: str | None = split_msg[3] if len(split_msg) > 3 else None

        max_hp = side.stored().stats().hp
        hp_or_percent, max_hp_or_percent, status_str = _parse_condition(condition)
        hp = None
        if is_opp:
            # Opp is taking damage
            if max_hp_or_percent == 0:
                hp = 0
            else:
                hp = int(max_hp * hp_or_percent / max_hp_or_percent)
        else:
            hp = hp_or_percent
        side.stored().hp = hp

        # SLEEP = "slp"
        # BURN = "brn"
        # FROZEN = "frz"
        # PARALYZED = "par"
        # POISON = "psn"
        # TOXIC = "tox"
        # TOXIC_COUNT = "toxic_count"

        if status_str:
            if status_str == constants.PARALYZED:
                side.stored().status = _STATUS_BYTE[status_str]
            else:
                assert False, status_str
        # gen1: hitting self in confusion releases opponent binding
        # other_idx = 1 - side_idx
        # if len(split_msg) > 4 and "[from] confusion" in split_msg[-1]:
        #     self._set_vol(other_idx, binding=False)
        #     # self._bind_turns[other_idx] = 0

    def sethp(self, split_msg):
        assert False, "Unused?"
        self.heal_or_damage(split_msg)

    def fail(self, _split_msg):
        # Reasons: lkiss sleeping mon
        pass

    def move(self, split_msg):
        side, opp_side = self.sides(split_msg)
        dur, opp_dur = self.get_durations(split_msg)
        move_id: str | None = (
            normalize_name(split_msg[3]) if len(split_msg) > 3 else None
        )
        missed: bool = any(s.strip() == "[miss]" for s in split_msg)
        if move_id in BINDING_MOVES and not missed:
            side.active.volatiles().binding = True
        # moving means side is free from binding
        opp_side.active.volatiles().binding = False
        opp_dur.binding = 0

        # add move to pokemon/active
        if move_id and move_id != "struggle":
            s = oak.Set()
            move: int = oak.id_to_move(move_id)
            s.moves = [oak.id_to_move(move_id), 0, 0, 0]
            oak.complete_pokemon_moves(side.stored(), s)
            oak.complete_active_moves(side.active, s)
            # TODO
            # oak.decrement_pp(side.stored().moves, move)
            # oak.decrement_pp(side.active.moves, move)
            for i in range(4):
                ms: oak.MoveSlot = side.stored().move(i)
                if ms.id == move:
                    assert ms.pp > 0, "Used move with tracked pp=0"
                    ms.pp = max(0, ms.pp - 1)
            for i in range(4):
                ms: oak.MoveSlot = side.active.move(i)
                if ms.id == move:
                    assert ms.pp > 0, "Used move with tracked pp=0"
                    ms.pp = max(0, ms.pp - 1)

    def boost(self, split_msg):
        side, opp_side = self.sides(split_msg)
        stat: str | None = split_msg[3].strip() if len(split_msg) > 3 else None
        amount = int(split_msg[4].strip()) if len(split_msg) > 4 else 0
        assert amount != 0, "Why is boost amount 0???"
        prop = _STAT_ABBREV_TO_BOOST_PROP.get(stat)
        assert prop is not None, f"Could not parse stat for boost: {stat}"
        oak.boost(side, opp_side, prop, amount)

    def unboost(self, split_msg):
        side, opp_side = self.sides(split_msg)
        stat: str | None = split_msg[3].strip() if len(split_msg) > 3 else None
        amount = int(split_msg[4].strip()) if len(split_msg) > 4 else 0
        assert amount != 0, "Why is boost amount 0???"
        prop = _STAT_ABBREV_TO_BOOST_PROP.get(stat)
        assert prop is not None, f"Could not parse stat for boost: {stat}"
        oak.boost(side, opp_side, prop, -amount)

    def status(self, split_msg):
        side, _ = self.sides(split_msg)
        dur, _ = self.get_durations(split_msg)
        status_str = split_msg[3].strip() if len(split_msg) > 3 else ""
        from_str = split_msg[4].strip() if len(split_msg) > 4 else None
        byte: int = _STATUS_BYTE.get(status_str, None)
        assert byte is not None, "Bad status string lookup"
        print(f"Status from: {from_str}")
        if from_str == "Rest":
            side.stored().status = _STATUS_BYTE["rest"]
        else:
            side.stored().status = byte
        # dur.sleep()
        # TODO maybe init sleep duration to 1?

    def setboost(self, split_msg):
        assert False
        # side_idx = 1 if self._is_opponent(split_msg) else 0
        # stat = split_msg[3].strip() if len(split_msg) > 3 else ""
        # amount = int(split_msg[4].strip()) if len(split_msg) > 4 else 0
        # self._boost_set(side_idx, stat, amount)

    def clearnegativeboost(self, split_msg):
        side_idx = 1 if self._is_opponent(split_msg) else 0
        for battle in (self.public, self.private):
            b = battle.side(side_idx).active.boosts()
            for attr in ("atk", "def_", "spe", "spc", "acc", "eva"):
                if getattr(b, attr) < 0:
                    setattr(b, attr, 0)

    def clearboost(self, split_msg):
        assert False
        # side_idx = 1 if self._is_opponent(split_msg) else 0
        # self._clear_boosts(side_idx)

    def clearallboost(self, _split_msg):
        assert False
        # Haze zeros the boosts, copies stored stats into active, clears volatiles, and
        # self._clear_boosts(0)
        # self._clear_boosts(1)

    def curestatus(self, split_msg):
        assert False, "Start curestatus now pls"
        side_idx = 1 if self._is_opponent(split_msg) else 0
        # try to find the right storage slot from ident; fall back to active
        ident = split_msg[2] if len(split_msg) > 2 else ""
        slot = None
        # slot = (
        #     self._resolve_slot(side_idx, ident)
        #     if ident
        #     else self._active_slot[side_idx]
        # )
        # TODO replace _set_status

    def start_volatile_status(self, split_msg):
        vol, _ = self.volatiles(split_msg)
        s = normalize_name(split_msg[3].split(":")[-1]) if len(split_msg) > 3 else None
        assert s is not None

    def end_volatile_status(self, split_msg):
        vol, _ = self.volatiles(split_msg)
        s = normalize_name(split_msg[3].split(":")[-1]) if len(split_msg) > 3 else None
        assert s is not None

        if s == "mustrecharge":
            vol.recharging = False
        else:
            assert False, f"Bad volatile {s}"

    def mustrecharge(self, split_msg):
        vol, _ = self.volatiles(split_msg)
        vol.recharging = True

    def singleturn(self, _split_msg):
        assert False, "This is supposed to be protect only"

    def transform(self, split_msg):
        assert False, "Just take the dub bro"

    def activate(self, split_msg):
        side_idx = 1 if self._is_opponent(split_msg) else 0
        if len(split_msg) < 4:
            return
        effect = split_msg[3]
        if effect.lower().startswith("move: "):
            move_name = normalize_name(effect.split(":")[-1])
            if move_name in BINDING_MOVES:
                self._set_vol(side_idx, binding=True)

    def prepare(self, split_msg):
        side_idx = 1 if self._is_opponent(split_msg) else 0
        move_name = normalize_name(split_msg[3]) if len(split_msg) > 3 else ""
        if move_name in ("solarbeam", "skyattack", "razorwind"):
            self._set_vol(side_idx, charging=True)
        else:
            assert False, f"Prepare unexpected move: {move_name}"

    def cant(self, split_msg):
        side, opp_side = self.sides(split_msg)
        dur, _ = self.get_durations(split_msg)
        if len(split_msg) < 4:
            return
        reason = split_msg[3].strip()

        if reason == "recharge":
            side.active.volatiles().recharging = False
        elif reason == constants.PARALYZED:
            # gen1: full paralysis releases partial trap on other side
            side.active.volatiles().binding = False
            dur.binding = 0
        elif reason == constants.SLEEP:
            dur.set_sleep(0, dur.sleep(0) + 1)
        elif reason == "partiallytrapped":
            opp_side.active.volatiles().binding = True
            # TODO set duration
        else:
            assert False, f"Unsupported reason for cant {reason}"

    def upkeep(self, _split_msg):
        pass

    def turn(self, split_msg):
        if len(split_msg) > 2:
            self.public.turn = int(split_msg[2])

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
        "-sethp": sethp,  # TODO just proxy for heal_or_damage
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


def _parse_condition(condition: str) -> tuple[int, int, str | None]:
    """Return (hp, max_hp, status_str) from a condition string like '100/200 brn' or 'fnt'."""
    if not condition or constants.FNT in condition:
        return (0, 0, None)
    parts = condition.split("/")
    try:
        hp = int(parts[0])
    except ValueError:
        return (0, 0, None)
    max_hp = 0
    status_str = None
    if len(parts) > 1:
        rhs = parts[1].split()
        # try:
        max_hp = int(rhs[0])
        # except (ValueError, IndexError):
        #     pass
        if len(rhs) > 1:
            status_str = rhs[1]
    return (hp, max_hp, status_str)
