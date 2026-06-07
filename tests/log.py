from src.battle import PSBattle, PSPlayer

import oak
import oak.log

import random


def fill_side_randomly(side: oak.Side):
    species_list = list(range(1, 152))
    random.shuffle(species_list)
    for i, species in enumerate(species_list[:6]):
        s = oak.Set()
        s.species = species
        s.level = 100
        oak.fill_random_moveset(s)
        oak.complete_pokemon_from_set(side.pokemon(i), s)
    side.order = list(range(1, 7))


def get_random_battle() -> oak.Battle:
    battle = oak.Battle()
    fill_side_randomly(battle.side(0))
    fill_side_randomly(battle.side(1))
    return battle


def rollout_random_battle_with_log():
    battle = get_random_battle()
    durations = oak.Durations()

    ps_battle = PSBattle("", PSPlayer(), PSPlayer())
    ps_battle.us = "p1"

    result, msg = oak.log.update_(battle, durations, 0, 0)
    for line in msg:
        ps_battle.update(line)
    ps_battle.process_msg_lines_and_clear()

    while not oak.result_type(result):
        p1_choices, p2_choices = oak.choices(battle, result)
        c1 = random.choice(p1_choices)
        c2 = random.choice(p2_choices)
        result, msg = oak.log.update_(battle, durations, c1, c2)

        for line in msg:
            ps_battle.update(line)
        ps_battle.process_msg_lines_and_clear()

        print(oak.battle_string(ps_battle.public, ps_battle.durations))

        exit()


def main():
    rollout_random_battle_with_log()


if __name__ == "__main__":
    main()
