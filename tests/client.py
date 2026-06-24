from src.battle import PSBattle, PSPlayer
from src.mechanics import Mechanics

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
parser.add_argument(
    "--banned-moves",
    type=str,
    default="",
)
parser.add_argument(
    "--no-flinch",
    action="store_true",
)
parser.add_argument(
    "--no-binding",
    action="store_true",
)
parser.add_argument(
    "--no-pp",
    action="store_true",
)

TEAMS = oak.load_teams("/home/user/teams150")


def get_banned_moves(args) -> list[int]:
    banned = []
    if args.no_flinch:
        banned += ["headbutt", "stomp", "bite", "lowkick", "boneclub"]
    if args.no_binding:
        banned += ["wrap", "bind", "clamp", "firespin"]
    if args.no_pp:
        banned += ["metronome", "mimic", "mirrormove"]
    if args.banned_moves:
        banned += args.banned_moves.split(",")
    return [oak.id_to_move(m) for m in banned]


def get_match_keys(args):
    battle = oak.Battle()
    durations = oak.Durations()

    for i in range(2):
        side = battle.side(i)
        vol = side.active.volatiles()
        vol.substitute_hp = 20
        vol.state = 50
        battle.last_move(i).index = 1
        battle.last_move(i).counterable = 1

    # opp stuff
    battle.side(1).pokemon(0).hp = 15
    battle.side(1).last_selected_move = 1

    battle.last_damage = 15

    durations.get(0).binding = 1
    durations.get(1).binding = 1

    return battle, durations


def fill_side_randomly(RNG, side: oak.Side, banned_moves: list[int]):
    species_list = list(range(1, 150))
    # species_list.remove(oak.id_to_species("ditto"))
    RNG.shuffle(species_list)
    order = [0 for _ in range(6)]
    for i, species in enumerate(species_list[:6]):
        s = oak.Set()
        s.species = species
        s.level = 100
        oak.fill_random_moveset(s, RNG.randint(0, 2**32 - 1))
        moves = list(s.moves)
        for m in range(4):
            if moves[m] in banned_moves:
                moves[m] = 0
        moves = sorted(moves, reverse=True)
        s.moves = moves
        oak.complete_pokemon_from_set(side.pokemon(i), s)
        order[i] = i + 1
    side.order = order
    # team = RNG.choice(TEAMS)
    # for i, s in enumerate(team):
    #     oak.complete_pokemon_from_set(side.pokemon(i), s)
    # side.order = list(range(1, 7))


def get_battle(RNG, banned_moves: list[int]) -> oak.Battle:
    battle = oak.Battle()
    fill_side_randomly(RNG, battle.side(0), banned_moves)
    fill_side_randomly(RNG, battle.side(1), banned_moves)
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
    battle_key, durations_key = get_match_keys(args)
    banned_moves = get_banned_moves(args)

    for seed in range(args.start, 100000):
        print(f"SEED: {seed}")
        RNG = random.Random(seed)

        battle = get_battle(RNG, banned_moves)
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

            # last_selected_move
            active = ps_battle.public.side(0).active
            vol = active.volatiles()
            is_forced = vol.charging or vol.recharging or vol.rage or vol.thrashing
            c1_type = c1 & 3
            c1_data = c1 >> 2
            if c1_type == 1:
                if c1_data > 0 and not is_forced:
                    ms = battle.side(0).active.move(c1_data - 1)
                    ps_battle.public.side(0).last_selected_move = ms.id
                elif c1_data == 0:
                    ps_battle.public.side(0).last_selected_move = oak.id_to_move(
                        "struggle"
                    )

            # UPDATE
            result, msg = oak.log.update(battle, durations, c1, c2, PLAYER)

            for line in msg:
                ps_battle.update(line)
                messages.append(line)

            # must come after so log appears first in printout
            messages.append(
                "Actual battle:\n"
                + oak.battle_string(
                    battle,
                    durations,
                )
            )

            # This is currently just for our active.moves after a transform
            ps_battle.init_truth()
            ps_battle.truth.battle = battle
            ps_battle.truth.durations = durations

            # LOG UPDATE
            ps_battle.process_msg_lines_and_clear()
            messages.append(
                "Client battle:\n"
                + oak.battle_string(
                    ps_battle.public,
                    ps_battle.durations,
                )
            )

            matches, reason = oak.log.compare_battles(
                ps_battle.public,
                ps_battle.durations,
                battle,
                durations,
                battle_key,
                durations_key,
            )

            if not matches:
                can_sub_glitch = False
                if reason.startswith("last_damage"):
                    sides = tuple(ps_battle.public.side(i) for i in range(2))
                    can_sub_glitch = Mechanics.can_sub_confusion_glitch(
                        sides[0], sides[1]
                    ) or Mechanics.can_sub_confusion_glitch(sides[1], sides[0])
                messages.append(f"Mismatch: {reason}")
                for line in messages.data:
                    print(line)
                print(f"Failure at seed: {seed}")
                if can_sub_glitch:
                    print("SKIPPING SEED")
                    continue
                else:
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
