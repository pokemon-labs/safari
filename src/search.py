from __future__ import annotations

import math
import logging
import random
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

from src.config import Config, Policy
from src.battle import Battle
from src.teams import TeamPredictor, Team

import numpy as np
import oak

logger = logging.getLogger(__name__)

type Type = tuple[Oak.side, float]

# TODO review bayes_nash
class BayesianGame:
    def __init__(self, battle: Battle, p1_k: int, p2_k: int, predictor: TeamPredictor):
        self.p1_types: list[Type] = (
            [(battle.private, 1.0)]
            if p1_k == 1
            else [(battle.private, 1.0)] + (self._get_n_types(battle.public.side(0), predictor, p1_k - 1))
        )
        self.p2_types: list[Type] = self._get_n_types(
            battle.public.side(1), predictor, p2_k
        )

    # TODO should return a list of ([battle with p1/p2 sides, p1 type index, p2 type index])
    def flatten(self): ...

    def _get_n_types(
        self, side: oak.Side, predictor: TeamPredictor, n: int
    ) -> list[Type]:
        matches: list[tuple[Team, float]] = find_all_matching(side)[:n]
        assert len(matches) == n  # TODO
        den: float = sum(math.exp(logit) for _, logit in matches)
        return [
            (_fill_side_from_team(side, team), math.exp(logit) / den)
            for team, logit in matches
        ]

    def _fill_side_from_team(
        self, revealed: oak.Side, team: Team, max_pokemon: int = 6
    ) -> oak.Side:
        side = deepcopy(revealed)
        for s in team:
            empty_or_species_match: int | None = None
            for i in range(6):
                species: int = side.pokemon(i).species
                if species == 0 or species == s.species:
                    empty_or_species_match = i
                    break
            if empty_or_species_match is None:
                assert False, "_fill_side_from_team: Side and Team are inconsistent"
            else:
                oak.complete_pokemon_from_set(side.pokemon(empty_or_species_match), s)

# TODO gives this the necessary data of bayes_nash.py

class BayesianOutput:
    def __init__ (self, p1_k: int, p2_k: int): ...
    def add_output(self, p1_type_index:int, p2_type_index: int, p1_type_prob: float, p2_type_prob: float, oak.Output):
        pass
    def solve(self): ...



# a full Oak state is oak.Battle (NOT src.Battle), oak.Durations, and uint8 = int result
def _run_oak_search(
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


def _select_move(results: list[tuple[oak.Output, float, int]]) -> str:
    mode = getattr(Config, "policy_mode", Policy.argmax)

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
            p1_nash, _p2_nash, _val = oak.solve_matrix(
                total_value_matrix, discretize_factor=1000
            )
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


def perform_searches_and_select_move(battle: Battle, predictor: TeamPredictor) -> str:
    budget = Config.budget

    bayes = BayesianGame(battle, Config.p1_types, Config.p2_types, )

    # 2D p1 x p2 determinization — each det carries its joint probability weight
    logger.info(
        f"searching {len(dets)} determinizations (p1={getattr(Config,'p1_types',1)} x p2={getattr(Config,'p2_types',1)}) budget={budget}"
    )

    with ThreadPoolExecutor(max_workers=len(dets)) as ex:
        futures = [
            (
                ex.submit(
                    _run_oak_search,
                    det_battle,
                    det_durations,
                    det_result,
                    budget,
                    Config.eval,
                    Config.bandit,
                ),
                joint_prob,
                i,
            )
            for i, (det_battle, det_durations, det_result, joint_prob) in enumerate(
                dets
            )
        ]

    results = [(f.result(), w, i) for f, w, i in futures]
    choice = _select_move(results)
    logger.info(f"choice: {choice}")
    return choice
