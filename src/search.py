from __future__ import annotations

import logging
import random
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import numpy as np

import oak
from src.config import Config
from src.battle import Battle
from src.teams import TeamPredictor

logger = logging.getLogger(__name__)

def _fill_opponent(battle: Battle, det: oak.Battle) -> None: ...

# TODO iterate over p1/p2 det. Capture margianl prob of p1/p2 det and mutiple
# TODO change ressult type to include joint p1*p2 prob
# E.g. if p1_types = 1 means battle prob only depends on p2, like vanilla FoulPlay
def _make_determinization(battle: Battle) -> oak.Battle:
    det = deepcopy(battle.public)
    _fill_opponent(battle, det)
    return det

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
    return oak.search(battle, durations, result, heap, agent)

# TODO SYNTAX and move to config.py
enum class Policy:
    argmax : 'x'
    nash: 'n',
    empirical: 'e',

# TODO SYNTAX
def _select_move(results: list[tuple[oak.Output, float]]) -> str:
    policy: np.array | None = None
    switch (Config.policy_mode):
        case nash:
            # call a python package that does this and compute strategy
        # for de factor p1 type, do weighted sum of output.p1_empirical probs over all p2 types and their marginal probs
        empirical = None
        case argmax:
            # select argmax of empirical
        case empirical
            # select using empirical

    total = np.zeros(9)
    for output, weight, _ in results:
        for i in range(9):
            total[i] += weight * output.p1_empirical[i]
    logger.debug(f"empirical: {total}")
    idx = int(np.argmax(total))
    if idx < 4:
        return f"move {idx + 1}"
    return f"switch {idx - 3}"


# TODO remove n for only config
def perform_searches_and_select_move(battle: Battle) -> str:
    n = max(1, Config.parallelism)
    weight = 1.0 / n
    durations = battle.durations
    budget = Config.budget

    # TODO this _make function should iterate of p1, p2 types
    # 2D instead of 1D, but still a list
    # now also has det joint prob
    dets = [_make_determinization(battle) for _ in range(n)]
    logger.info(f"searching {n} determinizations budget={budget}")

    with ThreadPoolExecutor(max_workers=n) as ex:
        futures = [
            (ex.submit(_oak_result, det, durations, result, budget, Config.eval, Config.bandit), weight, i)
            for i, det in enumerate(dets)
        ]

    results = [(f.result(), w, i) for f, w, i in futures]
    choice = _select_move(results)
    logger.info(f"choice: {choice}")
    return choice
