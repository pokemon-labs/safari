from __future__ import annotations

from copy import deepcopy
from typing import List

import oak


class Team:
    """An ordered, hashable team of Sets."""

    def __init__(self, set_list: List[oak.Set]) -> None:
        ordered: list[oak.Set] = []
        for s in set_list:
            s_copy = deepcopy(s)
            s_copy.moves = sorted(s_copy.moves)
            ordered.append(s_copy)
        self.team: tuple[oak.Set, ...] = tuple(ordered)

    def __hash__(self) -> int:
        return hash(self.team)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Team) and self.team == other.team

    def __len__(self) -> int:
        return len(self.team)

    def __iter__(self):
        return iter(self.team)


class TeamPredictor:
    """Predicts/completes an oak.Side given partial information."""

    def __init__(self, path: str) -> None:
        raw: list[list[oak.Set]] = oak.load_teams(path)
        self.teams: list[Team] = [Team(sets) for sets in raw]
        assert len(self.teams), f"Failed to load teams from {path!r}"

    def complete_side(self, side: oak.Side, n_pokemon: int) -> oak.Side:
        """Fill in unknown slots on *side* using team data.

        Known pokemon (those with a non-zero species) are kept as-is.
        The remaining slots are sampled from teams that are consistent
        with the already-revealed pokemon.
        """
        known_species: list[int] = []
        for i in range(n_pokemon):
            pkmn = side.pokemon(i)
            if pkmn.species != 0:
                known_species.append(pkmn.species)

        # find compatible teams (contain all revealed species)
        candidates = [
            t for t in self.teams
            if all(
                any(s.species == sp for s in t)
                for sp in known_species
            )
        ]
        if not candidates:
            candidates = self.teams  # fallback: use all

        import random
        chosen: Team = random.choice(candidates)

        result = deepcopy(side)
        slot = len(known_species)
        for s in chosen:
            if slot >= n_pokemon:
                break
            # skip sets already represented
            if any(side.pokemon(i).species == s.species for i in range(len(known_species))):
                continue
            pkmn = result.pokemon(slot)
            pkmn.species = s.species
            pkmn.level = s.level
            for mi, move_id in enumerate(s.moves[:4]):
                pkmn.move(mi).id = move_id
            slot += 1

        return result
