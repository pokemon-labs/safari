from __future__ import annotations

import logging
import random
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import numpy as np

import oak
from src.config import Config, Policy
from src.battle import Battle
from src.teams import TeamPredictor

logger = logging.getLogger(__name__)

def _fill_opponent(battle: Battle, det: oak.Battle) -> None: ...

# Iterate over p1/p2 det types. Capture marginal prob of each det and multiply
# to get joint p1*p2 prob. If p1_types == 1 the battle only depends on p2 (vanilla FoulPlay).
def _make_determinizations(battle: Battle) -> list[tuple[oak.Battle, oak.Durations, int, float]]:
    """Return a flat list of (battle, durations, result, joint_prob) for all
    p1 x p2 type combinations. Joint prob is p1_prob * p2_prob."""
    p1_count = max(1, getattr(Config, 'p1_types', 1))
    p2_count = max(1, getattr(Config, 'p2_types', 1))
    total = p1_count * p2_count
    joint_prob = 1.0 / total

    dets: list[tuple[oak.Battle, oak.Durations, int, float]] = []
    for _ in range(p1_count):
        for _ in range(p2_count):
            det = deepcopy(battle.public)
            _fill_opponent(battle, det)
            dets.append((det, battle.durations, battle.result, joint_prob))
    return dets

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


def _select_move(results: list[tuple[oak.Output, float, int]]) -> str:
    mode = getattr(Config, 'policy_mode', Policy.argmax)

    total_empirical = np.zeros(9)
    total_value_matrix = np.zeros((9, 9))

    for output, weight, _ in results:
        for i in range(9):
            total_empirical[i] += weight * output.p1_empirical[i]
        total_value_matrix += weight * output.value_matrix

    logger.debug(f"empirical: {total_empirical}")

    if mode == Policy.nash:
        # Aggregate value_matrix across determinizations and solve for Nash strategy.
        # oak.solve_matrix returns (p1_nash, p2_nash, value)
        try:
            p1_nash, _p2_nash, _val = oak.solve_matrix(total_value_matrix, discretize_factor=1000)
            nash_sum = p1_nash.sum()
            if nash_sum > 0:
                probs = p1_nash / nash_sum
            else:
                probs = np.ones(9) / 9
            idx = int(np.random.choice(9, p=probs))
        except Exception as e:
            logger.warning(f"solve_matrix failed ({e}), falling back to argmax")
            idx = int(np.argmax(total_empirical))
    elif mode == Policy.empirical:
        total_sum = total_empirical.sum()
        if total_sum > 0:
            probs = total_empirical / total_sum
        else:
            probs = np.ones(9) / 9
        idx = int(np.random.choice(9, p=probs))
    else:  # argmax (default)
        idx = int(np.argmax(total_empirical))

    if idx < 4:
        return f"move {idx + 1}"
    return f"switch {idx - 3}"


def perform_searches_and_select_move(battle: Battle) -> str:
    budget = Config.budget

    # 2D p1 x p2 determinization — each det carries its joint probability weight
    dets = _make_determinizations(battle)
    logger.info(f"searching {len(dets)} determinizations (p1={getattr(Config,'p1_types',1)} x p2={getattr(Config,'p2_types',1)}) budget={budget}")

    parallelism = max(1, Config.parallelism)
    with ThreadPoolExecutor(max_workers=parallelism) as ex:
        futures = [
            (
                ex.submit(
                    _oak_result,
                    det_battle, det_durations, det_result,
                    budget, Config.eval, Config.bandit,
                ),
                joint_prob,
                i,
            )
            for i, (det_battle, det_durations, det_result, joint_prob) in enumerate(dets)
        ]

    results = [(f.result(), w, i) for f, w, i in futures]
    choice = _select_move(results)
    logger.info(f"choice: {choice}")
    return choice
