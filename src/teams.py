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
        # and the prob/exp rations from the first elem to the last is the ration_arg
        # so the logit steps are like math.log(ratio) / len(teams)
        self.policy: list[float] = ...
        # TODO SYNTAX
        assert(all(len(team) <= 6 for team in self.teams))
        assert self.teams, f"no teams loaded from {path!r}"

    def compare

    def sort_teams(self, teams: list[Team]) -> None: ...
        # assume Set is orderable from pyoak.cc function

    # TODO using set ordering (containment)
    # determine all sets in self.teams that contain the arg
    # That means lead contains lead
    # And each non lead is </contained in some non lead in the pool
    # basically, the arg is a partial observation of the team in the loaded pool
    # compile the larger teams in the pool into a list and return
    # the float return type is the normalized self.policy over the returned list
    # so its we essentially return a measure space
    def find_all_matching(side : oak.Side) -> list[tuple[Team, float]]:
        for s in 

    # we use find_all_matching to determinize
    # sometimes teams wone match
    # TODO stub out a function that determinizes from thin air

def set_to_packed(self: oak.Set) -> str:
    moves = ",".join(oak.move_id(m) for m in self.moves if m)

    return (
        f"{oak.species_id(self.species)}||||"
        f"{moves}||||||"
        f"{self.level}|"
    )

def to_packed(team: Team) -> str:
    return "]".join(set_to_packed(s) for s in team)
