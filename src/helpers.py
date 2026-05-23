import math
import constants
from config import FoulPlayConfig


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


def common_pkmn_stat_calc_gen_1_2(stat, level):
    return math.floor(((((stat + 15) * 2) + 63) * level) / 100)


def _calculate_stats_gen_1_2(base_stats, level):
    new_stats = dict()

    new_stats[constants.HITPOINTS] = (
        common_pkmn_stat_calc_gen_1_2(base_stats[constants.HITPOINTS], level)
        + level
        + 10
    )

    new_stats[constants.ATTACK] = (
        common_pkmn_stat_calc_gen_1_2(base_stats[constants.ATTACK], level) + 5
    )
    new_stats[constants.DEFENSE] = (
        common_pkmn_stat_calc_gen_1_2(base_stats[constants.DEFENSE], level) + 5
    )
    new_stats[constants.SPECIAL_ATTACK] = (
        common_pkmn_stat_calc_gen_1_2(base_stats[constants.SPECIAL_ATTACK], level) + 5
    )
    new_stats[constants.SPECIAL_DEFENSE] = (
        common_pkmn_stat_calc_gen_1_2(base_stats[constants.SPECIAL_DEFENSE], level) + 5
    )
    new_stats[constants.SPEED] = (
        common_pkmn_stat_calc_gen_1_2(base_stats[constants.SPEED], level) + 5
    )

    new_stats = {k: int(v) for k, v in new_stats.items()}

    return new_stats


def calculate_stats(base_stats, level, ivs=(31,) * 6, evs=(85,) * 6):
    return _calculate_stats_gen_1_2(base_stats, level)
