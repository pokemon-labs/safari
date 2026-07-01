"""
src/replay.py — Search-aware replay recording.

PokemonShowdown's `/savereplay` only captures the protocol log, so it can't
show what Safari was actually thinking at each decision point. This module
buffers the *full* search output (raw per-cell Output dicts, per-cell
full-info battle bytes, candidate policies, and the move actually chosen)
for every decision point in a battle, and pickles it to disk once the
battle ends.

Usage (see run.py):
    recorder = ReplayRecorder(fmt.value, Config.username, opp_name, selected_team)
    ...
    recorder.record(battle, search, pending_move, choice, Config.policy)
    ...
    recorder.finish(winner)
    path = recorder.save()

View a saved replay with the existing debug dashboard via:
    python -m src.replay_view path/to/replay.pkl
"""

from __future__ import annotations

import pickle
import time
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

import oak

from src.config import Policy

if TYPE_CHECKING:
    from src.battle import PSBattle
    from src.search import Search, Player


# ---------------------------------------------------------------------------
# Plain-Python serialization helpers
#
# oak.Set / oak.Battle / oak.Durations are pybind11 C++ objects and aren't
# guaranteed to be picklable directly. Battle/Durations round-trip through
# .bytes() / Type(bytes), which we lean on below. oak.Set has no such
# escape hatch, so we flatten it to a plain dict of its (already-plain)
# int fields instead.
# ---------------------------------------------------------------------------


def _set_to_record(s: oak.Set) -> dict:
    return {"species": s.species, "level": s.level, "moves": list(s.moves)}


def _team_to_record(team) -> list[dict]:
    return [_set_to_record(s) for s in team]


def _player_to_record(player: Player) -> dict:
    """Snapshot of a search.Player: teams, beliefs, and resulting strategies."""
    return {
        "teams": [_team_to_record(t) for t in player.teams],
        "omega": list(player.omega),
        "strategies": [
            {policy.name: list(values) for policy, values in strat.items()}
            for strat in player.strategies
        ],
    }


# ---------------------------------------------------------------------------
# Replay data model
# ---------------------------------------------------------------------------


@dataclass
class DecisionPoint:
    turn: int

    # The partial (incomplete-information) battle Safari actually saw,
    # i.e. PSBattle.public / PSBattle.durations.
    battle_bytes: bytes
    durations_bytes: bytes

    # Bayesian-game setup for this decision: teams, beliefs (omega), and the
    # resulting per-type strategies (empirical / nash / bayesian_nash / ...).
    p1: dict
    p2: dict
    p1_actions: list[int]
    p2_actions: list[int]

    # The full i x j search grid.
    # outputs[(i, j)]: raw Output dict (m, n, choices, visit/value matrices,
    #   empirical/nash/prior policies, empirical_value, nash_value, iterations, duration, ...)
    # battle_cells[(i, j)]: oak.Battle bytes for the fully-determinized battle
    #   used to produce outputs[(i, j)] — i.e. search.battles[(i, j)].bytes()
    outputs: dict[tuple[int, int], dict[str, Any]]
    battle_cells: dict[tuple[int, int], bytes]

    # What Safari actually did.
    pending_move: str  # the emitted "/choose ..." command
    pkmn_choice: int  # the raw pkmn_choice int it corresponds to
    policy_used: str  # name of the Policy used to select the move (Config.policy)


@dataclass
class Replay:
    format: str
    username: str
    opponent: str
    team: list[dict] | None
    winner: str | None = None
    decisions: list[DecisionPoint] = field(default_factory=list)


class ReplayRecorder:
    """Buffers DecisionPoints for one battle in memory; call save() once at the end."""

    def __init__(self, fmt: str, username: str, opponent: str, team) -> None:
        self.replay = Replay(
            format=fmt,
            username=username,
            opponent=opponent,
            team=_team_to_record(team) if team else None,
        )

    def record(
        self,
        battle: PSBattle,
        search: Search,
        pending_move: str,
        pkmn_choice: int,
        policy_used: Policy,
    ) -> None:
        dp = DecisionPoint(
            turn=battle.public.turn,
            battle_bytes=battle.public.bytes(),
            durations_bytes=battle.durations.bytes(),
            p1=_player_to_record(search.p1),
            p2=_player_to_record(search.p2),
            p1_actions=list(search.p1_actions),
            p2_actions=list(search.p2_actions),
            outputs=dict(search.outputs),
            battle_cells={
                pair: b.bytes() for pair, b in search.battles.items()
            },
            pending_move=pending_move,
            pkmn_choice=pkmn_choice,
            policy_used=policy_used.name,
        )
        self.replay.decisions.append(dp)

    def finish(self, winner: str | None) -> None:
        self.replay.winner = winner

    def save(self, path: str | None = None) -> str:
        if path is None:
            path = f"replay_{time.strftime('%Y%m%d-%H%M%S')}.pkl"
        with open(path, "wb") as f:
            pickle.dump(self.replay, f)
        return path
