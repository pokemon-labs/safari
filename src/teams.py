from __future__ import annotations

import math
import random
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Iterator

import oak

type Team = tuple[oak.Set]

class TeamPredictor:
    """Completes an oak.Side using a corpus of known teams."""

    def __init__(self, path: str, first_to_last_ratio: float = 0.01) -> None:
        self.teams: list[tuple[oak.Set]] = oak.load_teams(path)
        assert all(len(team) <= 6 for team in self.teams)
        assert self.teams, f"no teams loaded from {path!r}"

        # Policy is a prob distribution over teams.
        # Each team has a logit; logits descend from first to last in an affine
        # fashion such that exp(logit[0]) / exp(logit[-1]) == first_to_last_ratio.
        # logit step = log(first_to_last_ratio) / (len - 1)  (negative, descending)
        n = len(self.teams)
        if n == 1:
            self.policy: list[float] = [1.0]
        else:
            step = math.log(first_to_last_ratio) / (n - 1)
            logits = [step * i for i in range(n)]
            max_l = logits[0]
            exps = [math.exp(l - max_l) for l in logits]
            total = sum(exps)
            self.policy = [e / total for e in exps]

    # oak.Set supports == comparison (from pyoak.cc)
    def compare(self, a: oak.Set, b: oak.Set) -> bool:
        return a == b

    def sort_teams(self, teams: list[Team]) -> None:
        """Sort in-place using oak.Set ordering (species then moves)."""
        teams.sort(key=lambda team: [s.species for s in team])

    # Using set ordering (containment):
    # A pool team "contains" the observed side if:
    #   - pool[0].species == observed lead species
    #   - every other revealed species on the observed side is present
    #     in some non-lead slot of the pool team
    # The arg is a partial observation (revealed pokemon only).
    # Returns a probability-weighted list of matching teams (measure space).
    def find_all_matching(self, side: oak.Side) -> list[tuple[Team, float]]:
        observed: list[oak.Set] = []
        for i in range(6):
            pkmn = side.pokemon(i)
            if pkmn.species == 0:
                break
            observed.append(pkmn)

        if not observed:
            return self._uniform_all()

        observed_lead_species = observed[0].species
        observed_rest_species = {p.species for p in observed[1:]}

        matches: list[tuple[int, Team]] = []  # (original_index, team)
        for idx, team in enumerate(self.teams):
            if not team:
                continue
            if team[0].species != observed_lead_species:
                continue
            pool_rest_species = {s.species for s in team[1:]}
            if not observed_rest_species.issubset(pool_rest_species):
                continue
            matches.append((idx, team))

        if not matches:
            return self._determinize_from_thin_air(observed)

        raw_weights = [self.policy[idx] for idx, _ in matches]
        total = sum(raw_weights)
        return [(team, w / total) for (_, team), w in zip(matches, raw_weights)]

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
            s
            for team in self.teams
            for s in team
            if s.species not in observed_species
        ]
        n_missing = 6 - len(observed)
        if n_missing > 0 and pool_of_sets:
            padding = random.sample(pool_of_sets, min(n_missing, len(pool_of_sets)))
        else:
            padding = []
        synthetic: Team = tuple(list(observed) + padding)
        return [(synthetic, 1.0)]


def set_to_packed(s: oak.Set) -> str:
    moves = ",".join(oak.move_id(m) for m in s.moves if m)

    return (
        f"{oak.species_id(s.species)}||||"
        f"{moves}|||||"
        f"{s.level}|"
    )

def to_packed(team: Team) -> str:
    return "]".join(set_to_packed(s) for s in team)
