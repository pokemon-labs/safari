from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import numpy as np

import oak
from config import FoulPlayConfig
from src.battle import Battle
from src.teams import TeamPredictor

logger = logging.getLogger(__name__)

_predictor: TeamPredictor | None = None


def _get_predictor() -> TeamPredictor:
    global _predictor
    if _predictor is None:
        _predictor = TeamPredictor("teams150")
    return _predictor


def _determinize(battle: Battle) -> oak.Battle:
    """Produce one fully-determined oak.Battle from the imperfect-info state."""
    pred = _get_predictor()
    det = battle.determinize(use_private=True)
    completed = pred.complete_side(det.side(1), battle.p2.pokemon)
    # write completed slots back into det side 1
    for i in range(battle.p2.pokemon):
        src = completed.pokemon(i)
        dst = det.side(1).pokemon(i)
        dst.species = src.species
        dst.level = src.level
        dst.hp = src.hp
        dst.status = src.status
        for mi in range(4):
            dst.move(mi).id = src.move(mi).id
            dst.move(mi).pp = src.move(mi).pp
    return det


def _prepare_battles(battle: Battle, n: int) -> list[tuple[oak.Battle, float]]:
    """Return n determinizations each with equal sampling weight."""
    chance = 1.0 / n
    return [(_determinize(battle), chance) for _ in range(n)]


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
    logger.debug("empirical: %s", total)
    idx = int(np.argmax(total))
    # indices 0-3 = moves 1-4, 4-8 = switches 1-5
    if idx < 4:
        return f"move {idx + 1}"
    return f"switch {idx - 3}"


def perform_searches_and_select_move(battle: Battle) -> str:
    battle = deepcopy(battle)
    n = max(1, FoulPlayConfig.parallelism)
    budget = FoulPlayConfig.budget
    durations = battle.durations

    battles = _prepare_battles(battle, n)
    logger.info("searching %d determinizations at budget=%s", n, budget)

    with ThreadPoolExecutor(max_workers=FoulPlayConfig.parallelism) as ex:
        futures = [
            (ex.submit(_oak_result, det, durations, budget, FoulPlayConfig.eval, FoulPlayConfig.bandit), w, i)
            for i, (det, w) in enumerate(battles)
        ]

    results = [(f.result(), w, i) for f, w, i in futures]
    choice = _select_move(results)
    logger.info("choice: %s", choice)
    return choice
