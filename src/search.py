from __future__ import annotations

import logging
from concurrent.futures import ProcessPoolExecutor

from src.config import Config, Policy
from src.battle import PSBattle
from src.teams import (
    TeamPredictor,
    Team,
    side_to_team,
    team_to_string,
    set_to_string,
    Probability,
)
import src.bayes_nash

import numpy as np
import oak

logger = logging.getLogger(__name__)


class Player:
    """
    A proxy for BayesNash player that encaps all oak calls
    """

    def __init__(
        self, side: oak.Side, teams: list[Team], omega: list[Probability]
    ) -> None:
        self.n = len(teams)
        assert len(omega) == self.n
        self.side = side
        self.teams = teams
        self.omega = omega
        self.team_length = 6

    def _find(self, n: int, f) -> int | None:
        return next((i for i in range(n) if f(i)), None)

    def modify(self, index: int, dest: oak.Side) -> None:
        team = self.teams[index]
        order = list(dest.order)

        for slot in range(1, self.team_length + 1):
            index = slot - 1
            i = order[index]
            if i:
                pokemon = dest.pokemon(i - 1)
                assert pokemon.species, "Empty pokemon has non-zero slot tihngy"
                # find set
                matching = self._find(
                    self.team_length, lambda i: team[i].species == pokemon.species
                )
                assert (
                    matching is not None
                ), "Pokemon does not match any set in the team"
                oak.complete_pokemon_from_set(pokemon, team[matching])
            else:
                first_missing_set_index = self._find(
                    self.team_length,
                    lambda i: all(
                        team[i].species != dest.pokemon(k).species
                        for k in range(self.team_length)
                    ),
                )  # find first team index where mon is not present
                assert (
                    first_missing_set_index is not None
                ), "Found empty slot but no sets are free to fill it"
                pokemon = dest.pokemon(index)
                oak.complete_pokemon_from_set(pokemon, team[first_missing_set_index])
                order[index] = slot

        dest.order = order
        oak.copy_moves_to_active(dest.stored(), dest.active)


def get_agent(p1: Player, p2: Player, t1: int, t2: int) -> Oak.Agent:
    """
    This information tells us the teams and also the probs
    So we can set search budget in particular
    Maybe less iterations for unlikely stuff? Etc
    More iterations for the p1 type 0 (our actual team)
    """
    bandit = Config.bandit
    eval = Config.eval
    budget = Config.budget
    matrix_ucb = ""
    return (budget, bandit, eval, matrix_ucb)


# we need this wrapper for ProcessPool pickling
def _oak_search_mp(*args):
    return oak.search_mp(*args)


class Search:
    def __init__(self, b: Battle, p1: Player, p2: Player) -> None:
        self.battle = b
        self.p1 = p1
        self.p2 = p2

        type BattleMatrix = dict[tuple[int, int], oak.Battle]
        type OutputMatrix = dict[tuple[int, int], dict]
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

    def run(self):
        import pickle

        pickle.dumps(_oak_search_mp)
        with ProcessPoolExecutor(max_workers=4) as ex:
            futures = {
                ex.submit(
                    _oak_search_mp,
                    self.battles[(i, j)].bytes(),
                    self.battle.durations.bytes(),
                    *get_agent(self.p1, self.p2, i, j),
                ): (i, j)
                for i, j in self.indices()
            }

        for future, pair in futures.items():
            out = future.result()
            visit = np.asarray(out["visit_matrix"], dtype=np.float64)
            value = np.asarray(out["value_matrix"], dtype=np.float64)
            empirical = np.divide(
                value,
                visit,
                out=np.full_like(value, 0.5, dtype=np.float64),
                where=visit > 0,
            )
            out["empirical_matrix"] = empirical
            self.outputs[pair] = out

        # assert each type has a well defined number of actions
        # and that p1/p2 choices are consistent across types
        for i in range(self.p1.n):
            for j in range(self.p2.n):
                assert self.outputs[(i, j)]["m"] == self.outputs[(i, 0)]["m"]
                assert self.outputs[(i, j)]["n"] == self.outputs[(0, j)]["n"]
                ref_p1 = self.outputs[(i, 0)]["p1_choices"]
                ref_p2 = self.outputs[(0, j)]["p2_choices"]
                cur_p1 = self.outputs[(i, j)]["p1_choices"]
                cur_p2 = self.outputs[(i, j)]["p2_choices"]
                assert np.array_equal(
                    np.asarray(ref_p1) > 0,
                    np.asarray(cur_p1) > 0,
                ), f"p1 choices differ at ({i},{j}) vs ({i},0)"
                assert np.array_equal(
                    np.asarray(ref_p2) > 0,
                    np.asarray(cur_p2) > 0,
                ), f"p2 choices differ at ({i},{j}) vs (0,{j})"

        self.p1_actions = [self.outputs[(i, 0)]["m"] for i in range(self.p1.n)]
        self.p2_actions = [self.outputs[(0, j)]["n"] for j in range(self.p2.n)]

    def solve(self):
        p1 = src.bayes_nash.Player(self.p1_actions, self.p1.omega)
        p2 = src.bayes_nash.Player(self.p2_actions, self.p2.omega)
        matrices = {}
        for i, j in self.indices():
            out = self.outputs[(i, j)]
            m = self.p1_actions[i]
            n = self.p2_actions[j]
            matrices[(i, j)] = out["empirical_matrix"][:m, :n]

        solver = src.bayes_nash.Solver(p1, p2, matrices)
        (
            p1_avg,
            p2_avg,
            p1_cur,
            p2_cur,
        ) = solver.run(10000, 1.0, 1.0)
        return (p1_avg, p2_avg)

    def parse_pkmn_choice(self, c: int) -> str:
        side = self.battles[(0, 0)].side(0)

        choice_type = c & 3
        choice_data = c >> 2
        if choice_type == 0:
            return "/choose pass"
        elif choice_type == 1:
            # TODO this happens with binding, but I think showdown still expects move 1?
            if choice_data == 0:
                return "/choose move 1"

            # is forced
            vol = side.active.volatiles()
            if vol.recharging or vol.rage or vol.thrashing or vol.charging:
                assert (
                    choice_data == 1
                ), "is_forced but choice data is not one. Likely a parsing error of the actives"
                return "/choose move 1"

            # struggle
            if not any(
                side.active.move(i).id and side.active.move(i).pp for i in range(4)
            ):
                return "/choose move 1"

            move = side.active.move(choice_data - 1)
            # e.g. /choose move psychic
            assert move.pp > 0, "Using move with no pp"
            return f"/choose move {oak.move_id(move.id)}"
        elif choice_type == 2:
            assert 1 < choice_data <= 6
            index = side.order[choice_data - 1] - 1
            assert index >= 0, f"Bad switch : {choice_data}"
            species = side.pokemon(index).species
            assert species, "Trying to switch to empty slot (no species anyway)"
            return f"/choose switch {oak.species_id(species)}"
        else:
            assert (
                False
            ), f"Could not parse pkmn_choice: {c} = {choice_type}, {choice_data}"
