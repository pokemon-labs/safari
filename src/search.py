from __future__ import annotations

import logging
import random
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import numpy as np

import oak
from src.config import FoulPlayConfig
from src.battle import Battle
from src.teams import TeamPredictor

logger = logging.getLogger(__name__)

def _fill_opponent(battle: Battle, det: oak.Battle) -> None: ...

def _make_determinization(battle: Battle) -> oak.Battle:
    det = deepcopy(battle.public)
    _fill_opponent(battle, det)
    return det


def _oak_result(
    det: oak.Battle,
    durations: oak.Durations,
    budget: str,
    evl: str,
    bandit: str,
) -> oak.Output:
    heap = oak.Heap()
    agent = oak.Agent()
    agent.budget = budget
    agent.bandit = bandit or "pexp3-1.0-0.1"
    agent.eval = evl or "fp"
    return oak.search(det, durations, heap, agent)


def _select_move(results: list[tuple[oak.Output, float, int]]) -> str:
    total = np.zeros(9)
    for output, weight, _ in results:
        for i in range(9):
            total[i] += weight * output.p1_empirical[i]
    logger.debug(f"empirical: {total}")
    idx = int(np.argmax(total))
    if idx < 4:
        return f"move {idx + 1}"
    return f"switch {idx - 3}"


def perform_searches_and_select_move(battle: Battle) -> str:
    n = max(1, FoulPlayConfig.parallelism)
    weight = 1.0 / n
    durations = battle.durations
    budget = FoulPlayConfig.budget

    dets = [_make_determinization(battle) for _ in range(n)]
    logger.info(f"searching {n} determinizations budget={budget}")

    with ThreadPoolExecutor(max_workers=n) as ex:
        futures = [
            (ex.submit(_oak_result, det, durations, budget, FoulPlayConfig.eval, FoulPlayConfig.bandit), weight, i)
            for i, det in enumerate(dets)
        ]

    results = [(f.result(), w, i) for f, w, i in futures]
    choice = _select_move(results)
    logger.info(f"choice: {choice}")
    return choice
