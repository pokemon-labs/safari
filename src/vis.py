"""
src/vis.py — Safari debug visualizer.

Serves a browser dashboard at http://localhost:8765 showing the m×n Bayesian
type belief matrix and per-cell search stats + battle state.

Usage (from run.py):
    viz = DebugViz()
    viz.start()

    # after search.run() + search.solve():
    viz.push(battle, search, p1_nash, p2_nash, pending_move)

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
from typing import Optional

import numpy as np
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

import oak
from src.teams import team_to_string
from src.search import Search

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


def _extract_cells(
    search: Search, p1_nash: list[list[float]], p2_nash: list[list[float]]
) -> dict:
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

        # p1_action_names = [_choice_name(c, battle) for c in p1_choices]
        # p2_action_names = [_choice_name(c, battle) for c in p2_choices]
        p1_action_names = [oak.choice_label(battle.side(0), c) for c in p1_choices]
        p2_action_names = [oak.choice_label(battle.side(1), c) for c in p2_choices]

        # nash slices — already trimmed to m/n by solve()
        p1_n = list(p1_nash[i]) if i < len(p1_nash) else []
        p2_n = list(p2_nash[j]) if j < len(p2_nash) else []

        empirical_matrix = out.get("empirical_matrix")
        em_list = (
            empirical_matrix[:m, :n].tolist() if empirical_matrix is not None else []
        )
        visit_matrix = out.get("visit_matrix")
        # print(empirical_matrix, visit_matrix)
        # print(type(empirical_matrix), type(visit_matrix)) # np.array, list
        vm_list = visit_matrix[:m, :n].tolist() if visit_matrix is not None else []

        cells[f"{i},{j}"] = {
            "empirical_value": float(out.get("empirical_value", 0.0)),
            "nash_value": float(out.get("nash_value", 0.0)),
            "iterations": int(out.get("iterations", 0)),
            "duration_ms": int(out.get("duration_ms", 0)),
            "p1_action_names": p1_action_names,
            "p2_action_names": p2_action_names,
            "p1_nash": p1_n,
            "p2_nash": p2_n,
            "empirical_matrix": em_list,
            "visit_matrix": vm_list,
            "battle_repr": oak.battle_string(battle, search.battle.durations),
        }
    return cells


def _omega_matrix(p1_omega: list[float], p2_omega: list[float]) -> list[list[float]]:
    """Outer product of the two marginal belief vectors → joint probability matrix."""
    return [[p1 * p2 for p2 in p2_omega] for p1 in p1_omega]


def _team_labels(player) -> list[str]:
    """Short label for each type (Team) — lead species + bench count."""
    labels = []
    for team in player.teams:
        lead = oak.species_id(team[0].species) if team and team[0].species else "???"
        rest = [oak.species_id(s.species) for s in team[1:] if s.species]
        label = lead if not rest else f"{lead}+{len(rest)}"
        labels.append(label)
    return labels


def _team_species(player) -> list[list[str]]:
    """All species IDs for each team, in slot order."""
    return [
        [oak.species_id(s.species) for s in team if s.species] for team in player.teams
    ]


# ---------------------------------------------------------------------------
# DebugViz
# ---------------------------------------------------------------------------


class DebugViz:
    def __init__(self, port: int = 8765, auto_open: bool = True):
        self.port = port
        self.auto_open = auto_open

        self._data: dict = {
            "p1_types": [],
            "p2_types": [],
            "p1_teams": [],
            "p2_teams": [],
            "p1_omega": [],
            "p2_omega": [],
            "probs": [],
            "cells": {},
            "pending_move": "",
            "turn": 0,
        }
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

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        threading.Event().wait(0.8)
        if self.auto_open:
            webbrowser.open(f"http://localhost:{self.port}")

    def push(
        self,
        battle,  # PSBattle — for turn number
        search: Search,
        p1_nash: list,  # from search.solve() — list of per-type strategies
        p2_nash: list,
        pending_move: str,
    ):
        """
        Call this after search.run() + search.solve() to push live data to the browser.
        """
        p1_labels = _team_labels(search.p1)
        p2_labels = _team_labels(search.p2)
        p1_species = _team_species(search.p1)
        p2_species = _team_species(search.p2)
        probs = _omega_matrix(search.p1.omega, search.p2.omega)
        cells = _extract_cells(search, p1_nash, p2_nash)

        with self._lock:
            self._data.update(
                {
                    "p1_types": p1_labels,
                    "p2_types": p2_labels,
                    "p1_teams": p1_species,
                    "p2_teams": p2_species,
                    "p1_omega": list(search.p1.omega),
                    "p2_omega": list(search.p2.omega),
                    "probs": probs,
                    "cells": cells,
                    "pending_move": pending_move,
                    "turn": battle.public.turn,
                }
            )
            payload = json.dumps({"type": "update", **self._data})

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
                    init_payload = json.dumps({"type": "init", **self._data})
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
