from enum import StrEnum


class BattleType(StrEnum):
    STANDARD_BATTLE = "standard_battle"


START_STRING = "|start"
RQID = "rqid"

MOVES = "moves"
ABILITIES = "abilities"
ITEMS = "items"
COUNT = "count"
SETS = "sets"

UNKNOWN_ITEM = "unknownitem"

# a lookup for the opponent's name given the bot's name
# this has to do with the Pokemon-Showdown PROTOCOL
ID_LOOKUP = {"p1": "p2", "p2": "p1"}

FORCE_SWITCH = "forceSwitch"
REVIVING = "reviving"
WAIT = "wait"
TRAPPED = "trapped"
MAYBE_TRAPPED = "maybeTrapped"
ITEM = "item"

CONDITION = "condition"
DISABLED = "disabled"
PP = "pp"

SELF = "self"

DO_NOTHING_MOVE = "splash"

ID = "id"
BASESTATS = "baseStats"
NAME = "name"
STATUS = "status"
TYPES = "types"
TYPE = "type"
WEIGHT = "weightkg"

SIDE = "side"
POKEMON = "pokemon"
FNT = "fnt"

SWITCH_STRING = "switch"
WIN_STRING = "|win|"
TIE_STRING = "|tie"
CHAT_STRING = "|c|"
TIME_LEFT = "Time left:"
DETAILS = "details"
IDENT = "ident"

ACTIVE = "active"

PRIORITY = "priority"
STATS = "stats"
BOOSTS = "boosts"

HITPOINTS = "hp"
ATTACK = "attack"
DEFENSE = "defense"
SPECIAL_ATTACK = "special-attack"
SPECIAL_DEFENSE = "special-defense"
SPEED = "speed"
ACCURACY = "accuracy"
EVASION = "evasion"

MAX_BOOSTS = 6

STAT_ABBREVIATION_LOOKUPS = {
    "atk": ATTACK,
    "def": DEFENSE,
    "spa": SPECIAL_ATTACK,
    "spd": SPECIAL_DEFENSE,
    "spe": SPEED,
    "accuracy": ACCURACY,
    "evasion": EVASION,
}

PHYSICAL = "physical"
SPECIAL = "special"
CATEGORY = "category"

DAMAGING_CATEGORIES = [PHYSICAL, SPECIAL]

VOLATILE_STATUS = "volatileStatus"
LOCKED_MOVE = "lockedmove"

TYPECHANGE = "typechange"

# volatile statuses
REFLECT = "reflect"
LIGHT_SCREEN = "lightscreen"
MIST = "mist"
CONFUSION = "confusion"
LEECH_SEED = "leechseed"
SUBSTITUTE = "substitute"
TAUNT = "taunt"
ROOST = "roost"
PROTECT = "protect"
BANEFUL_BUNKER = "banefulbunker"
SILK_TRAP = "silktrap"
ENDURE = "endure"
SPIKY_SHIELD = "spikyshield"
DYNAMAX = "dynamax"
SLOW_START = "slowstart"
TERASTALLIZE = "terastallize"
TRANSFORM = "transform"
PARTIALLY_TRAPPED = "partiallytrapped"

PROTECT_VOLATILE_STATUSES = [PROTECT, BANEFUL_BUNKER, SPIKY_SHIELD, SILK_TRAP, ENDURE]

TAUNT_DURATION_INCREMENT_END_OF_TURN = {"gen3", "gen4"}

# non-volatile statuses
SLEEP = "slp"
BURN = "brn"
FROZEN = "frz"
PARALYZED = "par"
POISON = "psn"
TOXIC = "tox"
TOXIC_COUNT = "toxic_count"
NON_VOLATILE_STATUSES = {SLEEP, BURN, FROZEN, PARALYZED, POISON, TOXIC}

FIGHT = "fight"
