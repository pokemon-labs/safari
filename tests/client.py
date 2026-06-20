from src.battle import PSBattle, PSPlayer

import oak
import oak.log

import random

import argparse

parser = argparse.ArgumentParser()

PLAYER = 1

parser.add_argument("--main", type=str, choices=["scan"], default="scan")
parser.add_argument(
    "--seed",
    type=int,
    default=None,
)
parser.add_argument(
    "--games",
    type=int,
    default=None,
)
parser.add_argument(
    "--start",
    type=int,
    default=0,
)
parser.add_argument(
    "--verbose",
    type=int,
    default=0,
)


BANNED_MOVES = tuple(
    oak.id_to_move(x)
    for x in (
        # ts is fucking annoying i dont understand pp deduction currently
        "metronome",
        "mirrormove",
        # if a mimic'd move is already present the mon can have it twice and we can't tell which one was used/pp deducted
        "mimic",
        # These seem done we are only hitting cases where libpkmn leaks that binding has ended
        "bind",
        "wrap",
        "firespin",
        "clamp",
        # Not handling first-rage-on-immune correctly (don't set Rage, but immune message comes after)
        "rage",
        # this seems to be done, but we don't compare bide damage when min_damage should be used
        "bide",
        # These are done except flinch silently clears recharge. We can correct this with the request object (TODO clear recharge if can attack) but only for p1
        "headbutt",
        "stomp",
        "bite",
        "lowkick",
    )
)


def fill_side_randomly(side: oak.Side, RNG):
    species_list = list(range(1, 150))
    # species_list.remove(oak.id_to_species("ditto"))
    RNG.shuffle(species_list)
    for i, species in enumerate(species_list[:6]):
        s = oak.Set()
        s.species = species
        s.level = 100
        oak.fill_random_moveset(s, RNG.randint(0, 2**32 - 1))
        moves = list(s.moves)
        for m in range(4):
            if moves[m] in BANNED_MOVES:
                moves[m] = 0
        moves = sorted(moves, reverse=True)
        s.moves = moves
        oak.complete_pokemon_from_set(side.pokemon(i), s)
    side.order = list(range(1, 7))


def get_battle(RNG) -> oak.Battle:
    battle = oak.Battle()
    fill_side_randomly(battle.side(0), RNG)
    fill_side_randomly(battle.side(1), RNG)
    return battle


class Messages:
    def __init__(self, args):
        self.data = []
        self.args = args

    def append(self, x):
        self.data.append(x)
        if self.args.verbose:
            print(x)


def scan():
    args = parser.parse_args()
    for seed in range(args.start, 10000):
        print(f"SEED: {seed}")
        RNG = random.Random(seed)
        battle = get_battle(RNG)
        durations = oak.Durations()
        ps_battle = PSBattle("", PSPlayer(), PSPlayer())
        ps_battle.us = "p1"
        result, msg = oak.log.update(battle, durations, 0, 0, PLAYER)
        messages = Messages(args)
        for line in msg:
            ps_battle.update(line)
            messages.append(line)
        ps_battle.process_msg_lines_and_clear()

        while not oak.result_type(result):
            p1_choices, p2_choices = oak.choices(battle, result)
            c1 = RNG.choice(p1_choices)
            c2 = RNG.choice(p2_choices)
            messages.append(
                f"{oak.choice_label(battle.side(0), c1)} {oak.choice_label(battle.side(1), c2)}"
            )
            result, msg = oak.log.update(battle, durations, c1, c2, PLAYER)
            for line in msg:
                ps_battle.update(line)
                messages.append(line)
            # active move id's are public after transform
            # ps_battle.truth = PSBattle.Truth()
            ps_battle.init_truth()
            ps_battle.truth.battle = battle
            ps_battle.truth.durations = durations
            ps_battle.process_msg_lines_and_clear()
            matches, reason = oak.log.compare_battles(
                ps_battle.public,
                ps_battle.durations,
                battle,
                durations,
            )
            messages.append(
                "Actual battle:\n"
                + oak.battle_string(
                    battle,
                    durations,
                )
            )
            messages.append(
                "Client battle:\n"
                + oak.battle_string(
                    ps_battle.public,
                    ps_battle.durations,
                )
            )
            if not matches:
                messages.append(f"Mismatch: {reason}")
                for line in messages.data:
                    print(line)
                print(f"Failure at seed: {seed}")

                return


def main():
    args, _ = parser.parse_known_args()

    handles = [("scan", scan)]
    for name, fn in handles:
        if args.main == name:
            fn()
            return
    assert False, f"bad --main kwarg, valid: {[name for name, _ in handles]}"


if __name__ == "__main__":
    main()
