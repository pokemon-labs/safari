from src.battle import PSBattle, PSPlayer

import oak
import oak.log

import argparse
import random

RNG = random.Random(0)


def fill_side_randomly(side: oak.Side):
    species_list = list(range(1, 152))
    RNG.shuffle(species_list)

    for i, species in enumerate(species_list[:6]):
        s = oak.Set()
        s.species = species
        s.level = 100
        oak.fill_random_moveset(s)
        oak.complete_pokemon_from_set(side.pokemon(i), s)
    side.order = list(range(1, 7))


def get_battle(side=None) -> oak.Battle:
    battle = oak.Battle()
    if side is None:
        fill_side_randomly(battle.side(0))
        fill_side_randomly(battle.side(1))
    else:
        fill_side_randomly(battle.side(side))

    return battle


def rollout_battle_with_log(side=None):
    PLAYER = 1

    battle = get_battle(side)
    durations = oak.Durations()

    ps_battle = PSBattle("", PSPlayer(), PSPlayer())
    ps_battle.us = "p1"

    result, msg = oak.log.update(battle, durations, 0, 0, PLAYER)

    for line in msg:
        ps_battle.update(line)
    ps_battle.process_msg_lines_and_clear()

    while not oak.result_type(result):
        p1_choices, p2_choices = oak.choices(battle, result)

        c1 = RNG.choice(p1_choices)
        c2 = RNG.choice(p2_choices)

        result, msg = oak.log.update(battle, durations, c1, c2, PLAYER)

        matches, reason = oak.log.compare_battles(
            ps_battle.public,
            ps_battle.durations,
            battle,
            durations,
        )

        if not matches:
            print(f"Reason: {reason}")
            print(
                oak.battle_string(
                    ps_battle.public,
                    ps_battle.durations,
                )
            )
            return

        for line in msg:
            ps_battle.update(line)
        ps_battle.process_msg_lines_and_clear()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--side",
        type=int,
        choices=[0, 1],
        default=None,
        help="Only randomize the specified side (default: randomize both)",
    )
    args = parser.parse_args()

    rollout_battle_with_log(args.side)


if __name__ == "__main__":
    main()
