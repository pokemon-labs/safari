from __future__ import annotations

import math
import random
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Iterator
from collections import defaultdict

import oak

type Team = tuple[oak.Set]
type Species = int
type Move = int

# Helpers


def set_to_packed(s: oak.Set) -> str:
    moves = ",".join(oak.move_id(m) for m in s.moves if m)
    return f"{oak.species_id(s.species)}||||" f"{moves}||||||" f"{s.level}|"


def to_packed(team: Team) -> str:
    """
    Showdown protocol, used to set our team before the Battle
    """
    return "]".join(set_to_packed(s) for s in team)


def set_to_string(s: oak.Set):
    return f"{oak.species_id(s.species)}{s.species} " + " ".join(
        [oak.move_id(m) + f"{m}" for m in s.moves]
    )


def team_to_string(team: Team):
    return "; ".join([set_to_string(s) for s in team])


def sorted_team(t: list[oak.Set]) -> tuple[oak.Set]:
    """
    The oak.Sets from oak.load_teams have sorted move sets already.
    So we just have to sort the bench by species to get uniqueness of representation
    """
    team = deepcopy(t)
    team[1:] = sorted(team[1:], key=lambda s: s.species)
    return tuple(team)


def pokemon_to_set(pokemon: oak.Pokemon) -> oak.Set:
    s = oak.Set()
    s.species = pokemon.species
    s.level = pokemon.level
    s.moves = [pokemon.move(_).id for _ in range(4)]
    return s


def side_to_team(side: oak.Side) -> Team:
    return tuple(pokemon_to_set(side.pokemon(_)) for _ in range(6))


def set_matches(s: Set, t: Set) -> bool:
    return s.species == t.species and all(m == 0 or m in t.moves for m in s.moves)


def matches(a: Team, b: Team) -> bool:
    """
    The first argument 'matches' the second, or is a partial revelation of the second
    If every pokemon is either null (we only check species)
    """
    return all(s.species == 0 or any(set_matches(s, t) for t in b) for s in a)


class SetDict:
    """
    Used to guess teams when none are found matching in the teams list
    """

    def __init__(self) -> None:
        self.sets: dict[oak.Set, float] = defaultdict(lambda: 0)

    def print(self) -> None:
        for s, p in sorted(
            [(s, p) for s, p in self.sets.items()],
            key=lambda pair: pair[1],
            reverse=True,
        ):
            print(f"{set_to_string(s)} : {p}")

    def renormalize(self) -> None:
        den = sum(p for _, p in self.sets.items())
        assert den > 0, "Attempting to renormalize a SetDict with 0 total probability"
        for s in self.sets:
            self.sets[s] /= den

    def load(self, teams: list[Team], probs: list[float]):
        assert len(teams) == len(probs)
        for index, team in enumerate(teams):
            for s in team:
                self.sets[s] += probs[index] / len(team)

    def clone(self) -> SetDict:
        other = SetDict()
        other.sets = deepcopy(self.sets)

    def remove_species(species: int):
        to_remove: list[oak.Set] = [s for s in self.sets if s.species == species]
        for s in to_remove:
            del self.sets[s]
        self.renormalize()


class TeamPredictor:

    def __init__(self, path: str, first_to_last_ratio: float = 1) -> None:
        self.teams: list[Team] = [
            tuple(sorted_team(team)) for team in oak.load_teams(path)
        ]
        assert all(len(team) <= 6 for team in self.teams)
        assert all(
            all(1 < s.species <= 151 and any(s.moves) for s in team)
            for team in self.teams
        )
        assert self.teams, f"no teams loaded from {path!r}"
        assert 0 < first_to_last_ratio <= 1
        n = len(self.teams)
        if n == 1:
            self.logits: list[float] = [0.0]
        else:
            step = math.log(first_to_last_ratio) / (n - 1)
            self.logits = [step * i for i in range(n)]
        den = sum(math.exp(l) for l in self.logits)
        self.probs = [math.exp(l) / den for l in self.logits]
        self.sets = SetDict()
        self.sets.load(self.teams, self.probs)

    def find_all_matching(self, side: oak.Side) -> list[tuple[Team, float]]:
        result = []
        t = side_to_team(side)
        for _ in range(len(self.teams)):
            team = self.teams[_]
            logit = self.logits[_]
            if matches(t, team):
                result.append((team, logit))
        return result
