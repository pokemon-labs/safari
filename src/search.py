from __future__ import annotations

import logging
import random
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from enum import Enum

import numpy as np

import oak
from src.config import Config
from src.battle import Battle
from src.teams import TeamPredictor

logger = logging.getLogger(__name__)

def _fill_opponent(battle: Battle, det: oak.Battle) -> None: ...

# TODO iterate over p1/p2 det. Capture marginal prob of p1/p2 det and multiply
# TODO change result type to include joint p1*p2 prob
# E.g. if p1_types = 1 means battle prob only depends on p2, like vanilla FoulPlay
def _make_determinization(battle: Battle) -> tuple[oak.Battle, oak.Durations, int]:
    det = deepcopy(battle.public)
    _fill_opponent(battle, det)
    return det, battle.durations, battle.result

# a full Oak state is oak.Battle (NOT src.Battle), oak.Durations, and uint8 = int result
def _oak_result(
    battle: oak.Battle,
    durations: oak.Durations,
    result: int,
    budget: str,
    evl: str,
    bandit: str,
) -> oak.Output:
    heap = oak.Heap()
    agent = oak.Agent()
    agent.budget = budget
    agent.bandit = bandit or "pexp3-1.0-0.1"
    agent.eval = evl or "fp"
    # oak.search(input, heap, agent) — input bundles battle+durations+result
    input_ = oak.MCTSInput(battle, durations, result)
    return oak.search(input_, heap, agent)


# TODO move to config.py
class Policy(Enum):
    argmax = 'x'
    nash = 'n'
    empirical = 'e'


def _select_move(results: list[tuple[oak.Output, float, int]]) -> str:
    mode = getattr(Config, 'policy_mode', Policy.argmax)

    total = np.zeros(9)
    for output, weight, _ in results:
        for i in range(9):
            total[i] += weight * output.p1_empirical[i]
    logger.debug(f"empirical: {total}")

    if mode == Policy.nash:
        # TODO: call oak.solve_matrix on aggregated value_matrix and use nash strategy
        # For now fall through to empirical
        pass

    if mode == Policy.argmax:
        idx = int(np.argmax(total))
    else:
        # empirical: sample from the distribution
        total_sum = total.sum()
        if total_sum > 0:
            probs = total / total_sum
        else:
            probs = np.ones(9) / 9
        idx = int(np.random.choice(9, p=probs))

    if idx < 4:
        return f"move {idx + 1}"
    return f"switch {idx - 3}"


# TODO remove n for only config
def perform_searches_and_select_move(battle: Battle) -> str:
    n = max(1, Config.parallelism)
    weight = 1.0 / n
    budget = Config.budget

    # TODO this _make function should iterate over p1, p2 types
    # 2D instead of 1D, but still a list; now also has det joint prob
    dets = [_make_determinization(battle) for _ in range(n)]
    logger.info(f"searching {n} determinizations budget={budget}")

    with ThreadPoolExecutor(max_workers=n) as ex:
        futures = [
            (
                ex.submit(
                    _oak_result,
                    det_battle, det_durations, det_result,
                    budget, Config.eval, Config.bandit,
                ),
                weight,
                i,
            )
            for i, (det_battle, det_durations, det_result) in enumerate(dets)
        ]

    results = [(f.result(), w, i) for f, w, i in futures]
    choice = _select_move(results)
    logger.info(f"choice: {choice}")
    return choice
