"""
src/vis.py — Safari debug visualizer.

Serves a browser dashboard at http://localhost:8765 showing the m×n Bayesian
type belief matrix and per-cell search stats + battle state.

Usage (from run.py):
    viz = DebugViz()
    viz.start()

    # after search.run() + search.solve():
    viz.push(battle, search, pending_move)

    # instead of sending the move directly:
    override = viz.get_move_override()   # None if auto; blocks if manual toggle on
    actual_move = override or pending_move
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import webbrowser
from typing import Optional, Protocol, TypedDict, Any

import numpy as np
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

import oak
from src.teams import team_to_string
from src.config import Policy

# Load HTML from sibling file so this module stays readable
_HTML_PATH = os.path.join(os.path.dirname(__file__), "vis_dashboard.html")
with open(_HTML_PATH) as _f:
    _HTML = _f.read()


# ---------------------------------------------------------------------------
# Data extraction helpers
# ---------------------------------------------------------------------------


def _choice_name(choice: int, battle: oak.Battle) -> str:
    """Turn a raw pkmn_choice int into a human-readable string."""
    side = battle.side(0)
    ctype = choice & 3
    cdata = choice >> 2
    if ctype == 0:
        return "pass"
    elif ctype == 1:
        if cdata == 0:
            return "move 1 (forced)"
        move = side.active.move(cdata - 1)
        return oak.move_id(move.id) if move.id else f"move {cdata}"
    elif ctype == 2:
        idx = side.order[cdata - 1] - 1
        if idx >= 0:
            sp = side.pokemon(idx).species
            return f"switch {oak.species_id(sp)}" if sp else f"switch slot {cdata}"
    return f"choice({choice})"


def _battle_state(battle) -> dict:
    """Structured snapshot of the active pokemon on each side for sprite display."""

    def side_info(side_idx: int) -> dict:
        side = battle.side(side_idx)
        mon = side.stored()  # currently active (stored = the active slot)
        sp = mon.species
        stats = mon.stats()
        return {
            "species_num": int(sp),
            "species_id": oak.species_id(sp).lower() if sp else "",
            "hp": int(mon.hp),
            "maxhp": int(stats.hp),
            "status": mon.status_name() if mon.hp > 0 else "fnt",
        }

    return {"p1": side_info(0), "p2": side_info(1)}


def _player_strategies(
    player: PlayerLike, action_counts: list[int]
) -> list[dict[str, list[float]]]:
    """Per-type strategies: list indexed by type, each a dict of policy_name -> list[float]."""
    result = []
    for t, strat in enumerate(player.strategies):
        m = action_counts[t] if t < len(action_counts) else 9
        entry = {}
        for policy, values in strat.items():
            entry[policy.name] = [float(v) for v in values[:m]]
        result.append(entry)
    return result


def _extract_cells(search: SearchLike) -> dict:
    """
    Build the cells dict from a completed Search.

    p1_nash / p2_nash come from search.solve() — they are per-type mixed strategies,
    so p1_nash[i] is the nash strategy for p1 type i, length p1_actions[i].
    """
    cells = {}
    for i, j in search.indices():
        out = search.outputs[(i, j)]
        battle = search.battles[(i, j)]

        m = out["m"]
        n = out["n"]
        p1_choices = out["p1_choices"][:m]
        p2_choices = out["p2_choices"][:n]

        p1_action_names = [oak.choice_label(battle.side(0), c) for c in p1_choices]
        p2_action_names = [oak.choice_label(battle.side(1), c) for c in p2_choices]

        # Both matrices are plain list[list] from pybind11 (std::array<std::array<...>>)
        raw_val = out.get("value_matrix")
        raw_vis = out.get("visit_matrix")

        # Normalize value by visits; None where unvisited (visits == 0)
        em_list: list[list[float | None]] = []
        vm_list: list[list[int]] = []
        for ri in range(m):
            ev_row, vis_row = [], []
            for ci in range(n):
                v = raw_vis[ri][ci] if raw_vis is not None else 0
                vis_row.append(int(v))
                if v == 0 or raw_val is None:
                    ev_row.append(None)
                else:
                    ev_row.append(raw_val[ri][ci] / v)
            em_list.append(ev_row)
            vm_list.append(vis_row)

        def _trim(arr, length) -> list[float]:
            if arr is None:
                return []
            return [float(x) for x in list(arr)[:length]]

        cells[f"{i},{j}"] = {
            "empirical_value": float(out.get("empirical_value", 0.0)),
            "nash_value": float(out.get("nash_value", 0.0)),
            "iterations": int(out.get("iterations", 0)),
            "duration_ms": int(out.get("duration", 0)),
            "p1_action_names": p1_action_names,
            "p2_action_names": p2_action_names,
            "p1_nash": _trim(out.get("p1_nash"), m),
            "p2_nash": _trim(out.get("p2_nash"), n),
            "p1_empirical": _trim(out.get("p1_empirical"), m),
            "p2_empirical": _trim(out.get("p2_empirical"), n),
            "p1_prior": _trim(out.get("p1_prior"), m),
            "p2_prior": _trim(out.get("p2_prior"), n),
            "empirical_matrix": em_list,
            "visit_matrix": vm_list,
            "battle_repr": oak.battle_string(battle, search.battle.durations),
            "battle_state": _battle_state(battle),
        }
    return cells


def _omega_matrix(p1_omega: list[float], p2_omega: list[float]) -> list[list[float]]:
    """Outer product of the two marginal belief vectors → joint probability matrix."""
    return [[p1 * p2 for p2 in p2_omega] for p1 in p1_omega]


def _team_labels(player: PlayerLike) -> list[str]:
    """Short label for each type (Team) — lead species + bench count."""
    labels = []
    for team in player.teams:
        lead = oak.species_id(team[0].species) if team and team[0].species else "???"
        rest = [oak.species_id(s.species) for s in team[1:] if s.species]
        label = lead if not rest else f"{lead}+{len(rest)}"
        labels.append(label)
    return labels


def _team_species(player: PlayerLike) -> list[list[dict]]:
    """All species for each team: [{num, id, moves: [move_id, ...]}, ...]."""
    result = []
    for team in player.teams:
        slot_list = []
        for s in team:
            if not s.species:
                continue
            moves = []
            for mi in range(4):
                mv = s.moves[mi]
                if mv:
                    moves.append(oak.move_id(mv))
            slot_list.append(
                {
                    "num": s.species,
                    "id": oak.species_id(s.species),
                    "moves": moves,
                }
            )
        result.append(slot_list)
    return result


# ---------------------------------------------------------------------------
# Shared snapshot type
#
# One Snapshot dict is what actually reaches the dashboard (S.history in the
# JS), built by build_snapshot() below. Both the live path (DebugViz.push,
# fed real Search/Player/PSBattle objects) and the replay path
# (src.replay_view, fed lightweight proxy objects built from a pickled
# DecisionPoint) construct their inputs to satisfy these same Protocols, so
# build_snapshot() runs unmodified either way — no duplicated dict literals.
#
# This is deliberately structural (Protocol), not nominal: neither
# src.search.Search/Player nor the replay-side proxies need to inherit from
# anything here. It's just a static contract for what build_snapshot() reads.
# ---------------------------------------------------------------------------


class TeamMemberLike(Protocol):
    species: int
    level: int
    moves: list[int]


class PlayerLike(Protocol):
    teams: list[list[TeamMemberLike]]
    omega: list[float]
    strategies: list[dict[Policy, list[float]]]


class DurationsHolderLike(Protocol):
    durations: Any  # oak.Durations or a compatible stand-in


class SearchLike(Protocol):
    p1: PlayerLike
    p2: PlayerLike
    p1_actions: list[int]
    p2_actions: list[int]
    outputs: dict[tuple[int, int], dict]
    battles: dict[tuple[int, int], Any]  # oak.Battle or a compatible stand-in
    battle: DurationsHolderLike

    def indices(self) -> list[tuple[int, int]]: ...


class BattleLike(Protocol):
    request: dict | None
    last_log: list[str]
    public: Any  # needs .turn


class Snapshot(TypedDict):
    p1_types: list[str]
    p2_types: list[str]
    p1_teams: list[list[dict]]
    p2_teams: list[list[dict]]
    p1_omega: list[float]
    p2_omega: list[float]
    probs: list[list[float]]
    cells: dict[str, dict]
    p1_strategies: list[dict[str, list[float]]]
    p2_strategies: list[dict[str, list[float]]]
    pending_move: str
    pkmn_choice: int | None
    request: dict | None
    log: list[str]
    policy_used: str
    turn: int
    search_ready: bool


def build_snapshot(
    battle: BattleLike,
    search: SearchLike,
    pending_move: str,
    pkmn_choice: int | None,
    policy_used: str,
) -> Snapshot:
    """
    Build the one dashboard-facing Snapshot for a completed decision point.
    Used identically by the live path (DebugViz.push) and the replay path
    (src.replay_view.build_replay_snapshot) — see module docstring above.
    """
    return Snapshot(
        p1_types=_team_labels(search.p1),
        p2_types=_team_labels(search.p2),
        p1_teams=_team_species(search.p1),
        p2_teams=_team_species(search.p2),
        p1_omega=list(search.p1.omega),
        p2_omega=list(search.p2.omega),
        probs=_omega_matrix(search.p1.omega, search.p2.omega),
        cells=_extract_cells(search),
        p1_strategies=_player_strategies(search.p1, search.p1_actions),
        p2_strategies=_player_strategies(search.p2, search.p2_actions),
        pending_move=pending_move,
        pkmn_choice=pkmn_choice,
        request=battle.request,
        log=list(battle.last_log),
        policy_used=policy_used,
        turn=battle.public.turn,
        search_ready=True,
    )


# ---------------------------------------------------------------------------
# DebugViz
# ---------------------------------------------------------------------------


class DebugViz:
    @staticmethod
    def _default_data() -> dict:
        return {
            "p1_types": [],
            "p2_types": [],
            "p1_teams": [],
            "p2_teams": [],
            "p1_omega": [],
            "p2_omega": [],
            "probs": [],
            "cells": {},
            "p1_strategies": [],
            "p2_strategies": [],
            "pending_move": "",
            "pkmn_choice": None,
            "request": None,
            "log": [],
            "policy_used": "",
            "turn": 0,
            "search_ready": False,
        }

    def __init__(self, port: int = 8765, auto_open: bool = False):
        self.port = port
        self.auto_open = auto_open

        self._data: dict = self._default_data()
        self._history: list[dict] = []
        self._lock = threading.Lock()
        self._clients: set[WebSocket] = set()

        self._manual_mode = False
        self._move_event = threading.Event()
        self._move_override: Optional[str] = None

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._app = self._build_app()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_history(self, history: list[dict]) -> None:
        """
        Preload a full history (e.g. reconstructed from a saved replay file) so
        clients connecting to start() see it immediately via the 'init' message.
        Call before start().
        """
        with self._lock:
            self._history = list(history)
            if history:
                self._data.update(history[-1])

    def reset(self) -> None:
        """
        Clear all history — call at the start of a new battle so the live
        dashboard doesn't bleed together multiple games run in one process.
        Replays are unaffected (each gets its own ReplayRecorder already);
        this only resets the long-lived live-view state. Manual-mode is left
        alone since it's a UI preference, not battle data.
        """
        with self._lock:
            self._history = []
            self._data = self._default_data()
            payload = json.dumps({"type": "reset"})

        if self._loop:
            asyncio.run_coroutine_threadsafe(self._broadcast(payload), self._loop)

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        threading.Event().wait(0.8)
        if self.auto_open:
            webbrowser.open(f"http://localhost:{self.port}")

    def push_battle(self, battle, search_p1, search_p2) -> None:
        """
        Call this immediately after battle.update() — before search starts.
        Pushes a battle-only snapshot: left-panel data only, no search results.
        search_p1 / search_p2 are Player objects (for team/omega info).
        """
        p1_labels = _team_labels(search_p1)
        p2_labels = _team_labels(search_p2)
        p1_species = _team_species(search_p1)
        p2_species = _team_species(search_p2)
        probs = _omega_matrix(search_p1.omega, search_p2.omega)

        snapshot: Snapshot = Snapshot(
            p1_types=p1_labels,
            p2_types=p2_labels,
            p1_teams=p1_species,
            p2_teams=p2_species,
            p1_omega=list(search_p1.omega),
            p2_omega=list(search_p2.omega),
            probs=probs,
            cells={},  # no search data yet
            p1_strategies=[],  # no search data yet
            p2_strategies=[],
            pending_move="",
            pkmn_choice=None,  # not decided yet
            request=battle.request,
            log=list(battle.last_log),
            policy_used="",  # not decided yet
            turn=battle.public.turn,
            search_ready=False,
        )

        with self._lock:
            self._data.update(snapshot)
            self._history.append(snapshot)
            payload = json.dumps(
                {
                    "type": "update",
                    "snapshot": snapshot,
                    "history_len": len(self._history),
                }
            )

        if self._loop:
            asyncio.run_coroutine_threadsafe(self._broadcast(payload), self._loop)

    def push(
        self,
        battle: BattleLike,
        search: SearchLike,
        pending_move: str,
        pkmn_choice: int,
        policy_used: str = "",
    ):
        """
        Call this after search.run() + search.solve() to push live data to the browser.
        Amends the most recent battle snapshot (from push_battle) with search results.
        pkmn_choice is the raw int Safari selected (before formatting into pending_move).
        """
        snapshot: Snapshot = build_snapshot(
            battle, search, pending_move, pkmn_choice, policy_used
        )

        with self._lock:
            self._data.update(snapshot)
            # Amend the last history entry in-place if it was a battle-only snapshot
            # for the same turn; otherwise append fresh.
            if (
                self._history
                and not self._history[-1].get("search_ready")
                and self._history[-1].get("turn") == battle.public.turn
            ):
                self._history[-1].update(snapshot)
                payload = json.dumps(
                    {
                        "type": "amend",
                        "snapshot": snapshot,
                        "history_len": len(self._history),
                    }
                )
            else:
                self._history.append(snapshot)
                payload = json.dumps(
                    {
                        "type": "update",
                        "snapshot": snapshot,
                        "history_len": len(self._history),
                    }
                )

        if self._loop:
            asyncio.run_coroutine_threadsafe(self._broadcast(payload), self._loop)

    def get_move_override(self) -> Optional[str]:
        """
        Returns None immediately in auto mode.
        Blocks until the user submits a move in manual mode.
        """
        if not self._manual_mode:
            return None
        self._move_event.clear()
        self._move_event.wait()
        move, self._move_override = self._move_override, None
        return move

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_app(self) -> FastAPI:
        app = FastAPI()

        @app.get("/", response_class=HTMLResponse)
        async def index():
            return _HTML

        @app.websocket("/ws")
        async def ws_endpoint(websocket: WebSocket):
            await websocket.accept()
            self._clients.add(websocket)
            try:
                with self._lock:
                    init_payload = json.dumps(
                        {
                            "type": "init",
                            "history": self._history,
                            "history_len": len(self._history),
                            **self._data,
                        }
                    )
                await websocket.send_text(init_payload)

                async for raw in websocket.iter_text():
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if msg.get("type") == "manual_mode":
                        self._manual_mode = bool(msg.get("enabled", False))
                    elif msg.get("type") == "move_override":
                        move = msg.get("move", "").strip()
                        if move:
                            self._move_override = move
                            self._move_event.set()
            except WebSocketDisconnect:
                pass
            finally:
                self._clients.discard(websocket)

        return app

    async def _broadcast(self, payload: str):
        dead = set()
        for client in list(self._clients):
            try:
                await client.send_text(payload)
            except Exception:
                dead.add(client)
        self._clients -= dead

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        config = uvicorn.Config(
            self._app,
            host="127.0.0.1",
            port=self.port,
            log_level="error",
            loop="asyncio",
        )
        server = uvicorn.Server(config)
        self._loop.run_until_complete(server.serve())
