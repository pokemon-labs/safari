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
        for i, j in self.indices():
            b = oak.Battle(self.battle.public.bytes())
            self.p1.modify(i, b.side(0))
            self.p2.modify(j, b.side(1))
            self.battles[(i, j)] = b

    def run_searches(self):
        with ThreadPoolExecutor(max_workers=Config.paralellism) as ex:
            futures = [
                (
                    ex.submit(
                        oak.search,
                        self.battles[(i, j)],
                        self.battle.durations,
                        self.battle.result,
                        oak.Heap(),
                        get_agent(self.p1, self.p2, i, j)
                    ),
                    (i, j),
                )
                for i, j in self.indices()
            ]
        for future, pair in futures:
            self.outputs[pair] = future.result()

        # assert each type has a well defined number of actions
        # and that p1/p2 choices are consistent across types
        for i in range(self.p1.n):
            for j in range(self.p2.n):
                assert self.outputs[(i, j)].m == self.outputs[(i, 0)].m
                assert self.outputs[(i, j)].n == self.outputs[(0, j)].n
                ref_p1 = self.outputs[(i, 0)].p1_empirical
                ref_p2 = self.outputs[(0, j)].p2_empirical
                cur_p1 = self.outputs[(i, j)].p1_empirical
                cur_p2 = self.outputs[(i, j)].p2_empirical
                assert np.array_equal(ref_p1 > 0, cur_p1 > 0), \
                    f"p1 choices differ at ({i},{j}) vs ({i},0)"
                assert np.array_equal(ref_p2 > 0, cur_p2 > 0), \
                    f"p2 choices differ at ({i},{j}) vs (0,{j})"

        # compute p1/p2 action counts per type
        self.p1_actions = [self.outputs[(i, 0)].m for i in range(self.p1.n)]
        self.p2_actions = [self.outputs[(0, j)].n for j in range(self.p2.n)]

    def solve(self):

        p1 = src.bayes_nash.Player(self.p1_actions, self.p1.omega)
        p2 = src.bayes_nash.Player(self.p2_actions, self.p2.omega)

        # Convert each output's empirical visit_matrix into a payoff matrix for bayes_nash.Solver.
        # visit_matrix is shape (9,9) with joint visit counts; value_matrix holds p1 values.
        # We slice to [m, n] actual actions and normalize rows to get an empirical payoff estimate.
        matrices = {}
        for i, j in self.indices():
            out = self.outputs[(i, j)]
            m = self.p1_actions[i]
            n = self.p2_actions[j]
            matrices[(i, j)] = out.value_matrix[:m, :n]

        solver = src.bayes_nash.Solver(p1, p2, matrices)
        (
            p1_avg,
            p2_avg,
            p1_cur,
            p2_cur,
        ) = solver.run(10000, 1.0, 1.0)
        # These are 4 numpy arrays with padded policies over each players types
        return (p1_avg, p2_avg)

