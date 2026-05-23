import logging
import random
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from copy import deepcopy

from constants import BattleType
from fp.battle import Battle
from config import FoulPlayConfig

import oak
import numpy as np

logger = logging.getLogger(__name__)


def select_move_from_mcts_results_oak(
    mcts_results: list[(oak.Output, float, int)],
) -> str:
    final_policy = {}

    total_empirical = np.zeros(9)

    for mcts_output, sample_chance, index in mcts_results:
        for i in range(9):
            total_empirical[i] += sample_chance * mcts_output.p1_empirical[i]

    print(total_empirical)

    selected_index = int(np.argmax(total_empirical))

    print("SELECTING move 1 NO MATTER WAHT!!!")

    return "move 1"


def get_oak_result(
    battle: oak.Battle,
    durations: oak.Durations,
    search_budget: str,
    evl: str,
    bandit: str = None,
) -> oak.Output:
    heap = oak.Heap()
    agent = oak.Agent()
    agent.budget = search_budget
    agent.bandit = bandit or "pexp3-1.0-0.1"
    agent.eval = evl or "fp"
    output = oak.search(battle, durations, heap, agent)
    return output


def n_battles() -> int:
    return max(1, FoulPlayConfig.parallelism)


def perform_searches_and_select_move_oak(battle: Battle) -> str:
    battle = deepcopy(battle)
    num_battles = n_battles()
    budget = FoulPlayConfig.budget
    # TODO stubb. this was deleted from FP. Use the Set
    if battle.battle_type == BattleType.STANDARD_BATTLE:
        battles = prepare_battles(battle, num_battles)
    else:
        raise ValueError("Unsupported battle type: {}".format(battle.battle_type))

    logger.info("Searching for a move using MCTS...")
    logger.info(
        "Sampling {} battles at {} each".format(num_battles, FoulPlayConfig.budget)
    )
    with ThreadPoolExecutor(max_workers=FoulPlayConfig.parallelism) as executor:
        futures = []
        for index, (b, chance) in enumerate(battles):
            fut = executor.submit(
                get_oak_result,
                b,
                budget,
                FoulPlayConfig.eval,
                FoulPlayConfig.bandit,
            )
            futures.append((fut, chance, index))

    mcts_results = [(fut.result(), chance, index) for (fut, chance, index) in futures]
    choice = select_move_from_mcts_results_oak(mcts_results)
    logger.info("Choice: {}".format(choice))
    return choice
