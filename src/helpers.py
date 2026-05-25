import math
import src.constants as constants
from src.config import Config

# TODO totally replace with new oak id_to_move/string and the inverse functions
# assume showdown ids are the lower case oak strings
# e.g. nidoranf, nidoranm, struggle, substitute,
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

# TODO remove this now useless shite