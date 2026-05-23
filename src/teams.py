from __future__ import annotations

import random
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Iterator

import oak

from src.helpers import normalize_name


@dataclass(frozen=True)
class PokemonSet:
    species: str          # normalized
    moves: tuple[str, ...]

    def __iter__(self) -> Iterator[str]:
        return iter(self.moves)


@dataclass(frozen=True)
class Team:
    sets: tuple[PokemonSet, ...]

    def __iter__(self) -> Iterator[PokemonSet]:
        return iter(self.sets)

    def __len__(self) -> int:
        return len(self.sets)

    def species_set(self) -> frozenset[str]:
        return frozenset(s.species for s in self.sets)


def _parse_teams(path: str) -> list[Team]:
    """Parse teams150 format: one team per line, sets separated by ';',
    each set is 'species move1 move2 ...'"""
    teams: list[Team] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            sets: list[PokemonSet] = []
            for chunk in line.split(";"):
                parts = chunk.strip().split()
                if not parts:
                    continue
                species = normalize_name(parts[0])
                moves = tuple(normalize_name(m) for m in parts[1:])
                sets.append(PokemonSet(species=species, moves=moves))
            if sets:
                teams.append(Team(sets=tuple(sets)))
    return teams


def _species_id(name: str) -> int:
    norm = normalize_name(name)
    for i, n in enumerate(oak.species_names):
        if normalize_name(n) == norm:
            return i
    return 0


def _move_id(name: str) -> int:
    norm = normalize_name(name)
    for i, n in enumerate(oak.move_names):
        if normalize_name(n) == norm:
            return i
    return 0


class TeamPredictor:
    """Completes an oak.Side using a corpus of known teams."""

    def __init__(self, path: str) -> None:
        self.teams: list[Team] = _parse_teams(path)
        assert self.teams, f"no teams loaded from {path!r}"

    def complete_side(self, side: oak.SideProxy, n_pokemon: int) -> oak.SideProxy:
        """Fill unrevealed slots on *side* (in-place copy returned).

        Revealed pokemon have non-zero species; unknown slots are species=0.
        Picks a random compatible team and copies its sets into the empty slots.
        """
        known: list[str] = []
        for i in range(n_pokemon):
            sp = side.pokemon(i).species
            if sp != 0:
                known.append(normalize_name(oak.species_names[sp]))

        candidates = [
            t for t in self.teams
            if all(k in t.species_set() for k in known)
        ]
        if not candidates:
            candidates = self.teams

        chosen = random.choice(candidates)

        result = deepcopy(side)
        slot = len(known)
        for pset in chosen:
            if slot >= n_pokemon:
                break
            if pset.species in {normalize_name(oak.species_names[side.pokemon(i).species])
                                 for i in range(len(known))}:
                continue
            pkmn = result.pokemon(slot)
            pkmn.species = _species_id(pset.species)
            pkmn.level = 100
            for mi, move_name in enumerate(pset.moves[:4]):
                pkmn.move(mi).id = _move_id(move_name)
                pkmn.move(mi).pp = 63
            slot += 1

        return result
