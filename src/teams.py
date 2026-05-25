from __future__ import annotations

import random
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Iterator

import oak

type Team = tuple[oak.Set]

class TeamPredictor:
    """Completes an oak.Side using a corpus of known teams."""

    def __init__(self, path: str, first_to_last_ratio: float = .01) -> None:
        self.teams: list[tuple[oak.Set]] = oak.load_teams(path)
        # TODO add policy member list[float] with same length
        # make it an actual prob distro that corresponds to each team having a logit
        # and the logits descend from the lead in an affine fashion
        # and the prob/exp ratio from the first elem to the last is the ratio_arg
        # so the logit steps are like math.log(ratio) / len(teams)
        self.policy: list[float] = []  # stub; uniform until implemented
        assert all(len(team) <= 6 for team in self.teams)
        assert self.teams, f"no teams loaded from {path!r}"

    def compare(self, a: oak.Set, b: oak.Set) -> bool:
        # assume Set is orderable/comparable from pyoak.cc
        ...

    def sort_teams(self, teams: list[Team]) -> None:
        # assume Set is orderable from pyoak.cc function
        ...

    # TODO using set ordering (containment):
    # determine all sets in self.teams that contain the arg.
    # That means lead contains lead,
    # and each non-lead is contained in some non-lead in the pool.
    # The arg is a partial observation of the team in the loaded pool.
    # Compile the larger teams in the pool into a list and return.
    # The float return is the normalized self.policy over the returned list
    # (i.e. a measure space over matching teams).
    def find_all_matching(self, side: oak.Side) -> list[tuple[Team, float]]:
        # TODO implement containment check
        ...

    # we use find_all_matching to determinize
    # sometimes teams won't match
    # TODO stub out a function that determinizes from thin air


def set_to_packed(s: oak.Set) -> str:
    moves = ",".join(oak.move_id(m) for m in s.moves if m)

    return (
        f"{oak.species_id(s.species)}||||"
        f"{moves}|||||"
        f"{s.level}|"
    )

def to_packed(team: Team) -> str:
    return "]".join(set_to_packed(s) for s in team)
