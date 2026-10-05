from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor

from src.config import Config, Policy, Selection
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
import oak.search

logger = logging.getLogger(__name__)

type Strategy = dict[Policy, list[float]]


def process_policy(policy, min_val=0.0, temp=1.0) -> np.array:
    x = np.asarray(policy, dtype=float)
    if temp != 1.0:
        x = x**temp
        s = x.sum()
        if s:
            x /= s
    if min_val > 0.0:
        x = np.where(x >= min_val, x, 0.0)
        s = x.sum()
        if s:
            x /= s
    return x


# A cache slot embeds side.pokemon[i]: its species and its moves in slot order
type SlotKey = tuple[int, tuple[int, ...]]
# A whole side in side.pokemon (storage) order. Caches are only valid for this order
type TeamKey = tuple[SlotKey, ...]


def pokemon_key(pokemon: oak.Pokemon) -> SlotKey:
    return (pokemon.species, tuple(pokemon.move(i).id for i in range(4)))


def set_key(s: oak.Set) -> SlotKey:
    # equal to pokemon_key of an empty slot completed from s
    return (s.species, tuple(s.moves))


def side_key(side: oak.Side) -> TeamKey:
    return tuple(pokemon_key(side.pokemon(i)) for i in range(6))


class SideCachePool:
    """
    Side caches for one game, shared by both players and kept across turns.
    A cache is keyed by its team in storage order, since its embeddings are per
    side.pokemon index. Teams that only differ by order are found through their
    sorted key, and Player.modify fills the bench to match a cached order.
    """

    def __init__(self, eval: str, max_size: int = 128) -> None:
        self.network = oak.search.Network(oak.search.parse_eval(eval, False))
        self.max_size = max_size
        self.caches: dict[TeamKey, oak.search.SideCache] = {}
        self.orders: dict[TeamKey, list[TeamKey]] = {}
        self.last_used: dict[TeamKey, int] = {}
        self.turn = 0
        self.hits = self.misses = 0

    def new_turn(self) -> None:
        self.turn += 1
        self.hits = self.misses = 0

    def arrange(
        self, fixed: dict[int, SlotKey], free: list[int], sets: list[SlotKey]
    ) -> list[int]:
        """
        Order in which `sets` should fill the `free` storage slots so the side
        matches a cached team. Pokemon in `fixed` slots can't move.
        Returns indices into `sets`, one per free slot.
        """
        identity = list(range(len(free)))
        filled = dict(fixed)
        filled.update(zip(free, sets))
        canonical = tuple(sorted(filled.values()))
        for order in self.orders.get(canonical, []):
            if any(order[s] != key for s, key in fixed.items()):
                continue
            unused = list(range(len(sets)))
            perm = []
            for s in free:
                k = next(k for k in unused if sets[k] == order[s])
                unused.remove(k)
                perm.append(k)
            return perm
        return identity

    def get(self, side: oak.Side) -> oak.search.SideCache:
        key = side_key(side)
        cache = self.caches.get(key)
        if cache is None:
            self.misses += 1
            self._evict()
            cache = oak.search.SideCache()
            for i in range(6):
                cache.precompute(self.network, side, i)
            self.caches[key] = cache
            self.orders.setdefault(tuple(sorted(key)), []).append(key)
        else:
            self.hits += 1
        self.last_used[key] = self.turn
        return cache

    def _evict(self) -> None:
        # never evict a cache handed out this turn
        while len(self.caches) >= self.max_size:
            key = min(self.last_used, key=self.last_used.__getitem__)
            if self.last_used[key] == self.turn:
                return
            del self.caches[key], self.last_used[key]
            canonical = tuple(sorted(key))
            self.orders[canonical].remove(key)
            if not self.orders[canonical]:
                del self.orders[canonical]


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
        self.strategies: list[Strategy] = [
            {p: [0.0 for _ in range(9)] for p in Policy} for _ in range(self.n)
        ]

    def _find(self, n: int, f) -> int | None:
        return next((i for i in range(n) if f(i)), None)

    def modify(
        self, index: int, dest: oak.Side, pool: SideCachePool | None = None
    ) -> None:
        team = self.teams[index]
        order = list(dest.order)

        free = []
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
                if matching is None:
                    print(
                        f"Cant find {oak.species_id(pokemon.species)} in {team_to_string(team)}"
                    )
                assert (
                    matching is not None
                ), "Pokemon does not match any set in the team"
                oak.complete_pokemon_from_set(pokemon, team[matching])
            else:
                free.append(index)

        # team indices whose mon is not present, in team order
        missing = [
            k
            for k in range(self.team_length)
            if all(
                team[k].species != dest.pokemon(s).species
                for s in range(self.team_length)
            )
        ]
        if len(missing) < len(free):
            print(f"team: {team_to_string(team)}")
            print([oak.species_id(dest.pokemon(i).species) for i in range(6)])
        assert len(missing) >= len(
            free
        ), "Found empty slot but no sets are free to fill it"
        missing = missing[: len(free)]

        # fill the bench in an order that matches a cached team, if there is one
        perm = list(range(len(free)))
        if pool is not None and free:
            fixed = {
                s: pokemon_key(dest.pokemon(s))
                for s in range(self.team_length)
                if s not in free
            }
            perm = pool.arrange(fixed, free, [set_key(team[k]) for k in missing])
        for index, k in zip(free, perm):
            oak.complete_pokemon_from_set(dest.pokemon(index), team[missing[k]])
            order[index] = index + 1

        dest.order = order
        oak.copy_moves_to_active(dest.stored(), dest.active)


def parse_matrix_ucb(bandit: oak.search.Bandit, s: str) -> oak.search.MatrixUCB:
    """C-INTERVAL-GROW, mirrors oak's Search::Parse::matrix_ucb (minus discretize)"""
    split = s.split("-")
    assert len(split) >= 3, "matrix-ucb: C-INTERVAL-GROW"
    return oak.search.MatrixUCB(bandit, float(split[0]), int(split[1]), int(split[2]))


def output_to_dict(output: oak.search.Output) -> dict:
    """
    Flatten an oak.search.Output into the dict the rest of safari expects
    (run.py, vis, replay pickles). Output itself is not picklable, and
    Output.duration is unreadable from python (no pybind11/chrono.h in oak)
    """
    out = {
        "m": output.p1.k,
        "n": output.p2.k,
        "iterations": output.iterations,
        "initial_value": output.initial_value,
        "empirical_value": output.empirical_value,
        "nash_value": output.nash_value,
        "visit_matrix": output.visit_matrix,
        "value_matrix": output.value_matrix,
        "empirical_matrix": output.empirical_matrix,
    }
    for prefix, side in (("p1", output.p1), ("p2", output.p2)):
        out[f"{prefix}_choices"] = list(side.choices)
        out[f"{prefix}_logit"] = list(side.logit)
        out[f"{prefix}_prior"] = list(side.prior)
        out[f"{prefix}_empirical"] = list(side.empirical)
        out[f"{prefix}_nash"] = list(side.nash)
    return out


def search_cell(
    battle: oak.Battle,
    durations: oak.Durations,
    p1: Player,
    p2: Player,
    t1: int,
    t2: int,
    caches: tuple[oak.search.SideCache, oak.search.SideCache] | None = None,
) -> dict:
    """
    This information tells us the teams and also the probs
    So we can set search budget in particular
    Maybe less iterations for unlikely stuff? Etc
    More iterations for the p1 type 0 (our actual team)
    """
    start = time.perf_counter()
    # each thread parses its own eval, like oak's vs.cc
    eval = oak.search.parse_eval(Config.eval or "fp", False)
    bandit = oak.search.parse_bandit(Config.bandit)
    if Config.matrix_ucb:
        bandit = parse_matrix_ucb(bandit, Config.matrix_ucb)
        heap = oak.search.MatrixUCBNode()
    else:
        heap = oak.search.Node()
    budget = oak.search.parse_budget(Config.budget)
    kwargs = {}
    if caches is not None:
        kwargs = {"p1_cache": caches[0], "p2_cache": caches[1]}
    out = output_to_dict(
        oak.search.run(battle, durations, budget, bandit, heap, eval, **kwargs)
    )
    out["duration"] = int((time.perf_counter() - start) * 1000)
    return out


class Search:
    def __init__(
        self, b: Battle, p1: Player, p2: Player, pool: SideCachePool | None = None
    ) -> None:
        self.battle = b
        self.p1 = p1
        self.p2 = p2
        self.pool = pool

        type BattleMatrix = dict[tuple[int, int], oak.Battle]
        type OutputMatrix = dict[tuple[int, int], dict]
        self.battles: BattleMatrix = {}
        self.outputs: OutputMatrix = {}
        type CacheMatrix = dict[
            tuple[int, int], tuple[oak.search.SideCache, oak.search.SideCache]
        ]
        self.caches: CacheMatrix = {}

    def indices(self):
        return [(i, j) for i in range(self.p1.n) for j in range(self.p2.n)]

    def init_battles(self):
        for i, j in self.indices():
            b = oak.Battle(self.battle.public.bytes())
            self.p1.modify(i, b.side(0), self.pool)
            self.p2.modify(j, b.side(1), self.pool)
            self.battles[(i, j)] = b
        if self.pool is not None:
            # a side is the same for every cell of its type, so look it up once
            p1_caches = [
                self.pool.get(self.battles[(i, 0)].side(0)) for i in range(self.p1.n)
            ]
            p2_caches = [
                self.pool.get(self.battles[(0, j)].side(1)) for j in range(self.p2.n)
            ]
            for i, j in self.indices():
                b = self.battles[(i, j)]
                assert side_key(b.side(0)) == side_key(self.battles[(i, 0)].side(0))
                assert side_key(b.side(1)) == side_key(self.battles[(0, j)].side(1))
                self.caches[(i, j)] = (p1_caches[i], p2_caches[j])
            logger.debug(
                f"side caches: {self.pool.hits} reused, {self.pool.misses} computed, "
                f"{len(self.pool.caches)} held"
            )

    def run(self):
        # oak.search.run releases the GIL, so threads search in parallel
        cells = self.indices()
        # TODO re-enable once oak's side caches are thread safe
        # if self.caches and Config.parallelism > 1 and len(cells) > 1:
        #     # side caches fill in live entries during search, so concurrent
        #     # searches can only share one if oak says that's safe
        #     users: dict[int, int] = {}
        #     for pair in cells:
        #         for cache in {id(c) for c in self.caches[pair]}:
        #             users[cache] = users.get(cache, 0) + 1
        #     shared = any(n > 1 for n in users.values())
        #     assert not shared or getattr(oak.search, "side_cache_thread_safe", False), (
        #         "concurrent searches share side caches but oak's side caches are not "
        #         "thread safe; use --threads 1 or --no-side-cache"
        #     )
        search_start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=Config.parallelism) as ex:
            futures = {
                ex.submit(
                    search_cell,
                    self.battles[(i, j)],
                    self.battle.durations,
                    self.p1,
                    self.p2,
                    i,
                    j,
                    self.caches.get((i, j)),
                ): (i, j)
                for i, j in cells
            }
            for future, pair in futures.items():
                self.outputs[pair] = future.result()
        search_elapsed = time.perf_counter() - search_start
        logger.debug(
            f"oak.search.run: {len(cells)} cell(s) (max_workers={Config.parallelism}, budget={Config.budget}) "
            f"took {search_elapsed:.3f}s wall-clock"
        )

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
                # TODO this doesn't really check anything
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

        for t1 in range(self.p1.n):
            for t2 in range(self.p2.n):

                output = self.outputs[(t1, t2)]

                s1: Strategy = self.p1.strategies[t1]
                s2: Strategy = self.p2.strategies[t2]
                o1 = self.p1.omega[t1]
                o2 = self.p2.omega[t2]

                for p in Policy:
                    if p in (Policy.empirical, Policy.nash, Policy.prior):
                        for i in range(9):
                            s1[p][i] += o2 * output[f"p1_{p.name}"][i]
                            s2[p][i] += o1 * output[f"p2_{p.name}"][i]
                # p1 argmax
                for strategy, prefix in [(s1, "p1"), (s2, "p2")]:
                    s = output[f"{prefix}_empirical"]
                    argmax_idx = max(range(len(s)), key=s.__getitem__)
                    strategy[Policy.argmax] = [
                        1.0 if i == argmax_idx else 0.0 for i in range(9)
                    ]

    def solve(self) -> None:
        p1 = src.bayes_nash.Player(self.p1_actions, self.p1.omega)
        p2 = src.bayes_nash.Player(self.p2_actions, self.p2.omega)
        matrices = {}
        for i, j in self.indices():
            out = self.outputs[(i, j)]
            m = self.p1_actions[i]
            n = self.p2_actions[j]
            matrices[(i, j)] = out["empirical_matrix"][:m, :n]

        solver = src.bayes_nash.Solver(p1, p2, matrices)
        if not Config.skip_bayes:
            solve_start = time.perf_counter()
            (
                p1_avg,
                p2_avg,
                p1_cur,
                p2_cur,
            ) = solver.run(10000, 1.0, 1.0)
            solve_elapsed = time.perf_counter() - solve_start
            logger.debug(
                f"bayes_nash.Solver.run: 10000 iterations over "
                f"{self.p1.n}x{self.p2.n} type matrix took {solve_elapsed:.3f}s"
            )

            for t in range(self.p1.n):
                for i in range(self.p1_actions[t]):
                    self.p1.strategies[t][Policy.bayesian_nash][i] = p1_avg[t, i]
            for t in range(self.p2.n):
                for i in range(self.p2_actions[t]):
                    self.p2.strategies[t][Policy.bayesian_nash][i] = p2_avg[t, i]

    def parse_pkmn_choice(self, c: int, battle: PSBattle) -> str:
        side = self.battles[(0, 0)].side(0)

        choice_type = c & 3
        choice_data = c >> 2
        if choice_type == 0:
            return "/choose pass"
        elif choice_type == 1:
            if battle.fight_button:
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
