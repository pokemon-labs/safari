from __future__ import annotations

import math
import logging
import random
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

from src.config import Config, Policy
from src.battle import Battle
from src.teams import TeamPredictor, Team, side_to_team

import numpy as np
import oak

logger = logging.getLogger(__name__)

# A "type" is a fully-revealed oak.Battle side paired with its probability weight.
type Type = tuple[Team, float]


class BayesianGame:

    def __init__(self, battle: Battle, p1_k: int, p2_k: int, predictor: TeamPredictor):
        self.p1_k = p1_k
        self.p2_k = p2_k
        if p1_k == 1:
            self.p1_types: list[Type] = [(side_to_team(battle.private.side(0)), 1.0)]
        else:
            real_weight: float = .2
            not_real_weight: float = 1 - real_weight
            extras = self._get_n_types(battle.public.side(0), predictor, p1_k - 1, not_real_weight)
            self.p1_types = [(side_to_team(battle.private), real_weight)] + extras
        self.p2_types: list[Type] = self._get_n_types(
            battle.public.side(1), predictor, p2_k
        )

    def flatten(self) -> list[oak.Battle, int, int]:

        for i in range(self.p1_k)

    def _get_n_types(
        self, side: oak.Side, predictor: TeamPredictor, n: int, weight: float = 1
    ) -> list[Type]:
        matches: list[tuple[Team, float]] = predictor.find_all_matching(side)[:n]
        # If fewer matches than requested, pad with random fallback teams.
        while len(matches) < n:
            fallback = random.choice(predictor.teams)
            matches.append((fallback, matches[-1][1] if matches else 0.0))
        den: float = sum(math.exp(logit) for _, logit in matches)
        return [
            (team, weight * math.exp(logit) / den)
            for team, logit in matches
        ]

    def _fill_side_from_team(
        self, revealed: oak.Side, team: Team, max_pokemon: int = 6
    ) -> oak.Side:
        """
        Returns a copy of `revealed` with any empty slots filled in from `team`.
        Slots whose species already match a team set are also completed with
        that set's moves/level. Slots with species==0 are filled with the
        next unmatched set from the team.
        """
        side = deepcopy(revealed)
        for s in team[:max_pokemon]:
            empty_or_species_match: int | None = None
            for i in range(6):
                species: int = side.pokemon(i).species
                if species == s.species:
                    # Exact match — complete it.
                    empty_or_species_match = i
                    break
                if species == 0 and empty_or_species_match is None:
                    empty_or_species_match = i
            if empty_or_species_match is None:
                # Side is already full; skip this set.
                continue
            pkmn = side.pokemon(empty_or_species_match)
            pkmn.species = s.species
            pkmn.level = s.level
            for mi, move_id in enumerate(s.moves[:4]):
                pkmn.move(mi).id = move_id
        return side


# ---------------------------------------------------------------------------
# BayesianOutput — collects per-determinization oak.Output results and
# aggregates them into a single weighted payoff matrix for Nash solving.
# TODO: wire up with bayes_nash.py once that module is ready.
# ---------------------------------------------------------------------------


class BayesianOutput:
    """
    Accumulates oak.Output results from each (p1_type, p2_type) cell and
    exposes a weighted aggregate for move selection.
    """

    def __init__(self, p1_k: int, p2_k: int):
        self.p1_k = p1_k
        self.p2_k = p2_k
        # Stores (output, joint_weight) tuples in insertion order.
        self._entries: list[tuple[oak.Output, float]] = []

    def add_output(
        self,
        p1_type_index: int,
        p2_type_index: int,
        p1_type_prob: float,
        p2_type_prob: float,
        output: oak.Output,
    ):
        joint_weight = p1_type_prob * p2_type_prob
        self._entries.append((output, joint_weight))

    def solve(self) -> list[tuple[oak.Output, float, int]]:
        """Return entries as (output, weight, index) for _select_move."""
        return [(out, w, i) for i, (out, w) in enumerate(self._entries)]


# ---------------------------------------------------------------------------
# Oak search helpers
# ---------------------------------------------------------------------------


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

    bayes = BayesianGame(battle, Config.p1_types, Config.p2_types, predictor)

    # flatten() gives the 2D p1 x p2 determinization grid as a flat list.
    # Each entry is (p1_side, p2_side, joint_prob).
    flat = bayes.flatten()

    logger.info(
        f"searching {len(flat)} determinizations "
        f"(p1={Config.p1_types} x p2={Config.p2_types}) budget={budget}"
    )

    # Build fully-determinized oak.Battle + oak.Durations per cell.
    dets: list[tuple[oak.Battle, oak.Durations, int, float]] = []
    for (p1_side, p), (p2_side, q), joint_prob in flat:
        # Construct a determinized oak.Battle from the public battle, overwriting
        # both sides with the sampled/known sides.
        det_battle = oak.Battle(battle.public.bytes())
        # Side 0 = us (p1), side 1 = opponent (p2).
        # Copy the order arrays so the active slot is correct.
        det_battle.side(0).order = p1_side.order
        det_battle.side(1).order = p2_side.order
        for slot in range(6):
            src = p1_side.pokemon(slot)
            dst = det_battle.side(0).pokemon(slot)
            dst.species = src.species
            dst.level = src.level
            dst.hp = src.hp
            dst.status = src.status
            for mi in range(4):
                dst.move(mi).id = src.move(mi).id
                dst.move(mi).pp = src.move(mi).pp
            for slot2 in range(6):
                src2 = p2_side.pokemon(slot2)
                dst2 = det_battle.side(1).pokemon(slot2)
                dst2.species = src2.species
                dst2.level = src2.level
                dst2.hp = src2.hp
                dst2.status = src2.status
                for mi in range(4):
                    dst2.move(mi).id = src2.move(mi).id
                    dst2.move(mi).pp = src2.move(mi).pp
        det_durations = oak.Durations(battle.durations.bytes())
        det_result = 0  # ongoing battle
        dets.append((det_battle, det_durations, det_result, joint_prob))

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
