from src.battle import PSBattle, PSPlayer

import oak
import oak.log

import argparse
import random

seed = random.randint(0, 2**64 - 1)
RNG = random.Random(seed)


def fill_side_randomly(side: oak.Side):
    species_list = list(range(1, 152))
    RNG.shuffle(species_list)

    for i, species in enumerate(species_list[:6]):
        s = oak.Set()
        s.species = species
        s.level = 100
        oak.fill_random_moveset(s, RNG.randint(0, 2**32 - 1))
        oak.complete_pokemon_from_set(side.pokemon(i), s)
    side.order = list(range(1, 7))


def get_battle() -> oak.Battle:
    battle = oak.Battle()
    fill_side_randomly(battle.side(0))
    fill_side_randomly(battle.side(1))
    return battle


def rollout_battle_with_log():
    PLAYER = 1

    battle = get_battle()
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
        print(f"{oak.choice_label(battle.side(0), c1)} {oak.choice_label(battle.side(1), c2)}")

        result, msg = oak.log.update(battle, durations, c1, c2, PLAYER)
        for line in msg:
            ps_battle.update(line)
        ps_battle.process_msg_lines_and_clear()

        matches, reason = oak.log.compare_battles(
            ps_battle.public,
            ps_battle.durations,
            battle,
            durations,
        )

        if not matches:
            print(f"Mismatch: {reason}")
            print(
                "Client battle:\n",
                oak.battle_string(
                    ps_battle.public,
                    ps_battle.durations,
                ),
            )
            print(
                "Actual battle:\n",
                oak.battle_string(
                    battle,
                    durations,
                ),
            )
            return


def main():
    global seed, RNG
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
    )
    args = parser.parse_args()

    if not args.seed is None:
        seed = args.seed
        RNG = random.Random(seed)

    print(f"Seed: {seed}")

    rollout_battle_with_log()


if __name__ == "__main__":
    main()
