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
    FIGHT = "fight"
    CHARGING_MOVES = ("skullbash", "solarbeam", "skyattack", "razorwind")
    INVULN_MOVES = ("fly", "dig")
    THRASHING_MOVES = ("thrash", "petaldance")


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


class SideAux:
    @dataclass
    class Stats:
        atk: int = 1
        def_: int = 1
        spe: int = 1
        spc: int = 1

        def __init__(self):
            pass

        def read(self, src: oak.Stats):
            self.atk = src.atk
            self.def_ = src.def_
            self.spe = src.spe
            self.spc = src.spc

        def write(self, dest: oak.Stats):
            dest.atk = self.atk
            dest.def_ = self.def_
            dest.spe = self.spe
            dest.spc = self.spc

    def __init__(self):
        self.stats = SideAux.Stats()


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
        self.aux: list[SideAux, SideAux] = [SideAux() for i in range(2)]
        self.msg_index: int = 0

        self.request: dict | None = None
        self.msg_lines: list[str] = []

        self.started: bool = False
        self.rqid: int | None = None
        self.force_switch: bool = False
        self.wait: bool = False
        self.time_remaining: int | None = None

    def store_stats(self):
        for i in range(2):
            self.aux[i].stats.read(self.public.side(i).active.stats())

    def unstore_stats(
        self,
    ):
        for i in range(2):
            self.aux[i].stats.write(self.public.side(i).active.stats())

    def is_us(self, msg: list[str]) -> bool:
        return msg[2].startswith(self.us)

    def sides(self, is_us: bool) -> tuple[oak.Side, oak.Side]:
        return tuple(self.public.side(i) for i in range(2))[:: (-1) ** (not is_us)]

    def actives(self, is_us: bool):
        return tuple(self.public.side(i).active for i in range(2))[
            :: (-1) ** (not is_us)
        ]

    def volatiles(self, is_us: bool):
        return tuple(self.public.side(i).active.volatiles() for i in range(2))[
            :: (-1) ** (not is_us)
        ]

    def get_durations(self, is_us: bool):
        return tuple(self.durations.get(i) for i in range(2))[:: (-1) ** (not is_us)]

    def sleep(self, side: oak.Side, duration: oak.Duration):
        side.stored().status = _STATUS_BYTE["slp"]
        duration.set_sleep(0, 1)
        side.active.volatiles().recharging = False

    def rest(self, side: oak.Side, duration: oak.Duration):
        side.stored().status = _STATUS_BYTE["rest"]
        duration.set_sleep(0, 1)

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
        is_us = self.is_us(split_msg)
        side, opp_side = self.sides(is_us)
        duration, opp_duration = self.get_durations(is_us)

        details = split_msg[3] if len(split_msg) > 3 else ""  # Jynx
        condition = split_msg[4] if len(split_msg) > 4 else ""  # 100/100

        species_name, level = _parse_details(details)
        species: int = oak.id_to_species(normalize_name(species_name))
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
        self.store_stats()
        # Durations
        old_sleep = duration.sleep(0)
        # clear volatile durations
        duration.set_sleep(0, duration.sleep(slot - 1))
        duration.set_sleep(slot - 1, old_sleep)
        duration.confusion = 0
        duration.disable = 0
        duration.attacking = 0
        duration.binding = 0

        opp_side.active.volatiles().binding = False  # found in mechanics

        if side.stored().status == _STATUS_BYTE[constants.TOXIC]:
            side.stored().status = _STATUS_BYTE[constants.POISON]

        opp_side.active.volatiles().binding = False

    def faint(self, split_msg):
        side, _ = self.sides(self.is_us(split_msg))
        side.stored().hp = 0

    def heal_or_damage(self, split_msg):
        is_us: bool = self.is_us(split_msg)
        side, opp_side = self.sides(is_us)
        duration, _ = self.get_durations(is_us)
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
                constants.PARALYZED,
                constants.SLEEP,
                constants.FROZEN,
                constants.BURN,
                constants.TOXIC,
                constants.POISON,
            ):
                pass
            else:
                assert False, status_str

    def sethp(self, split_msg):
        assert False, "sethp assumed impossile"

    def fail(self, split_msg):
        is_us = self.is_us(split_msg)
        side, _ = self.sides(is_us)
        if self.msg_index > 0:
            prev_split_msg = self.msg_lines[self.msg_index - 1].split("|")
            if len(prev_split_msg) > 1 and prev_split_msg[1] in ("-boost", "-unboost"):
                self.unstore_stats()

    def move(self, split_msg):
        is_us = self.is_us(split_msg)
        side, opp_side = self.sides(is_us)
        self.before_move(side)
        vol = side.active.volatiles()
        dur, opp_dur = self.get_durations(is_us)
        move_id: str | None = (
            normalize_name(split_msg[3]) if len(split_msg) > 3 else None
        )
        missed: bool = any(s.strip() == "[miss]" for s in split_msg)
        # moving means side is free from binding
        opp_side.active.volatiles().binding = False
        opp_dur.binding = 0

        from_metrome = len(split_msg) > 5 and split_msg[5] == "[from] Metronome"
        from_mirror_move = len(split_msg) > 5 and split_msg[5] == "[from] MirrorMove"

        charging_move = move_id in constants.CHARGING_MOVES
        thrashing_move = move_id in constants.THRASHING_MOVES
        pp_deduction = (
            0
            if (move_id == "rage" and vol.rage)
            or (charging_move and not vol.charging)
            or (thrashing_move and vol.thrashing)
            else 1
        )

        mimic_move, mimic_move_index = None, None
        mimic_ = oak.id_to_move("mimic")
        for i in range(4):
            if side.active.move(i).id != mimic_ and side.stored().move(i).id == mimic_:
                mimic_move: int = side.active.move(i).id
                mimic_move_index = i
                break
        from_mimic = not mimic_move is None and (oak.id_to_move(move_id) == mimic_move)
        if from_mimic:
            ms = side.active.move(mimic_move_index)
            ms.pp = max(0, ms.pp - pp_deduction)
            ms = side.stored().move(mimic_move_index)
            ms.pp = max(0, ms.pp - pp_deduction)

        # add move to pokemon/active
        if (
            move_id
            and move_id != "struggle"
            and not from_metrome
            and not from_mimic
            and not from_mirror_move
        ):
            # idiom to add single move while while keeping existing moves the same
            s = oak.Set()
            move: int = oak.id_to_move(move_id)
            s.moves = [oak.id_to_move(move_id), 0, 0, 0]

            oak.complete_pokemon_moves(side.stored(), s)
            oak.complete_active_moves(side.active, s)

            for i in range(4):
                ms: oak.MoveSlot = side.stored().move(i)
                if ms.id == move:
                    assert ms.pp > 0, "Used move with tracked pp=0"
                    ms.pp = max(0, ms.pp - pp_deduction)
            for i in range(4):
                ms: oak.MoveSlot = side.active.move(i)
                if ms.id == move:
                    assert ms.pp > 0, "Used move with tracked pp=0"
                    ms.pp = max(0, ms.pp - pp_deduction)

        if missed:
            return

        if move_id in BINDING_MOVES:
            if vol.binding:
                dur.binding = dur.binding + 1
            else:
                side.active.volatiles().binding = True
                dur.binding = 1

        if move_id == "bide":
            if vol.bide:
                dur.attacking = dur.attacking + 1
            else:
                vol.bide = True

        if move_id == "rage":
            vol.rage = True

        if move_id in constants.CHARGING_MOVES:
            vol.charging = not vol.charging

        if move_id in constants.THRASHING_MOVES:

            if vol.thrashing:
                dur.attacking = dur.attacking + 1
            else:
                vol.thrashing = True
                dur.attacking = 1

    def boost(self, split_msg):
        is_us = self.is_us(split_msg)
        side, opp_side = self.sides(is_us)
        stat: str | None = split_msg[3].strip() if len(split_msg) > 3 else None
        prop = _STAT_ABBREV_TO_BOOST_PROPERTY.get(stat)
        if split_msg[4].strip() == "[from] Rage":
            prop += "|[from] Rage"
            amount = int(split_msg[5].strip())
            assert amount == 1
        else:
            amount = int(split_msg[4].strip()) if len(split_msg) > 4 else 0
        assert amount != 0, "Why is boost amount 0???"

        assert prop is not None, f"Could not parse stat for boost: {stat}"
        oak.boost(side, opp_side, prop, amount)

    def unboost(self, split_msg):
        is_us = self.is_us(split_msg)
        side, opp_side = self.sides(is_us)
        stat: str | None = split_msg[3].strip() if len(split_msg) > 3 else None
        amount = int(split_msg[4].strip()) if len(split_msg) > 4 else 0
        assert amount != 0
        prop = _STAT_ABBREV_TO_BOOST_PROPERTY.get(stat)
        assert prop is not None, f"Could not parse stat for boost: {stat}"
        oak.boost(side, side, prop, -amount)

    def _status(self, split_msg):
        is_us = self.is_us(split_msg)
        side, _ = self.sides(is_us)
        dur, _ = self.get_durations(is_us)
        status_str = split_msg[3].strip() if len(split_msg) > 3 else ""
        from_str = normalize_name(split_msg[5]) if len(split_msg) > 5 else None
        byte: int = _STATUS_BYTE.get(status_str, None)
        assert byte is not None, f"Bad status string lookup: {status_str}"
        # if side.stored().status == 0:
        if from_str == "rest":
            self.rest(side, dur)
        else:
            if status_str == constants.SLEEP:
                self.sleep(side, dur)
            elif status_str == constants.TOXIC:
                vol = side.active.volatiles()
                vol.toxic = True
            else:
                side.stored().status = byte
                pass

        # TODO maybe init sleep duration to 1?
        oak.status_modify(side.stored().status, side.active.stats())
        self.store_stats()

    def setboost(self, split_msg):
        assert False, "setboost assumed impossile"

    def clearnegativeboost(self, split_msg):
        assert False, "clearnegativeboost assumed impossile"

    def clearboost(self, split_msg):
        assert False, "clearboost assumed impossile"

    def clearallboost(self, _split_msg):
        for i in range(2):
            active = self.public.side(i).active
            boosts = active.boosts()
            boosts.atk = 0
            boosts.def_ = 0
            boosts.spe = 0
            boosts.spc = 0
            boosts.acc = 0
            boosts.eva = 0

    def curestatus(self, split_msg):
        is_us = self.is_us(split_msg)
        side, _ = self.sides(is_us)
        dur, _ = self.get_durations(is_us)
        side.stored().status = 0
        dur.set_sleep(0, 0)

    def _singlemove(
        self,
    ):
        assert False, "singlemove assumed impossible"

    def _start(self, split_msg):
        is_us = self.is_us(split_msg)
        active, _ = self.actives(is_us)
        vol, _ = self.volatiles(is_us)
        s = normalize_name(split_msg[3].split(":")[-1]) if len(split_msg) > 3 else None
        dur, _ = self.get_durations(is_us)
        assert s is not None
        if s == "substitute":
            vol.substitute = True
            vol.substitute_hp = int(active.stats().hp / 4) or 1
        elif s == "reflect":
            vol.reflect = True
        elif s == "lightscreen":
            vol.light_screen = True
        elif s == "bide":
            vol.bide = True
            dur.attacking = 1
        elif s == "disable":
            # vol.disable = True
            dur.disable = 1
            vol.disable_left = 1
        elif s == "focusenergy":
            vol.focus_energy = True
        elif s == "mimic":
            move = oak.id_to_move(normalize_name(split_msg[4]))
            side, _ = self.sides(is_us)
            stored = side.stored()
            found = False
            for i in range(4):
                if active.move(i).id == oak.id_to_move("mimic"):
                    active.move(i).id = move
                    # active.move(i).pp = 5
                    found = True
                    break
            if not found:
                assert False
        elif s == "leechseed":
            vol.leech_seed = True
        elif s == "confusion":
            if vol.thrashing:
                vol.thrashing = False
                vol.attacks = 0
                dur.attacking = 0
            vol.confusion = True
            vol.confusion_left = 1
            dur.confusion = 1
        elif s == "mist":
            vol.mist = True
        elif s == "typechange":
            active.types = int(split_msg[4])
        else:
            assert False, f"Bad volatile {s}"

    def _end(self, split_msg):
        is_us = self.is_us(split_msg)
        vol, _ = self.volatiles(is_us)
        dur, _ = self.get_durations(is_us)
        s = normalize_name(split_msg[3].split(":")[-1]) if len(split_msg) > 3 else None
        assert s is not None

        if s == "mustrecharge":
            vol.recharging = False
        elif s == "substitute":
            vol.substitute = False
            vol.substitute_hp = 0
        elif s == "disable":
            # vol.disable = False
            vol.disable_left = 0
            vol.disable_move = 0
            dur.disable = 0
        elif s == "bide":
            vol.bide = False
            dur.attacking = 0
        elif s == "confusion":
            vol.confusion = False
            vol.confusion_left = 0
            dur.confusion = 0
        elif s == "mist":
            vol.mist = False
        else:
            print(split_msg)
            assert False, f"Bad volatile {s}"

    def mustrecharge(self, split_msg):
        is_us = self.is_us(split_msg)
        vol, _ = self.volatiles(is_us)
        vol.recharging = True

    def singleturn(self, _split_msg):
        assert False, "singleturn assumed impossible"

    def transform(self, split_msg):
        assert False, "transform not impl"

    def activate(self, split_msg):
        is_us = self.is_us(split_msg)
        active, _ = self.actives(is_us)
        vol, opp_vol = self.volatiles(is_us)
        dur, opp_dur = self.get_durations(is_us)
        s = normalize_name(split_msg[3].split(":")[-1]) if len(split_msg) > 3 else None
        assert s is not None
        if s == "substitute":
            vol.substitute = True
            vol.substitute_hp = int(active.stats().hp / 4) or 1
        elif s == "confusion":
            dur.confusion = dur.confusion + 1
        elif s == "bide":
            dur.attacking = dur.attacking + 1
        elif s == "":
            assert split_msg[-1] == "move: Splash"
        elif s == "haze":
            oak.clear_volatiles(vol, dur)
            oak.clear_volatiles(opp_vol, opp_dur)
        elif s == "mist":
            vol.mist = True
        else:
            assert False, f"Activate idk: {s}"

    def prepare(self, split_msg):
        is_us = self.is_us(split_msg)
        side, _ = self.sides(is_us)
        move_name = normalize_name(split_msg[3]) if len(split_msg) > 3 else ""
        if move_name in constants.CHARGING_MOVES:
            side.active.volatiles().charging = True
        elif move_name in constants.INVULN_MOVES:
            side.active.volatiles().invulnerable = True
        else:
            assert False, f"Prepare unexpected move: {move_name}"

    def before_move(self, side: oak.Side):
        # upkeep like incrementing confusion
        vol = side.active.volatiles()
        if vol.toxic:
            vol.toxic_counter = vol.toxic_counter + 1
        if vol.charging:
            vol.charging = False
        self.store_stats()

    def cant(self, split_msg):
        is_us = self.is_us(split_msg)
        side, opp_side = self.sides(is_us)
        vol, opp_vol = self.volatiles(is_us)
        dur, opp_dur = self.get_durations(is_us)

        self.before_move(side)

        # clear charging
        vol.charging = False
        # clear bide
        vol.bide = False
        dur.attacking = 0

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

            def is_self(status):
                return bool(status & 128)

            if is_self(side.stored().status):
                side.stored().status = side.stored().status - 1
        elif reason == "partiallytrapped":
            # opp_side.active.volatiles().binding = True
            # TODO set duration
            if opp_side.active.volatilesKT().binding:
                opp_dur.binding = opp_dur.binding + 1
            pass
        elif reason == "flinch":
            # This happens silently too so we just ignore flinch vol TODO
            vol.recharging = False
        elif reason == "Disable":
            pass
        else:
            assert False, f"Unsupported reason for cant {reason}"

    def upkeep(self, _split_msg):
        pass

    def turn(self, split_msg):
        self.public.turn = int(split_msg[2])
        for side in range(2):
            dur = self.durations.get(side)
            vol = self.public.side(side).active.volatiles()
            if vol.disable_left:
                dur.disable = dur.disable + 1
            # if vol.bide:
            #     dur.attacking = dur.attacking + 1

    def noinit(self, split_msg):
        # TODO wtf is this
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

    def _immune(self, _split_msg):
        pass

    def anim(self, _split_msg):
        assert False, "anim assumed impossible"

    def update(self, msg: str):
        self.msg_index = 0
        self.store_stats()
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
            self.msg_index += 1
        self.msg_lines.clear()

    _HANDLERS = {
        "move": move,
        "switch": switch_or_drag,
        "drag": switch_or_drag,
        "faint": faint,
        "turn": turn,
        "-fail": fail,
        "-heal": heal_or_damage,
        "-damage": heal_or_damage,
        "-sethp": sethp,
        "-setboost": setboost,
        "-boost": boost,
        "-unboost": unboost,
        "-clearnegativeboost": clearnegativeboost,
        "-clearboost": clearboost,
        "-clearallboost": clearallboost,
        "-status": _status,
        "-curestatus": curestatus,
        "-activate": activate,
        "-anim": anim,
        "-prepare": prepare,
        "-start": _start,
        "-singlemove": _singlemove,
        "-end": _end,
        "-immune": _immune,
        "-transform": transform,
        "-clearnegativeboost": clearnegativeboost,
        "-singleturn": singleturn,
        "-mustrecharge": mustrecharge,
        "upkeep": upkeep,
        "cant": cant,
        "inactive": inactive,
        "inactiveoff": inactiveoff,
        "noinit": noinit,
    }
