from __future__ import annotations

import random
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Iterator

import oak

class TeamPredictor:
    """Completes an oak.Side using a corpus of known teams."""

    def __init__(self, path: str) -> None:
        self.teams: list[list[oak.Set]] = oak.load_teams(path)
        assert self.teams, f"no teams loaded from {path!r}"

def set_to_packed(self: oak.Set) -> str:
    moves = ",".join(oak.move_id(m) for m in self.moves if m)

    return (
        f"{self.nickname if hasattr(self, 'nickname') else ''}|"
        f"{oak.species_id(self.species)}||"
        f"|{moves}||||||"
        f"{self.level}||||||,,,,,"
    )

def to_packed(team: List[oak.Set]) -> str:
    return "]".join(set_to_packed(s) for s in team)
