import argparse
import logging
import os
import sys
from enum import Enum, auto


# How Safari selects its final move from the search grid
class Policy(Enum):
    argmax = auto()
    nash = auto()
    empirical = auto()
    bayesian_nash = auto()


class CustomFormatter(logging.Formatter):
    def format(self, record):
        lvl = "{}".format(record.levelname)
        return "{} {}".format(lvl.ljust(8), record.msg)


def init_logging(level, log_to_file):
    websockets_logger = logging.getLogger("websockets")
    websockets_logger.setLevel(logging.INFO)
    requests_logger = logging.getLogger("urllib3")
    requests_logger.setLevel(logging.INFO)

    # Gets the root logger to set handlers/formatters
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(level)
    stdout_handler.setFormatter(CustomFormatter())
    logger.addHandler(stdout_handler)
    Config.stdout_log_handler = stdout_handler


class SaveReplay(Enum):
    always = auto()
    never = auto()
    on_loss = auto()
    on_win = auto()


class BotModes(Enum):
    challenge_user = auto()
    accept_challenge = auto()
    search_ladder = auto()


class Format(Enum):
    ou = "gen1ou"
    randombattle = "gen1randombattle"


class _Config:
    websocket_uri: str
    username: str
    password: str | None
    user_id: str
    avatar: str
    bot_mode: BotModes
    format: Format
    budget: str
    eval: str
    bandit: str
    # parallelism is derived as p1_types * p2_types in configure()
    parallelism: int

    run_count: int
    # Path to teams file used for set predictor (opponent model).
    # For each battle loop iteration we uniformly sample a team from this file
    # and use it for the challenge/ladder request.
    # determinization grid dimensions
    teams: str
    predictor_teams: str

    p1_types: int = 1
    p2_types: int = 1
    # policy_mode controls move selection from MCTS output
    policy_mode: Policy = Policy.argmax
    user_to_challenge: str
    save_replay: SaveReplay
    room_name: str
    log_level: str
    log_to_file: bool
    stdout_log_handler: logging.StreamHandler
    vis: bool = False

    def configure(self):
        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--websocket-uri",
            required=True,
            help="The PokemonShowdown websocket URI, e.g. wss://sim3.psim.us/showdown/websocket",
        )
        parser.add_argument("--ps-username", required=True)
        parser.add_argument("--ps-password", default=None)
        parser.add_argument("--ps-avatar", default=None)
        parser.add_argument(
            "--bot-mode", required=True, choices=[e.name for e in BotModes]
        )
        parser.add_argument(
            "--user-to-challenge",
            default=None,
            help="If bot_mode is `challenge_user`, this is required",
        )
        parser.add_argument(
            "--format",
            required=True,
            type=Format,
        )
        parser.add_argument(
            "--budget",
            type=str,
            help="Time to search per battle in milliseconds",
        )
        parser.add_argument(
            "--eval",
            type=str,
            default=None,
            help="Eval. None for PokeEngine, otherwise use Oak",
        )
        parser.add_argument(
            "--bandit",
            type=str,
            help="Oak bandit algorithm",
        )
        parser.add_argument(
            "--matrix-ucb",
            type=str,
            default="",
            help="Matrix UCB start/solve-interval/minimum-visit/ucb-c-param",
        )

        parser.add_argument(
            "--run-count",
            type=int,
            default=1,
            help="Number of PokemonShowdown battles to run",
        )
        parser.add_argument(
            "--teams",
            type=str,
            default=None,
            help="Path to team file to use for the agent",
        )
        parser.add_argument(
            "--predictor-teams",
            type=str,
            default=None,
            help="Path to team file for set predictor",
        )
        parser.add_argument(
            "--predictor-ratio",
            default=1,
            help="Ratio of probability from first to last team in the predictor team file",
        )
        parser.add_argument(
            "--p1-types",
            type=int,
            default=1,
            help="Number of p1 determinization types (default: 1)",
        )
        parser.add_argument(
            "--p2-types",
            type=int,
            default=1,
            help="Number of p2 determinization types (default: 1)",
        )
        parser.add_argument(
            "--threads",
            type=int,
        )
        parser.add_argument(
            "--policy-mode",
            type=str,
            default=Policy.bayesian_nash,
            help="Move selection policy: x=argmax, n=nash, e=empirical (default: x)",
        )
        parser.add_argument(
            "--save-replay",
            default="never",
            choices=[e.name for e in SaveReplay],
            help="When to save replays",
        )
        parser.add_argument(
            "--room-name",
            default=None,
            help="If bot_mode is `accept_challenge`, the room to join while waiting",
        )
        parser.add_argument("--log-level", default="DEBUG", help="Python logging level")
        parser.add_argument(
            "--log-to-file",
            action="store_true",
            help="When enabled, DEBUG logs will be written to a file in the logs/ directory",
        )
        parser.add_argument(
            "--vis",
            action="store_true",
            help="Launch the debug visualizer at http://localhost:8765",
        )

        args = parser.parse_args()
        self.websocket_uri = args.websocket_uri
        self.username = args.ps_username
        self.password = args.ps_password
        self.avatar = args.ps_avatar
        self.bot_mode = BotModes[args.bot_mode]
        self.format = args.format
        self.budget = args.budget
        self.eval = args.eval
        self.bandit = args.bandit
        self.matrix_ucb = args.matrix_ucb
        self.p1_types = args.p1_types or 1
        self.p2_types = args.p2_types or 1
        self.parallelism = args.threads or self.p1_types * self.p2_types
        self.policy_mode = Policy(args.policy_mode)
        self.run_count = args.run_count
        self.teams = args.teams
        self.predictor_teams = args.predictor_teams
        self.predictor_ratio = args.predictor_ratio
        self.user_to_challenge = args.user_to_challenge
        self.save_replay = SaveReplay[args.save_replay]
        self.room_name = args.room_name
        self.log_level = args.log_level
        self.log_to_file = args.log_to_file
        self.vis = args.vis

        self.validate_config()

    def validate_config(self):
        if self.bot_mode == BotModes.challenge_user:
            assert (
                self.user_to_challenge is not None
            ), "If bot_mode is `CHALLENGE_USER`, you must declare USER_TO_CHALLENGE"


Config = _Config()
