from enum import Enum, auto

import oak

BOOSTS = (
    (25, 100),
    (28, 100),
    (33, 100),
    (40, 100),
    (50, 100),
    (66, 100),
    (1, 1),
    (15, 10),
    (2, 1),
    (25, 10),
    (3, 1),
    (35, 10),
    (4, 1),
)


class Constants:
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
    BINDING_MOVES = ("bind", "clamp", "firespin", "wrap")
    SWITCH_MOVES = (
        "roar",
        "whirlwind",
    )  # we exclude Teleport cus that doesnt reset damage on showdown
    DRAIN_MOVES = (
        "megadrain",
        "absorb",
        "leechlife",
        "dreameater",
    )


_STAT_ABBREV_TO_BOOST_PROPERTY = {
    "atk": "atk",
    "def": "def",
    "spa": "spa",
    "spd": "spd",  # Showdown sends boost msg for spa and spd so we just ignore spd
    "spe": "spe",
    "accuracy": "acc",
    "evasion": "eva",
}

# fmt: off
_STATUS_BYTE = {
    Constants.SLEEP:     0b00000001, # slp1
    Constants.BURN:      0b00010000,
    Constants.FROZEN:    0b00100000,
    Constants.PARALYZED: 0b01000000,
    Constants.POISON:    0b00001000,
    Constants.TOXIC:     0b10001000,
    "rest":              0b10000010, # This is Rest2 TODO check if correct value
}
# fmt: on


class CantReason(Enum):
    slp = auto()
    frz = auto()
    par = auto()
    partiallytrapped = auto()
    flinch = auto()
    Disable = auto()
    recharge = auto()
    nopp = auto()


class BeforeMove(Enum):
    done = auto()
    ok = auto()


class ActivateReason(Enum):
    confusion = auto()


class FailReason(Enum):
    bide = auto()


class Mechanics:
    def is_sleep(status: int) -> bool:
        return bool(status & 7)

    def sleep(side: oak.Side, duration: oak.Duration):
        side.stored().status = _STATUS_BYTE["slp"]
        duration.set_sleep(0, 1)
        side.active.volatiles().recharging = False

    def rest(side: oak.Side, duration: oak.Duration):
        side.stored().status = _STATUS_BYTE["rest"]
        duration.set_sleep(0, 1)

    def clear_volatiles(side: oak.Side, duration: oak.Duration):
        vol = side.active.volatiles()
        vol.disable_move = 0
        vol.confusion = False
        vol.mist = False
        vol.focus_energy = False
        vol.leech_seed = False
        vol.light_screen = False
        vol.reflect = False
        vol.toxic = False
        vol.toxic_counter = 0
        duration.disable = 0
        duration.confusion = 0

    def clear_boosts(side: oak.Side):
        stats = side.active.stats()
        stored = side.stored().stats()
        stats.atk = stored.atk
        stats.def_ = stored.def_
        stats.spe = stored.spe
        stats.spc = stored.spc
        boosts = side.active.boosts()
        boosts.atk = 0
        boosts.def_ = 0
        boosts.spe = 0
        boosts.spc = 0
        boosts.acc = 0
        boosts.eva = 0

    def minimum_damage(attacker: oak.Side, defender: oak.Side, move: int) -> int:
        pass

    def status_modify(status: int, stats: oak.Stats):
        if status == _STATUS_BYTE["par"]:
            stats.spe = max(1, stats.spe // 4)
        elif status == _STATUS_BYTE["brn"]:
            stats.atk = max(1, stats.atk // 2)
        else:
            pass

    def unmodified_stats(battle: oak.Battle, side: oak.Side) -> oak.Stats:
        transform_id = side.active.volatiles().transform_species
        if transform_id == 0:
            return side.stored().stats()
        else:
            pokemon_index = transform_id & 7
            side_index = transform_id >> 3
            return battle.side(side_index).pokemon(pokemon_index - 1).stats()

    def boost(
        battle: oak.Battle, side: oak.Side, opp_side: oak.Side, prop: str, amount: int
    ):
        player = side.active
        boosts = player.boosts()
        if prop == "atk" or prop == "atk|[from] Rage":
            boosts.atk = min(6, boosts.atk + amount)
            mod = BOOSTS[boosts.atk + 6]
            stat = Mechanics.unmodified_stats(battle, side).atk
            side.active.stats().atk = min(999, stat * mod[0] // mod[1])
            if prop == "atk|[from] Rage":
                return
        elif prop == "def":
            boosts.def_ = min(6, boosts.def_ + amount)
            mod = BOOSTS[boosts.def_ + 6]
            stat = Mechanics.unmodified_stats(battle, side).def_
            side.active.stats().def_ = min(999, stat * mod[0] // mod[1])
        elif prop == "spe":
            boosts.spe = min(6, boosts.spe + amount)
            mod = BOOSTS[boosts.spe + 6]
            stat = Mechanics.unmodified_stats(battle, side).spe
            side.active.stats().spe = min(999, stat * mod[0] // mod[1])
        elif prop == "spa":
            boosts.spc = min(6, boosts.spc + amount)
            mod = BOOSTS[boosts.spc + 6]
            stat = Mechanics.unmodified_stats(battle, side).spc
            side.active.stats().spc = min(999, stat * mod[0] // mod[1])
        elif prop == "spd":
            return
        elif prop == "eva":
            side.active.stats().eva = min(6, boosts.eva + amount)
        else:
            assert False
        Mechanics.status_modify(opp_side.stored().status, opp_side.active.stats())

    def unboost(battle: oak.Battle, side: oak.Side, prop: str, amount: int):
        player = side.active
        boosts = player.boosts()
        if prop == "atk":
            boosts.atk = max(-6, boosts.atk - amount)
            mod = BOOSTS[boosts.atk + 6]
            stat = Mechanics.unmodified_stats(battle, side).atk
            side.active.stats().atk = max(1, stat * mod[0] // mod[1])
        elif prop == "def":
            boosts.def_ = max(-6, boosts.def_ - amount)
            mod = BOOSTS[boosts.def_ + 6]
            stat = Mechanics.unmodified_stats(battle, side).def_
            side.active.stats().def_ = max(1, stat * mod[0] // mod[1])
        elif prop == "spe":
            boosts.spe = max(-6, boosts.spe - amount)
            mod = BOOSTS[boosts.spe + 6]
            stat = Mechanics.unmodified_stats(battle, side).spe
            side.active.stats().spe = max(1, stat * mod[0] // mod[1])
        elif prop == "spa":
            boosts.spc = max(-6, boosts.spc - amount)
            mod = BOOSTS[boosts.spc + 6]
            stat = Mechanics.unmodified_stats(battle, side).spc
            side.active.stats().spc = max(1, stat * mod[0] // mod[1])
        elif prop == "spd":
            return
        elif prop == "acc":
            boosts.acc = max(-6, boosts.acc - amount)
        else:
            assert False
        Mechanics.status_modify(side.stored().status, side.active.stats())

    # def decrement_pp(side: oak.Side, mslot: int):
    #     if side.last_selected_move == oak.id_to_move("struggle"):
    #         return
    #     active = side.active
    #     vol = active.volatiles()
    #     assert not vol.rage and not vol.thrashing and True  # not multi_hit
    #     if vol.bide:
    #         return
    #     ms = active.move(mslot)
    #     ms.pp = (ms.pp - 1) % 64
    #     if vol.transform:
    #         return
    #     ms = side.stored().move(mslot)
    #     ms.pp = (ms.pp - 1) % 64
    #     assert active.move(mslot.pp) == side.stored().move(mslot).pp

    # TODO review claude
    # This covers normal damage, specialDamage, and counterDamage
    def calc_damage(
        battle: oak.Battle,
        attacker: oak.Side,
        defender: oak.Side,
        move: int | None,
        crit: bool = False,
        adjust: bool = True,
        roll: int = 217,
    ):
        cfz = False
        if move is None:
            cfz = True
            move = oak.id_to_move("pound")

        if move == oak.id_to_move("counter"):
            return 2 * battle.last_damage
        if move == oak.id_to_move("bide"):
            d = attacker.active.volatiles().state * 2
            attacker.active.volatiles().state = 0
            return d

        move_data = oak.move_data(move)
        bp = move_data["bp"]
        effect = move_data["effect"]

        if effect in (41, 42):
            return Mechanics.special_damage(attacker, defender, move)

        move_type = move_data["type"]
        is_special = move_type >= 8

        attack = Mechanics.attack(attacker, crit, is_special)
        defense = Mechanics.defense(defender, crit, is_special, cfz)

        if attack > 255 or defense > 255:
            attack = max((attack // 4) & 255, 1)
            defense = max(
                (defense // 4) & 255, 1
            )  # pkmn.options.mod path: min 1, not 0

        lvl = attacker.stored().level * (2 if crit else 1)

        # GLITCH: Explode halves defense (post-rescale)
        if effect == 34:  # Explode
            defense = max(defense // 2, 1)

        if defense == 0:
            return 0  # cartridge: division-by-zero freeze / .Error; shouldn't reach here for min-damage use

        d = (lvl * 2 // 5) + 2
        d *= int(bp)
        d *= int(attack)
        d //= defense
        d //= 50
        d = min(997, d)
        d += 2

        # ---- adjustDamage ----
        if adjust:
            atk_types = set()
            t = attacker.active.types
            atk_types.add(t & 15)
            t >>= 4
            atk_types.add(t & 15)

            if move_type in atk_types:
                d += d // 2  # STAB, integer, BEFORE effectiveness

            def_raw = defender.active.types
            type1 = def_raw & 15
            type2 = (def_raw >> 4) & 15

            NEUTRAL = 10  # x1.0 scaled by /10; eff values are 0 (immune), 5 (x0.5), 10 (x1), 20 (x2)
            eff1 = oak.get_effectiveness(move_type, type1) * 5
            eff2 = oak.get_effectiveness(move_type, type2) * 5

            # Showdown mode never takes the mismatch-precedence reorder branch (that's a
            # non-showdown-only cartridge quirk) -- intentionally omitted here.
            if eff1 != NEUTRAL:
                d = (d * eff1) // 10
            if type1 != type2 and eff2 != NEUTRAL:
                d = (d * eff2) // 10

            if d == 0:
                return 0  # immune

        # ---- randomizeDamage ----
        if d <= 1:
            return d  # not randomized

        d = (d * roll) // 255
        return d

    def attack(side: oak.Side, crit: bool, special: bool):
        if crit:
            if special:
                return side.stored().stats().spc
            else:
                return side.stored().stats().atk
        else:
            if special:
                return side.active.stats().spc
            else:
                return side.active.stats().atk

    def defense(side: oak.Side, crit: bool, special: bool, cfz: bool = False):
        if crit:
            if special:
                return side.stored().stats().spc
            else:
                return side.stored().stats().def_
        else:
            if special:
                return side.active.stats().spc * (
                    2 if side.active.volatiles().light_screen else 1
                )
            else:
                return side.active.stats().def_ * (
                    2 if not cfz and side.active.volatiles().reflect else 1
                )

    def special_damage(attacker: oak.Side, defender: oak.Side, move: int):
        d = 0
        if move == oak.id_to_move("superfang"):
            d = max(defender.stored().hp // 2, 1)
        elif move in [oak.id_to_move(m) for m in ("seismictoss", "nightshade")]:
            d = attacker.stored().level
        elif move == oak.id_to_move("sonicboom"):
            d = 20
        elif move == oak.id_to_move("dragonrage"):
            d = 40
        elif move == oak.id_to_move("psywave"):
            # assert False, "FUCK PSYWAVE"
            d = 1
        else:
            assert False, f"Invalid move for special_damage: {oak.move_id(move)}"
        return d

    def faint(battle: oak.Battle, durations: oak.Durations, player: int):
        players = (player, 0 if player else 1)
        side, opp_side = (battle.side(p) for p in players)
        dur, opp_dur = (durations.get(p) for p in players)
        side.stored().hp = 0
        dur.binding = 0
        opp_dur.binding = 0
        side.last_used_move = 0
        opp_side.last_used_move = 0
        opp_vol = opp_side.active.volatiles()
        if opp_vol.bide:
            opp_vol.state = 0

    def cure_status(side: oak.Side, duration: oak.Duration):
        side.stored().status = 0
        duration.set_sleep(0, 0)

    def interrupt(side: oak.Side, duration: oak.Duration):
        vol = side.active.volatiles()
        if not vol.rage:
            vol.state = 0
        vol.bide = False
        vol.thrashing = False
        vol.charging = False
        vol.binding = False
        duration.attacking = 0
        duration.binding = 0

    def before_move(
        battle: oak.Battle, side: oak.Side, duration: oak.Duration, reason=None
    ):
        vol = side.active.volatiles()
        if Mechanics.is_sleep(side.stored().status):
            side.last_used_move = 0
            return BeforeMove.done

        if reason == CantReason.frz:
            side.last_used_move = 0
            return BeforeMove.done

        if reason == CantReason.partiallytrapped:
            return BeforeMove.done

        if reason == CantReason.flinch:
            # recharge clearing is in the Effect fn
            return BeforeMove.done

        if reason == CantReason.recharge:
            vol.recharging = False
            return BeforeMove.done

        if vol.disable_move != 0:
            duration.disable += 1

        if reason == CantReason.Disable:
            vol.charging = False
            return BeforeMove.done

        if reason == ActivateReason.confusion:
            Mechanics.interrupt(side, duration)
            return BeforeMove.done

        if reason == CantReason.par:
            Mechanics.interrupt(side, duration)
            return BeforeMove.done

        if vol.bide:
            vol.state += battle.last_damage

        if reason == FailReason.bide:
            return BeforeMove.done

        if vol.thrashing:
            duration.attacking += 1

        if vol.binding:
            duration.binding += 1
            return BeforeMove.done

        return BeforeMove.ok

    def set_counterable(battle: oak.Battle, player: int):
        last = battle.side(player).last_selected_move
        data = oak.move_data(last)
        battle.last_move(player).counterable = (
            last != oak.id_to_move("counter")
            and data["bp"] > 0
            and data["type"] in (0, 1)
        )

    def is_counterable(battle: oak.Battle, player: int):
        last = battle.side(player).last_selected_move
        data = oak.move_data(last)
        return (
            last != oak.id_to_move("counter")
            and data["bp"] > 0
            and data["type"] in (0, 1)
        )

    def can_sub_confusion_glitch(side: oak.Side, opp_side: oak.Side):
        vol = side.active.volatiles()
        return (
            vol.substitute
            and vol.confusion
            and not opp_side.active.volatiles().substitute
        )
