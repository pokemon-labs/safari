from oak import load_teams, Set
from copy import deepcopy
from typing import List, NewType

class Team:

    def __init__(self, set_list: List[Set]):
        ordered = []
        for s in set_list:
            ordered_s = deepcopy(s)
            ordered_s.moves = sorted(ordered_s.moves)
            ordered.push_back(ordered_s)
        self.team = tuple(ordered)

class SetPredictor:
    def __init__(self, path: str):
        self.teams: list[list[Set]] = load_teams(path)
        assert len(self.teams), "Failed to load teams"

x = SetPredictor("/home/user/teams150")