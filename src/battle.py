from __future__ import annotations

import re
import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

import oak

type Msg = list[str]


class constants:
    RQID = "rqid"
    FORCE_SWITCH = "forceSwitch"
    WAIT = "wait"
    STATUS = "status"
    FNT = "fnt"
    TIME_LEFT = "Time left:"
    IDENT = "ident"
    ACTIVE = "active"
    VOLATILE_STATUS = "volatileStatus"
    LOCKED_MOVE = "lockedmove"
    REFLECT = "reflect"
    LIGHT_SCREEN = "lightscreen"
    MIST = "mist"
    CONFUSION = "confusion"
    LEECH_SEED = "leechseed"
    SUBSTITUTE = "substitute"
    TRANSFORM = "transform"
    PARTIALLY_TRAPPED = "partiallytrapped"
    # non-volatile statuses
    SLEEP = "slp"
    BURN = "brn"
    FROZEN = "frz"
    PARALYZED = "par"
    POISON = "psn"
    TOXIC = "tox"
    TOXIC_COUNT = "toxic_count"
    FIGHT = "fight"


BINDING_MOVES = {"bind", "clamp", "firespin", "wrap"}

# fmt: off
_STATUS_BYTE = {
    constants.SLEEP:     0b00000001, # slp1
    constants.BURN:      0b00010000,
    constants.FROZEN:    0b00100000,
    constants.PARALYZED: 0b01000000,
    constants.POISON:    0b00001000,
    constants.TOXIC:     0b10001000,
    "rest":              0b10000010, # This is Rest2 TODO check if correct value
}
# fmt: on

_STAT_ABBREV_TO_BOOST_PROPERTY = {
    "atk": "atk",
    "def": "def",
    "spa": "spc",
    "spd": "non",  # Showdown sends boost msg for spa and spd so we just ignore spd
    "spe": "spe",
    "accuracy": "acc",
    "evasion": "eva",
}


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


def _parse_details(details: str) -> tuple[int, int]:
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
        max_hp = int(rhs[0])
        if len(rhs) > 1:
            status_str = rhs[1]
    return (hp, max_hp, status_str)


@dataclass
class PSPlayer:
    user: str = ""
    avatar: int | None = None
    rating: int | None = None
    pokemon: int = 6


class PSBattle:
    def __init__(self, tag: str, p1: PSPlayer, p2: PSPlayer):
        self.tag = tag
        self.p1 = p1
        self.p2 = p2
        self.us: str | None = None

        self.rules: list[str] = []

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

    def is_us(self, msg: list[str]) -> bool:
        return msg[2].startswith(self.us)

    def sides(self, split_msg) -> tuple[oak.Side, oak.Side]:
        return (
            (self.public.side(0), self.public.side(1))
            if self.is_us(split_msg)
            else (self.public.side(1), self.public.side(0))
        )

    def actives(self, split_msg):
        return (
            (self.public.side(0).active, self.public.side(1).active)
            if self.is_us(split_msg)
            else (self.public.side(1).active, self.public.side(0).active)
        )

    def volatiles(self, split_msg):
        return (
            (
                self.public.side(0).active.volatiles(),
                self.public.side(1).active.volatiles(),
            )
            if self.is_us(split_msg)
            else (
                self.public.side(1).active.volatiles(),
                self.public.side(0).active.volatiles(),
            )
        )

    def get_durations(self, split_msg):
        return (
            (self.durations.get(0), self.durations.get(1))
            if self.is_us(split_msg)
            else (self.durations.get(1), self.durations.get(0))
        )

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
                fn(self, split_msg)
                if action not in ("inactive",):
                    print(f"args: {split_msg}")
                    print(
                        f"|{action}|: \n{oak.battle_string(self.public, self.durations)}"
                    )
                    print(self.public.side(0).order, self.public.side(1).order)
        self.msg_lines.clear()

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
        # TODO we may need this for action parsing

    # -----------------------------------------------------------------------
    # Protocol handlers
    # -----------------------------------------------------------------------

    def switch_or_drag(self, split_msg: Msg) -> None:
        side, opp_side = self.sides(split_msg)
        duration, opp_duration = self.get_durations(split_msg)
        details = split_msg[3] if len(split_msg) > 3 else ""  # Jynx
        condition = split_msg[4] if len(split_msg) > 4 else ""  # 100/100

        species_name, level = _parse_details(details)
        species: int = oak.id_to_species(species_name)
        assert 0 < species <= 151, f"Failed to parse species {species_name}"

        # add to pokemon/order if necessary, then return maintained slot of the incoming
        order = list(side.order)
        index: int | None = None
        slot: int = 0
        # use the information to reveal prior to the switch being executed
        for s in range(1, 7):
            i = order[s - 1]
            if i:
                # we are looking through the revealed slots
                pokemon = side.pokemon(i - 1)
                if pokemon.species == species:
                    slot = s
                    index = i - 1
                    break
            else:
                # we have hit the end of revealed without finding the species, add it
                slot = s
                index = slot - 1
                order[index] = slot

                s = oak.Set()
                s.species = species
                s.level = 100  # TODO actually parse
                oak.complete_pokemon_from_set(side.pokemon(index), s)
                break
        assert index is not None, "Failed to find incoming or an empty slot for it"

        # Update order
        order[0], order[slot - 1] = order[slot - 1], order[0]
        side.order = order

        # last_ stuff
        # side.last_move_index = 1
        # side.last_used_move = None
        # opp_side.last_used_move = None

        # Clears active, then sets species, moves, types, stats
        oak.switch_in(side.stored(), side.active)
        oak.status_modify(side.stored().status, side.active.stats())
        # Durations
        old_sleep = duration.sleep(0)
        duration.set_sleep(0, duration.sleep(slot - 1))
        duration.set_sleep(slot - 1, old_sleep)
        duration.confusion = 0
        duration.disable = 0
        duration.attacking = 0
        duration.binding = 0
        opp_side.active.volatiles().binding = False  # found in mechanics
        # opp_duration.binding = 0 # not present in mechanics actually
        if side.stored().status == _STATUS_BYTE[constants.TOXIC]:
            side.stored().status = _STATUS_BYTE[constants.POISON]

    def faint(self, split_msg):
        side, _ = self.sides(split_msg)
        side.stored().hp = 0

    def heal_or_damage(self, split_msg):
        is_us: bool = self.is_us(split_msg)
        side, opp_side = self.sides(split_msg)
        duration, _ = self.get_durations(split_msg)
        condition: str | None = split_msg[3] if len(split_msg) > 3 else None

        max_hp = side.stored().stats().hp
        hp_or_percent, max_hp_or_percent, status_str = _parse_condition(condition)
        hp = None
        if is_us:
            # showdown gives us exact hp
            hp = hp_or_percent
        else:
            # and percentage 100,100 for opp live mons or 0,0 for fainted
            if max_hp_or_percent == 0:
                assert hp_or_percent == 0, "hp is not 0 while max_hp is 0"
                hp = 0
            else:
                hp = int(max_hp * hp_or_percent / max_hp_or_percent)

        side.stored().hp = hp

        if status_str:
            if status_str in (
                constants.PARALYZED.constants.SLEEP,
                constants.FROZEN,
                constants.BURN,
            ):
                side.stored().status = _STATUS_BYTE[status_str]
                if status_str == constants.SLEEP:
                    duration.set_sleep(0, 1)
            else:
                assert False, status_str

        # gen1: hitting self in confusion releases opponent binding
        # other_idx = 1 - side_idx
        # if len(split_msg) > 4 and "[from] confusion" in split_msg[-1]:
        #     self._set_vol(other_idx, binding=False)
        #     # self._bind_turns[other_idx] = 0

    def sethp(self, split_msg):
        assert False, "sethp assumed impossile"

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
        prop = _STAT_ABBREV_TO_BOOST_PROPERTY.get(stat)
        assert prop is not None, f"Could not parse stat for boost: {stat}"
        oak.boost(side, opp_side, prop, amount)

    def unboost(self, split_msg):
        side, opp_side = self.sides(split_msg)
        stat: str | None = split_msg[3].strip() if len(split_msg) > 3 else None
        amount = int(split_msg[4].strip()) if len(split_msg) > 4 else 0
        assert amount != 0
        prop = _STAT_ABBREV_TO_BOOST_PROPERTY.get(stat)
        assert prop is not None, f"Could not parse stat for boost: {stat}"
        oak.boost(side, opp_side, prop, -amount)

    def status(self, split_msg):
        side, _ = self.sides(split_msg)
        dur, _ = self.get_durations(split_msg)
        status_str = split_msg[3].strip() if len(split_msg) > 3 else ""
        from_str = split_msg[4].strip() if len(split_msg) > 4 else None
        byte: int = _STATUS_BYTE.get(status_str, None)
        assert byte is not None, f"Bad status string lookup: {status_str}"
        if from_str == "Rest":
            side.stored().status = _STATUS_BYTE["rest"]
        else:
            side.stored().status = byte
        # dur.sleep()
        # TODO maybe init sleep duration to 1?

    def setboost(self, split_msg):
        assert False, "setboost assumed impossile"

    def clearnegativeboost(self, split_msg):
        assert False, "clearnegativeboost assumed impossile"

    def clearboost(self, split_msg):
        assert False, "clearboost assumed impossile"

    def clearallboost(self, _split_msg):
        assert False, "clearallboost not impl"
        # Haze zeros the boosts, copies stored stats into active, clears volatiles, and
        # self._clear_boosts(0)
        # self._clear_boosts(1)

    def curestatus(self, split_msg):
        side, _ = self.sides(split_msg)
        side.stored().status = 0
        if len(split_msg) >= 4:
            if split_msg == constants.SLEEP:
                dur, _ = self.get_durations(split_msg)
                dur.set_sleep(0, 0)

    def start_volatile_status(self, split_msg):
        active, _ = self.actives(split_msg)
        vol, _ = self.volatiles(split_msg)
        s = normalize_name(split_msg[3].split(":")[-1]) if len(split_msg) > 3 else None
        assert s is not None
        if s == "substitute":
            vol.substitute = True
            vol.substitute_hp = int(active.stats().hp / 4) or 1
        elif s == "reflect":
            vol.reflect = True
        elif s == "lightscreen":
            vol.light_screen = True
        else:
            assert False, f"Bad volatile {s}"

    def end_volatile_status(self, split_msg):
        vol, _ = self.volatiles(split_msg)
        s = normalize_name(split_msg[3].split(":")[-1]) if len(split_msg) > 3 else None
        assert s is not None

        if s == "mustrecharge":
            vol.recharging = False
        elif s == "substitute":
            vol.substitute = False
            vol.substitute_hp = 0
        else:
            assert False, f"Bad volatile {s}"

    def mustrecharge(self, split_msg):
        vol, _ = self.volatiles(split_msg)
        vol.recharging = True

    def singleturn(self, _split_msg):
        assert False, "singleturn assumed impossible"

    def transform(self, split_msg):
        assert False, "transform not impl"

    def activate(self, split_msg):
        assert False, "TODO"

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
        elif reason == constants.FROZEN:
            pass
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
        assert False, "anim assume impossible"

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
