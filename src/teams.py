from __future__ import annotations

import math
import random
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Iterator

import oak

type Team = tuple[oak.Set]


def sorted_team(team: list[oak.Set]) -> list[oak.Set]:
    team[1:] = sorted(team[1:], key=lambda s: s.species)
    return team


def pokemon_to_set(pokemon: oak.Pokemon) -> oak.Set:
    s = oak.Set()
    s.species = pokemon.species
    s.level = pokemon.level
    s.moves = [pokemon.move(_).id for _ in range(4)]
    return s


def side_to_team(side: oak.Side) -> Team:
    return tuple(pokemon_to_set(side.pokemon(_)) for _ in range(6))


# a is partial of b
def matches(a: Team, b: Team) -> bool:
    return all(s.species == 0 or any(s < t for t in b) for s in a)


# TODO replace all self.policy with self.logits
class TeamPredictor:

    def __init__(self, path: str, first_to_last_ratio: float = 0.01) -> None:
        self.teams: list[Team] = [
            tuple(sorted_team(team)) for team in oak.load_teams(path)
        ]
        assert all(len(team) <= 6 for team in self.teams)
        assert self.teams, f"no teams loaded from {path!r}"
        assert 0 < first_to_last_ratio <= 1

        n = len(self.teams)
        if n == 1:
            self.policy: list[float] = [0.0]
        else:
            step = math.log(first_to_last_ratio) / (n - 1)
            self.logits = [step * i for i in range(n)]

        all_sets = set()
        for team in self.teams:
            for s in team:
                all_sets.add(s)
        print(f"{len(all_sets)} unique sets found!")

    def find_all_matching(self, side: oak.Side) -> list[tuple[Team, float]]:
        result = []
        t = side_to_team(side)
        for _ in range(len(self.teams)):
            team = self.teams[_]
            logit = self.logits[_]
            if matches(t, team):
                result.append((team, logit))
        return result

    def _uniform_all(self) -> list[tuple[Team, float]]:
        """Return all teams with uniform weight (no observations)."""
        n = len(self.teams)
        return [(team, 1.0 / n) for team in self.teams]

    def _determinize_from_thin_air(
        self, observed: list[oak.Set]
    ) -> list[tuple[Team, float]]:
        """No pool team matched. Build a synthetic team from the observed pokemon
        and pad the remaining slots with randomly sampled sets from the full corpus."""
        observed_species = {p.species for p in observed}
        pool_of_sets: list[oak.Set] = [
            s for team in self.teams for s in team if s.species not in observed_species
        ]
        n_missing = 6 - len(observed)
        if n_missing > 0 and pool_of_sets:
            padding = random.sample(pool_of_sets, min(n_missing, len(pool_of_sets)))
        else:
            padding = []
        synthetic: Team = tuple(list(observed) + padding)
        return [(synthetic, 1.0)]


def set_to_packed(self: oak.Set) -> str:
    moves = ",".join(oak.move_id(m) for m in self.moves if m)
    return f"{oak.species_id(self.species)}||||" f"{moves}||||||" f"{self.level}|"


def to_packed(team: Team) -> str:
    return "]".join(set_to_packed(s) for s in team)


if __name__ == "__main__":

    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--teams",
        required=True,
    )

    args = parser.parse_args()

    predictor = TeamPredictor(args.teams, 1)

    b = oak.Battle()
    side = b.side(0)
    side.pokemon(0).species = 124
    res = predictor.find_all_matching(side)
    print(len(res), "matching teams")
