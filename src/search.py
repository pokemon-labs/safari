from __future__ import annotations

import math
import logging
import random
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

from src.config import Config, Policy
from src.battle import PSBattle
from src.teams import TeamPredictor, Team, side_to_team
import src.bayes_nash

import numpy as np
import oak

logger = logging.getLogger(__name__)

# A "type" is a fully-revealed oak.Battle side paired with its probability weight.
type Type = tuple[Team, float]


class Player:
    """
    A proxy for BayesNash player that encaps all oak calls

    """

    def __init__(self, side: oak.Side, teams: list[Team], omega: list[float]) -> None:
        self.n = len(teams)
        assert len(omega) == self.n
        self.side: oak.Side = side
        self.teams = teams
        self.omega = omega

    def _find(self, n: int, f) -> int | None:
        return next((i for i in range(n) if f(i)), None)

    def modify(self, index: int, dest: oak.Side) -> None:
        team = self.teams[index]
        for s in team:
            assert s.species
            dest_pokemon: oak.Pokemon | None = None
            present = self._find(6, lambda i: dest.pokemon(i).species == s.species)
            if present is None:
                empty = self._find(6, lambda i: dest.pokemon(i).species == 0)
                assert (
                    empty is not None
                ), f"Failed to find empty slot for {oak.species_id(s.species)}"
                dest_pokemon = dest.pokemon(empty)
            else:
                dest_pokemon = dest.pokemon(present)
            # This handles stats, pp, etc
            oak.complete_pokemon_from_set(dest_pokemon, s)


def get_agent(p1: Player, p2: Player, t1: int, t2: int) -> Oak.Agent:
    """
    This information tells us the teams and also the probs
    So we can set search budget in particular
    Maybe less iterations for unlikely stuff? Etc
    More iterations for the p1 type 0 (our actual team)
    """
    agent = oak.Agent()
    agent.bandit = "ucb-1.0"
    agent.eval = "fp"
    agent.budget = "3000ms"
    return agent


class Search:
    def __init__(self, b: Battle, p1: Player, p2: Player) -> None:
        self.battle = b
        self.p1 = p1
        self.p2 = p2

        type BattleMatrix = dict[tuple[int, int], oak.Battle]
        type OutputMatrix = dict[tuple[int, int], oak.Output]
        self.battles: BattleMatrix = {}
        self.outputs: OutputMatrix = {}

    def indices(self):
        return [(i, j) for i in range(self.p1.n) for j in range(self.p2.n)]

    def init_battles(self):
        

    def run_searches(self):
        with ThreadPoolExecutor(max_workers=Config.paralellism) as ex:
            futures = [
                (
                    ex.submit(
                        oak.search,
                        self.battles[(i, j)],
                        battle.durations,
                        battle.result,
                        oak.Heap(),
                        get_agent(self.p1, self.p2, i, j)
                    ),
                    (i, j),
                )
                for i, j in self.indices()
            ]
        for future, pair in futures:
            self.outputs[pair] = future.result()

    def solve(self):

        p1 = src.bayes_nash.Player(

        )

        solver = src.bayles_nash.solver

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

    choice = _select_move(results)
    logger.info(f"choice: {choice}")
    return choice
