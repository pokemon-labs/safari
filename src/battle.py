from __future__ import annotations
from enum import Enum, auto

import re
import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

import oak

type Msg = list[str]

from src.mechanics import (
    BOOSTS,
    Constants,
    _STAT_ABBREV_TO_BOOST_PROPERTY,
    _STATUS_BYTE,
    Mechanics,
    CantReason,
    ActivateReason,
    FailReason,
    BeforeMove,
)


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
    if not condition or Constants.FNT in condition:
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

    class Truth:
        def __init__(
            self,
        ):
            self.battle = oak.Battle()
            self.durations = oak.Durations()

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

        # This is used for testing only
        self.truth: Truth | None = None

        self.request: dict | None = None
        self.msg_lines: list[str] = []
        # Snapshot of msg_lines taken right before each clear — i.e. the
        # protocol log that led up to the most recent decision point.
        self.last_log: list[str] = []

        self.started: bool = False
        self.rqid: int | None = None
        self.force_switch: bool = False
        self.wait: bool = False
        self.time_remaining: int | None = None

    def init_truth(
        self,
    ):
        self.truth = self.Truth()

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

    def parse_request(self, split_msg: list[str]):
        if len(split_msg) < 3:
            return
        raw = split_msg[2].strip("'")
        if not raw:
            return
        req: dict = json.loads(raw)
        self.request = req
        self.rqid = req.get(Constants.RQID)
        self.force_switch = bool(req.get(Constants.FORCE_SWITCH))
        self.wait = bool(req.get(Constants.WAIT))
        self._apply_request()

        # check if fight button is only option
        self.fight_button = False
        if req.get("active"):
            moves = req['active'][0]['moves']
            if len(moves) == 1 and moves[0]['id'] == 'fight':
                self.fight_button = True
        



    def _apply_request(self):
        """Write request JSON into self.private (and sync into self.public side 0)."""
        if not self.request:
            return
        side_data = self.request.get("side", {})
        pokemon_list = side_data.get("pokemon", [])
        # TODO we may need this for action parsing

    def _switch(self, split_msg: Msg) -> None:
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
                s.level = level
                oak.complete_pokemon_from_set(side.pokemon(index), s)
                break
        assert index is not None, "Failed to find incoming or an empty slot for it"

        # Update order
        order[0], order[slot - 1] = order[slot - 1], order[0]
        side.order = order

        # last_ stuff
        self.public.last_move(0 if is_us else 1).index = 1
        side.last_used_move = 0
        opp_side.last_used_move = 0

        # Clears active, then sets species, moves, types, stats
        oak.switch_in(side.stored(), side.active)
        side.active.species = side.stored().species
        Mechanics.status_modify(side.stored().status, side.active.stats())
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

        if side.stored().status == _STATUS_BYTE[Constants.TOXIC]:
            side.stored().status = _STATUS_BYTE[Constants.POISON]

        opp_side.active.volatiles().binding = False

    def faint(self, split_msg):
        is_us = self.is_us(split_msg)
        Mechanics.faint(self.public, self.durations, 0 if is_us else 1)

    def _heal(self, split_msg):
        self.heal_or_damage(split_msg, False)

    def _damage(self, split_msg):
        self.heal_or_damage(split_msg, True)
        # TODO new, ad hoc, to fix 5 hit double slap vs rage
        self.store_stats()

    def prev_split_msg(self, offset: int = 1):
        return self.msg_lines[self.msg_index - offset].split("|")

    def heal_or_damage(self, split_msg, damage=True):
        is_us: bool = self.is_us(split_msg)
        side, opp_side = self.sides(is_us)
        vol, _ = self.volatiles(is_us)
        duration, _ = self.get_durations(is_us)
        condition: str | None = split_msg[3] if len(split_msg) > 3 else None

        max_hp = side.stored().stats().hp
        hp_or_percent, max_hp_or_percent, status_str = _parse_condition(condition)
        hp = None
        if is_us:
            # showdown gives us exact hp
            hp = hp_or_percent
        else:
            # and `percentage, 100` for opp live mons or `0, 0` for fainted
            if max_hp_or_percent == 0:
                assert hp_or_percent == 0, "hp is not 0 while max_hp is 0"
                hp = 0
            else:
                hp = int(max_hp * hp_or_percent / max_hp_or_percent)

        from_confusion = len(split_msg) > 4 and split_msg[4] == "confusion"
        from_sub = False
        bind_into_ghost = False
        if self.msg_index > 0:
            prev = self.prev_split_msg()
            if len(prev) > 3:
                if prev[1] == "-start" and prev[3] == "Substitute":
                    from_sub = True
                if prev[1] == "move":
                    prev_move_id = normalize_name(prev[3])
                    prev_move = oak.id_to_move(prev_move_id)
                    move_data = oak.move_data(prev_move)
                    types = side.active.types
                    t1 = types & 15
                    t2 = types >> 4
                    effectiveness = oak.get_effectiveness(
                        move_data["type"], t1
                    ) * oak.get_effectiveness(move_data["type"], t2)

                    if effectiveness == 0 and prev_move_id in Constants.BINDING_MOVES:
                        bind_into_ghost = True
        damage_counts = (
            len(split_msg) < 5 or split_msg[4] in ("confusion",)
        ) and not from_sub

        if damage and damage_counts and not bind_into_ghost:
            dmg = 0
            if from_confusion:
                if hp == 0:
                    opp_def_temp = opp_side.active.stats().def_
                    opp_side.active.stats().def_ = side.active.stats().def_
                    dmg = Mechanics.calc_damage(
                        self.public,
                        side,
                        opp_side,
                        None,
                        crit=False,
                        adjust=False,
                        roll=255,
                    )
                    opp_side.active.stats().def_ = opp_def_temp
                    self.public.last_damage = dmg
                else:
                    dmg = side.stored().hp - hp
                    assert dmg >= 0
                    self.public.last_damage = max(1, dmg)
            else:
                dmg = side.stored().hp - hp
                assert dmg >= 0
                self.public.last_damage = max(1, dmg)

                if oak.move_data(opp_side.last_used_move)["effect"] in (
                    32,
                    33,
                ):  # DrainHP/DreamEater
                    self.public.last_damage = max(1, self.public.last_damage // 2)

        side.stored().hp = hp

        if len(split_msg) > 4:
            reason = normalize_name(split_msg[4])
            if reason == "confusion":
                Mechanics.before_move(
                    self.public, side, duration, reason=ActivateReason.confusion
                )
                self.store_stats()
            elif reason == "brn":
                if vol.toxic:
                    vol.toxic_counter += 1
            elif reason == "psn":
                if vol.toxic:
                    vol.toxic_counter += 1
            elif reason == "leechseed":
                if vol.toxic:
                    vol.toxic_counter += 1
            elif reason == "recoil":
                pass
            elif reason == "[silent]":
                pass
            elif reason == "[from]drain":
                # we don't halve last_damage here (for now WIP)
                pass
            elif reason == "[from]recoil":
                pass
            elif reason == "[from]confusion":
                pass
            elif reason == "[from]leechseed":
                pass
            else:
                print(split_msg)
                assert False

    def _sethp(self, split_msg):
        assert False, "setMechanics.intehp assumed impossile"

    def _fail(self, split_msg):
        is_us = self.is_us(split_msg)
        side, _ = self.sides(is_us)
        if self.msg_index > 0:
            prev_split_msg = self.msg_lines[self.msg_index - 1].split("|")
            if len(prev_split_msg) > 1 and prev_split_msg[1] in ("-boost", "-unboost"):
                self.unstore_stats()

    def move(self, split_msg):
        is_us = self.is_us(split_msg)
        side, opp_side = self.sides(is_us)
        vol = side.active.volatiles()
        dur, opp_dur = self.get_durations(is_us)
        move_details = self.public.last_move(0 if is_us else 1)

        Mechanics.before_move(self.public, side, dur, None)
        self.store_stats()
        move_id: str | None = (
            normalize_name(split_msg[3]) if len(split_msg) > 3 else None
        )
        move: int = oak.id_to_move(move_id)
        missed: bool = any(s.strip() == "[miss]" for s in split_msg)

        # TODO uncomment this lol
        # moving means side is free from binding
        # opp_side.active.volatiles().binding = False
        # opp_dur.binding = 0

        from_metronome = len(split_msg) > 5 and split_msg[5] == "[from] Metronome"
        from_mirror_move = len(split_msg) > 5 and split_msg[5] == "[from] MirrorMove"

        rage = move_id == "rage"
        charging_move = move_id in Constants.CHARGING_MOVES
        thrashing_move = move_id in Constants.THRASHING_MOVES
        binding_move = move_id in Constants.BINDING_MOVES
        if binding_move:
            # targeting a pokemon with a binding move will clear recharge even if it misses
            opp_side.active.volatiles().recharging = False
        else:
            vol.binding = False
            dur.binding = 0

        pp_deduction = (
            0
            if (rage and vol.rage)
            or (charging_move and not vol.charging)
            or (thrashing_move and vol.thrashing)
            or (binding_move and vol.binding)
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
            and not from_metronome
            and not from_mimic
            and not from_mirror_move
            # and not vol.transform
        ):
            # idiom to add single move while while keeping existing moves the same
            if not vol.transform:
                s = oak.Set()
                s.moves = [oak.id_to_move(move_id), 0, 0, 0]

                oak.complete_pokemon_moves(side.stored(), s)
                oak.complete_active_moves(side.active, s)

            if not vol.transform:
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

        # counterable

        # TODO wtf is this, fix it

        if (rage or pp_deduction or vol.thrashing) and not (
            charging_move and not vol.charging
        ):
            side.last_used_move = move
            side.last_selected_move = move
            if not is_us:
                move_details.index = 1
                for m in range(4):
                    ms = side.active.move(m)
                    if ms.id == move:
                        move_details.index = m + 1
                        break

        if (rage or pp_deduction or vol.thrashing or charging_move) and not (
            vol.charging
        ):
            side.last_selected_move = move

        if rage or pp_deduction or vol.thrashing or charging_move:
            player_index = 0 if is_us else 1
            counterable = Mechanics.is_counterable(self.public, player_index)
            self.public.last_move(player_index).counterable = counterable

        # Move side effects

        if move_id in Constants.THRASHING_MOVES:
            if not vol.thrashing:
                vol.thrashing = True
                dur.attacking = 1

        if charging_move and vol.charging:
            vol.charging = False

        if missed:
            vol.binding = False
            dur.binding = 0
        else:
            if move_id in Constants.BINDING_MOVES:
                if vol.binding:
                    dur.binding = min(4, dur.binding + 1)
                else:
                    side.active.volatiles().binding = True
                    dur.binding = 1

            if move_id == "bide":
                if vol.bide:
                    dur.attacking = dur.attacking + 1
                else:
                    vol.bide = True

            if move_id == "rage" and not vol.rage:
                vol.rage = True
                # rage is not applied if target is immune
                if len(self.msg_lines) >= self.msg_index + 2:
                    next_ = self.prev_split_msg(-1)
                    immune = next_[1] == "-immune"
                    broke_sub = next_[1] == "-end" and next_[3] == "Substitute"
                    if immune or broke_sub:
                        vol.rage = False

        is_still = len(split_msg) > 5 and split_msg[5] == "[still]"
        move_data = oak.move_data(move)
        effect = move_data["effect"]
        on_begin = 0 < effect <= 16
        if move_id in Constants.SWITCH_MOVES:
            self.public.last_damage = 0
        if not is_still and not on_begin and move_id != "counter":
            self.public.last_damage = 0

    def _boost(self, split_msg):
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
        Mechanics.boost(self.public, side, opp_side, prop, amount)

    def _unboost(self, split_msg):
        is_us = self.is_us(split_msg)
        side, opp_side = self.sides(is_us)
        stat: str | None = split_msg[3].strip() if len(split_msg) > 3 else None
        amount = int(split_msg[4].strip()) if len(split_msg) > 4 else 0
        prop = _STAT_ABBREV_TO_BOOST_PROPERTY.get(stat)
        # Showdown bug where no -fail is emitted when Rage is the reason so we check here
        prev = self.prev_split_msg()
        if (
            prop == "atk"
            and len(prev) > 4
            and prev[3] == "atk"
            and prev[4] == "[from] Rage"
            and side.active.stats().atk == 999  # aurora beam :\
        ):
            # TODO bug from bubblebeam failing after rage succeeds
            side.active.boosts().atk -= 1
            self.unstore_stats()
        else:
            Mechanics.unboost(self.public, side, prop, amount)

    def _status(self, split_msg):
        is_us = self.is_us(split_msg)
        side, _ = self.sides(is_us)
        dur, _ = self.get_durations(is_us)
        status_str = split_msg[3].strip() if len(split_msg) > 3 else ""
        from_str = normalize_name(split_msg[5]) if len(split_msg) > 5 else None
        byte: int = _STATUS_BYTE.get(status_str, None)
        assert byte is not None, f"Bad status string lookup: {status_str}"
        if from_str == "rest":
            Mechanics.rest(side, dur)
        else:
            if status_str == Constants.SLEEP:
                Mechanics.sleep(side, dur)
            elif status_str == Constants.TOXIC:
                vol = side.active.volatiles()
                vol.toxic = True
                vol.toxic_counter = 0
                side.stored().status = byte
            else:
                side.stored().status = byte
                pass

        Mechanics.status_modify(side.stored().status, side.active.stats())
        self.store_stats()

    def _clearallboost(self, _split_msg):
        for i in range(2):
            Mechanics.clear_boosts(self.public.side(i))
            Mechanics.clear_volatiles(self.public.side(i), self.durations.get(i))

    def _curestatus(self, split_msg):
        is_us = self.is_us(split_msg)
        side, _ = self.sides(is_us)
        dur, _ = self.get_durations(is_us)
        from_haze = False
        from_mist = False  # TODO does mist cause errors???
        if self.msg_index > 0:
            prev = self.msg_lines[self.msg_index - 1].split("|")
            if prev[1] == "-clearallboost":
                from_haze = True
        woke = (
            Mechanics.is_sleep(side.stored().status) and not from_haze and not from_mist
        )
        if woke:
            Mechanics.before_move(self.public, side, dur)
            self.store_stats()
        Mechanics.cure_status(side, dur)

    def _start(self, split_msg):
        is_us = self.is_us(split_msg)
        side, _ = self.sides(is_us)
        active, _ = self.actives(is_us)
        vol, _ = self.volatiles(is_us)
        s = normalize_name(split_msg[3].split(":")[-1])
        dur, _ = self.get_durations(is_us)
        if s == "substitute":
            vol.substitute = True
            vol.substitute_hp = (side.stored().stats().hp // 4) + 1 or 1
        elif s == "reflect":
            vol.reflect = True
        elif s == "lightscreen":
            vol.light_screen = True
        elif s == "bide":
            vol.bide = True
            vol.state = 0
            vol.attacks = 1
            dur.attacking = 1
        elif s == "disable":
            move = oak.id_to_move(normalize_name(split_msg[4]))
            vol.disable_left = 1
            dur.disable = 1
            slot = 0
            for i in range(4):
                if active.move(i).id == move:
                    slot = i + 1
                    break
            if slot == 0:
                # we have revealed a new move
                for i in range(4):
                    if active.move(i).id == 0:
                        slot = i + 1
                        break
            vol.disable_move = slot
            assert (
                slot > 0
            ), f"{oak.move_id(move)} not compatible with f{[oak.move_id(active.move(i).id) for i in range(4)]}"
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
                    found = True
                    break
            if not found:
                assert False
        elif s == "leechseed":
            vol.leech_seed = True
        elif s == "confusion":
            # we only check for |silent| here since otherwise it's only emitted by clearVolatiles which belongs to haze
            if len(split_msg) > 4 and split_msg[4] == "[silent]":
                vol.thrashing = 0
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

    def did_player_crit(self, player: int) -> bool:
        for line in self.msg_lines:
            split_msg = line.split("|")
            if split_msg[1] == "-crit":
                critting_player = 1 if self.is_us(split_msg) else 0
                if critting_player == player:
                    return True
        return False

    def sub_dmg(self, attacker: int):
        attacking_side, defending_side = self.sides(attacker == 0)
        crit = self.did_player_crit(attacker)
        dmg = Mechanics.calc_damage(
            self.public,
            attacking_side,
            defending_side,
            attacking_side.last_used_move,
            crit,
        )
        self.public.last_damage = dmg
        return dmg

    def _end(self, split_msg):
        is_us = self.is_us(split_msg)
        side, opp_side = self.sides(is_us)
        vol, _ = self.volatiles(is_us)
        dur, _ = self.get_durations(is_us)
        s = normalize_name(split_msg[3].split(":")[-1])

        if s == "mustrecharge":
            vol.recharging = False
        elif s == "substitute":
            dmg = self.sub_dmg(attacker=(1 if is_us else 0))
            vol.substitute = False
            vol.substitute_hp = 0
        elif s == "disable":
            vol.disable_left = 0
            vol.disable_move = 0
            dur.disable = 0
        elif s == "bide":
            Mechanics.before_move(self.public, side, dur, reason=FailReason.bide)
            self.store_stats()
            vol.bide = False
            # vol.state = 0
            vol.attacks = 0
            dur.attacking = 0
        elif s == "confusion":
            vol.confusion = False
            vol.confusion_left = 0
            dur.confusion = 0
        elif s == "mist":
            vol.mist = False
        elif s == "reflect":
            vol.reflect = False
        elif s == "lightscreen":
            vol.light_screen = False
        elif s == "focusenergy":
            vol.focus_energy = False
        elif s == "leechseed":
            vol.leech_seed = False
        elif s == "toxiccounter":
            vol.toxic = False
            vol.toxic_counter = 0
        else:
            print(split_msg)
            assert False, f"Bad volatile {s}"

    def _mustrecharge(self, split_msg):
        is_us = self.is_us(split_msg)
        vol, _ = self.volatiles(is_us)
        vol.recharging = True

    def _transform(self, split_msg):
        is_us = self.is_us(split_msg)
        _, opp_side = self.sides(is_us)
        opp_side_index = 1 if is_us else 0
        active, opp_active = self.actives(is_us)
        vol, opp_vol = self.volatiles(is_us)
        vol.transform = True
        opp_pokemon_index = opp_side.order[0]
        vol.transform_species = (
            ((opp_side_index << 3) | opp_pokemon_index)
            if not opp_vol.transform
            else opp_vol.transform_species
        )
        # vol.transform_species = 1
        stats, opp_stats = active.stats(), opp_active.stats()
        stats.atk = opp_stats.atk
        stats.def_ = opp_stats.def_
        stats.spe = opp_stats.spe
        stats.spc = opp_stats.spc
        active.species = opp_active.species
        active.types = opp_active.types
        boosts, opp_boosts = active.boosts(), opp_active.boosts()
        boosts.atk = opp_boosts.atk
        boosts.def_ = opp_boosts.def_
        boosts.spe = opp_boosts.spe
        boosts.spc = opp_boosts.spc
        boosts.acc = opp_boosts.acc
        boosts.eva = opp_boosts.eva
        if self.truth is None:
            for i in range(4):
                active.move(i).id = opp_active.move(i).id
                active.move(i).pp = 5 if opp_active.move(i).id else 0
        else:
            opp_active_truth = self.truth.battle.side(1 if is_us else 0).active
            for i in range(4):
                active.move(i).id = opp_active_truth.move(i).id
                active.move(i).pp = 5 if opp_active_truth.move(i).id else 0

    def _activate(self, split_msg):
        is_us = self.is_us(split_msg)
        side, opp_side = self.sides(is_us)
        active, _ = self.actives(is_us)
        vol, opp_vol = self.volatiles(is_us)
        dur, opp_dur = self.get_durations(is_us)
        s = normalize_name(split_msg[3].split(":")[-1]) if len(split_msg) > 3 else None
        assert s is not None
        if s == "substitute":
            # TODO this happens when a sub is damaged but not broken or when a bnding move is immune's while the foe sub is up
            dmg = self.sub_dmg(attacker=(1 if is_us else 0))
            vol.substitute_hp -= min(vol.substitute_hp - 1, dmg)
            # vol.substitute_hp = max(1, vol.substitute_hp)
            self.public.last_damage = dmg
            if oak.move_data(opp_side.last_used_move)["effect"] in (32, 33):
                self.public.last_damage = max(1, self.public.last_damage // 2)
        elif s == "confusion":
            dur.confusion = dur.confusion + 1
        elif s == "bide":
            Mechanics.before_move(self.public, side, dur)
            self.store_stats()
            dur.attacking = dur.attacking + 1
        elif s == "":
            assert split_msg[-1] == "move: Splash"
        elif s == "haze":
            # we handle this in -clearallboosts
            pass
        elif s == "mist":
            vol.mist = True
        else:
            assert False, f"Activate idk: {s}"

    def _prepare(self, split_msg):
        is_us = self.is_us(split_msg)
        side, _ = self.sides(is_us)
        move_name = normalize_name(split_msg[3]) if len(split_msg) > 3 else ""
        if move_name in Constants.CHARGING_MOVES:
            side.active.volatiles().charging = True
        elif move_name in Constants.INVULN_MOVES:
            side.active.volatiles().charging = True
            side.active.volatiles().invulnerable = True
        else:
            assert False, f"Prepare unexpected move: {move_name}"

    def cant(self, split_msg):
        is_us = self.is_us(split_msg)
        side, opp_side = self.sides(is_us)
        vol, opp_vol = self.volatiles(is_us)
        dur, opp_dur = self.get_durations(is_us)

        cant = None

        reason = split_msg[3].strip()
        if reason == "recharge":
            cant = CantReason.recharge
        elif reason == Constants.PARALYZED:
            cant = CantReason.par
        elif reason == Constants.FROZEN:
            cant = CantReason.frz
        elif reason == Constants.SLEEP:
            dur.set_sleep(0, dur.sleep(0) + 1)

            def is_self(status):
                return bool(status & 128)

            if is_self(side.stored().status):
                side.stored().status = side.stored().status - 1
            cant = CantReason.slp
        elif reason == "partiallytrapped":
            cant = CantReason.partiallytrapped
        elif reason == "flinch":
            # This happens silently too so we just ignore flinch vol TODO
            vol.recharging = False
            cant = CantReason.flinch
        elif reason == "Disable":
            cant = CantReason.Disable
        else:
            assert False, f"Unsupported reason for cant {reason}"

        Mechanics.before_move(self.public, side, dur, cant)
        self.store_stats()

    def turn(self, split_msg):
        self.public.turn = int(split_msg[2])

    def inactive(self, split_msg):
        if len(split_msg) > 2 and split_msg[2].startswith(Constants.TIME_LEFT):
            m = re.search(r"(\d+) sec this turn", split_msg[2])
            if m:
                try:
                    self.time_remaining = int(m.group(1))
                except ValueError:
                    pass

    def inactiveoff(self, split_msg):
        self.time_remaining = None

    def noinit(self, split_msg):
        # TODO wtf is this
        if len(split_msg) > 3 and split_msg[2] == "rename":
            self.tag = split_msg[3]

    def upkeep(self, split_msg):
        pass

    def _immune(self, split_msg):
        # rage/immune is handled in move
        pass

    def _miss(self, split_msg):
        self.public.last_damage = 0
        is_us = self.is_us(split_msg)
        side, _ = self.sides(is_us)
        if side.last_used_move in tuple(
            oak.id_to_move(_) for _ in ("jumpkick", "highjumpkick")
        ):
            self.public.last_damage = 1

    def impossible(self):
        pass

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
        self.last_log = list(self.msg_lines)
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
        "switch": _switch,
        "drag": impossible,
        "faint": faint,
        "turn": turn,
        "-fail": _fail,
        "-heal": _heal,
        "-damage": _damage,
        "-miss": _miss,
        "-sethp": _sethp,
        "-boost": _boost,
        "-unboost": _unboost,
        "-clearallboost": _clearallboost,
        "-status": _status,
        "-curestatus": _curestatus,
        "-activate": _activate,
        "-prepare": _prepare,
        "-start": _start,
        "-end": _end,
        "-immune": _immune,
        "-transform": _transform,
        "-mustrecharge": _mustrecharge,
        "upkeep": upkeep,
        "cant": cant,
        "inactive": inactive,
        "inactiveoff": inactiveoff,
        "noinit": noinit,
        "-clearnegativeboost": impossible,
        "-singleturn": impossible,
        "-clearnegativeboost": impossible,
        "-clearboost": impossible,
        "-setboost": impossible,
        "-singlemove": impossible,
        "-anim": impossible,
    }
