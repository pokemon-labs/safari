from oak import load_teams, Set
from copy import deepcopy
from typing import List, NewType

# oak.load_teams returns list[list[Oak.set]]
# this class should made hashable
# It should be ordered for validity, use OOP for this
# Ordering means each sets movesets is ordrered and each set after the first is in order (first elem is the lead)

class Team:

    def __init__(self, set_list: List[Set]):
        ordered = []
        for s in set_list:
            ordered_s = deepcopy(s)
            ordered_s.moves = sorted(ordered_s.moves)
            ordered.push_back(ordered_s)
        self.team = tuple(ordered)

class TeamPredictor:
    def __init__(self, path: str):
        unordered_teams : list[list[Set]] = load_teams(path)
        self.teams = # TODO
        assert len(self.teams), "Failed to load teams"

    def complete_side(self, side : oak.Side, n_pokemon: int) -> oak.Side: ...

# this works rn
x = SetPredictor("teams150")