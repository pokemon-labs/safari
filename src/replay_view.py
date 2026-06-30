"""
src/replay_view.py — Load a saved search replay into the existing debug dashboard.

Reconstructs, per decision point, lightweight proxy objects shaped just enough
like a live src.search.Search / src.search.Player so we can reuse vis.py's
existing snapshot-building helpers unmodified (_extract_cells, _team_labels,
_team_species, _omega_matrix, _player_strategies). The result is a history
list identical in shape to what DebugViz.push() produces live, so the
existing vis_dashboard.html needs no changes.

Usage:
    python -m src.replay_view path/to/replay.pkl [--port 8765]
"""

from __future__ import annotations

import argparse
import pickle
import time
from dataclasses import dataclass
from types import SimpleNamespace

import oak

from src.config import Policy
from src.replay import Replay, DecisionPoint
from src.vis import (
    DebugViz,
    _extract_cells,
    _team_labels,
    _team_species,
    _omega_matrix,
    _player_strategies,
)


@dataclass
class _FrozenSet:
    """Stand-in for oak.Set, exposing just the attributes vis.py's team
    helpers read (.species, .moves). Avoids needing a real oak.Set, which
    isn't reliably constructible/picklable round-trip outside the engine."""

    species: int
    level: int
    moves: list[int]


class _ReplayPlayer:
    """Stand-in for src.search.Player, built from a _player_to_record() dict."""

    def __init__(self, record: dict) -> None:
        self.teams = [
            [_FrozenSet(**s) for s in team] for team in record["teams"]
        ]
        self.omega = record["omega"]
        self.strategies = [
            {Policy[name]: list(values) for name, values in strat.items()}
            for strat in record["strategies"]
        ]
        self.n = len(self.teams)


class _ReplaySearch:
    """Stand-in for src.search.Search, built from one DecisionPoint. Only
    exposes what _extract_cells() needs: indices(), outputs, battles,
    battle.durations."""

    def __init__(self, dp: DecisionPoint) -> None:
        self.outputs = dp.outputs
        self.battles = {
            pair: oak.Battle(b) for pair, b in dp.battle_cells.items()
        }
        self._indices = list(dp.outputs.keys())
        self.battle = SimpleNamespace(durations=oak.Durations(dp.durations_bytes))

    def indices(self):
        return self._indices


def build_snapshot(dp: DecisionPoint) -> dict:
    """Reconstruct one DebugViz-shaped snapshot dict from a DecisionPoint,
    matching what DebugViz.push() builds live."""
    search = _ReplaySearch(dp)
    p1 = _ReplayPlayer(dp.p1)
    p2 = _ReplayPlayer(dp.p2)

    p1_labels = _team_labels(p1)
    p2_labels = _team_labels(p2)
    p1_species = _team_species(p1)
    p2_species = _team_species(p2)
    probs = _omega_matrix(p1.omega, p2.omega)
    cells = _extract_cells(search)

    return {
        "p1_types": p1_labels,
        "p2_types": p2_labels,
        "p1_teams": p1_species,
        "p2_teams": p2_species,
        "p1_omega": list(p1.omega),
        "p2_omega": list(p2.omega),
        "probs": probs,
        "cells": cells,
        "p1_strategies": _player_strategies(p1, dp.p1_actions),
        "p2_strategies": _player_strategies(p2, dp.p2_actions),
        "pending_move": dp.pending_move,
        "selected_choice": dp.pkmn_choice,
        "policy_used": dp.policy_used,
        "turn": dp.turn,
        "search_ready": True,
    }


def load_replay(path: str) -> Replay:
    with open(path, "rb") as f:
        return pickle.load(f)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Path to a .pkl replay saved by ReplayRecorder")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--no-open", action="store_true", help="Don't auto-open a browser tab"
    )
    args = parser.parse_args()

    replay = load_replay(args.path)
    print(
        f"Loaded replay: {replay.username} vs {replay.opponent} "
        f"({replay.format}), {len(replay.decisions)} decisions, "
        f"winner={replay.winner}"
    )

    history = [build_snapshot(dp) for dp in replay.decisions]

    viz = DebugViz(port=args.port, auto_open=not args.no_open)
    viz.load_history(history)
    viz.start()

    print(f"dashboard at http://localhost:{args.port}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
