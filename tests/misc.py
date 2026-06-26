from src.battle import Mechanics
import oak
import oak.log

from collections import defaultdict


def basic_confusion(attacker: str, defender: str, move: str) -> int:
    b, d, r = oak.parse_battle(f"{attacker} tackle | {defender} tackle def=176")
    dmg = Mechanics.calc_damage(
        b,
        b.side(0),
        b.side(1),
        oak.id_to_move(move),
        crit=True,
        adjust=True,
        roll=217,
    )
    return dmg


def full_para():
    b, d, r = oak.parse_battle("rhydon thunderwave | alakazam recover thunderwave")
    c1 = 5
    for i in range(20):
        c2 = 5 if ((i % 2) == 0) else 9
        r, msg = oak.log.update(b, d, c1, c2, 1)
        opp = b.side(1)
        for line in msg:
            print(line)
        print(
            f"{oak.move_id(opp.last_selected_move)} {oak.move_id(opp.last_used_move)}"
        )


if __name__ == "__main__":
    # print(basic_confusion("dragonair", "goldeen", "rage"))
    full_para()
    # dmgs = defaultdict(lambda: [])
    # for s in range(1, 152):
    #     species = oak.species_id(s)
    #     d = basic_confusion(species)
    #     dmgs[d].append(species)
    # dmgs = [(k, v) for k, v in dmgs.items()]
    # dmgs.sort()
    # for entry in dmgs[:]:
    #     print(entry)
