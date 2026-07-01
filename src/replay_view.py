"""
src/replay_view.py — Load a saved search replay into the existing debug dashboard.

Reconstructs, per decision point, lightweight proxy objects satisfying
vis.SearchLike/vis.PlayerLike/vis.BattleLike, then feeds them into vis.py's
build_snapshot() — the exact same function DebugViz.push() calls live — so
replayed and live history entries are built by identical code and share one
Snapshot type. See vis.py's "Shared snapshot type" section for the contract.

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
from src.vis import DebugViz, Snapshot, build_snapshot as _build_snapshot


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
    """Stand-in for src.search.Search — satisfies vis.SearchLike: indices(),
    outputs, battles, battle.durations, p1/p2 (PlayerLike), p1_actions/p2_actions."""

    def __init__(self, dp: DecisionPoint) -> None:
        self.outputs = dp.outputs
        self.battles = {
            pair: oak.Battle(b) for pair, b in dp.battle_cells.items()
        }
        self._indices = list(dp.outputs.keys())
        self.battle = SimpleNamespace(durations=oak.Durations(dp.durations_bytes))
        self.p1 = _ReplayPlayer(dp.p1)
        self.p2 = _ReplayPlayer(dp.p2)
        self.p1_actions = dp.p1_actions
        self.p2_actions = dp.p2_actions

    def indices(self):
        return self._indices


class _ReplayBattle:
    """Stand-in for PSBattle — satisfies vis.BattleLike: request, last_log,
    public (+.turn), durations. public/durations are reconstructed from the
    same bytes ReplayRecorder already stored (no extra persisted fields
    needed to render the publicly-observed battle)."""

    def __init__(self, dp: DecisionPoint) -> None:
        self.request = dp.request
        self.last_log = dp.log
        self.public = oak.Battle(dp.battle_bytes)
        self.durations = oak.Durations(dp.durations_bytes)


def build_replay_snapshot(dp: DecisionPoint) -> Snapshot:
    """
    Reconstruct one dashboard-shaped Snapshot from a DecisionPoint by feeding
    proxies satisfying vis.SearchLike/BattleLike into the same build_snapshot()
    the live path uses — see vis.py's "Shared snapshot type" section.
    """
    return _build_snapshot(
        _ReplayBattle(dp),
        _ReplaySearch(dp),
        dp.pending_move,
        dp.pkmn_choice,
        dp.policy_used,
    )


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

    history = [build_replay_snapshot(dp) for dp in replay.decisions]

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
