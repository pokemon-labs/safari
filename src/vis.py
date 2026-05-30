"""
debug_viz.py — Pokémon battle debug visualizer
FastAPI + WebSocket, opens a browser tab automatically.

Usage:
    from debug_viz import DebugViz

    viz = DebugViz(port=8765)
    viz.start()   # spawns server thread + opens browser

    # in your battle loop:
    viz.update(
        p1_types=["Starmie", "Alakazam"],
        p2_types=["Gengar", "Tauros", "Snorlax"],
        probs=[[0.18, 0.22, 0.07], [0.09, 0.14, 0.11]],   # m×n list/ndarray
        cells={
            "0,1": {
                "value": 0.42,
                "visits": 512,
                "depth": 6,
                "q": 0.38,
                "policy": [{"name": "Surf", "prob": 0.6},
                            {"name": "Blizzard", "prob": 0.4}],
                "battle_repr": "Turn 12\\n...",
            }
        },
        pending_move="/choose move 1",   # what the bot WOULD send
    )

    # Block until the user picks a move (only when manual mode is on):
    move = viz.get_move_override()   # returns None if auto mode
    if move:
        send_to_showdown(move)
    else:
        send_to_showdown(pending_move)

Dependencies:
    pip install fastapi uvicorn websockets
"""

import asyncio
import json
import threading
import webbrowser
from typing import Optional

import numpy as np
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

# ---------------------------------------------------------------------------
# HTML dashboard (single-file, no build step)
# ---------------------------------------------------------------------------

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>battle debug</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg: #0e0e10; --bg2: #18181b; --bg3: #222226;
  --border: #2e2e35; --border2: #404048;
  --text: #e8e8ec; --muted: #888892; --dim: #555560;
  --blue: #378ADD; --blue-dim: #1a3a5c; --blue-bright: #85B7EB;
  --green: #3dd68c; --green-dim: #0d3320;
  --amber: #f0a030; --red: #e24b4a;
  --mono: 'Cascadia Code', 'Fira Code', 'JetBrains Mono', monospace;
  --sans: system-ui, sans-serif;
}
html, body { height: 100%; background: var(--bg); color: var(--text); font-family: var(--sans); font-size: 13px; }
#root { display: flex; height: 100vh; overflow: hidden; }

/* ── LEFT PANEL ── */
#left { width: 320px; min-width: 200px; display: flex; flex-direction: column; border-right: 1px solid var(--border); }
#left-top { flex: 1; overflow: auto; padding: 10px; }
#left-bottom { padding: 10px; border-top: 1px solid var(--border); font-size: 11px; color: var(--muted); }

.panel-title { font-size: 10px; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); margin-bottom: 8px; }

#matrix-wrap { overflow: auto; }
#matrix { display: grid; gap: 2px; }

.ax-corner { font-size: 9px; color: var(--dim); display: flex; align-items: flex-end; justify-content: flex-end; padding: 2px; }
.ax-col { font-size: 9px; color: var(--muted); text-align: center; padding: 2px 3px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ax-row { font-size: 9px; color: var(--muted); writing-mode: vertical-lr; text-align: center; padding: 2px; overflow: hidden; text-overflow: ellipsis; }

.cell {
  border: 1px solid var(--border); border-radius: 4px;
  padding: 4px 5px; cursor: pointer;
  background: var(--bg2); transition: border-color .12s, background .12s;
  display: flex; flex-direction: column; gap: 2px; min-width: 0;
}
.cell:hover { border-color: var(--border2); background: var(--bg3); }
.cell.active { border-color: var(--blue); background: var(--blue-dim); }
.cell .c-prob { font-size: 11px; font-weight: 600; font-family: var(--mono); color: var(--text); }
.cell .c-heat { height: 3px; border-radius: 2px; }

/* ── RIGHT PANEL ── */
#right { flex: 1; display: flex; flex-direction: column; min-width: 0; }
#search-panel { flex: 1; padding: 12px 14px; overflow: auto; border-bottom: 1px solid var(--border); }
#battle-panel { height: 210px; display: flex; flex-direction: column; }
#battle-header-row { display: flex; align-items: center; justify-content: space-between; padding: 7px 12px; border-bottom: 1px solid var(--border); flex-shrink: 0; }
#battle-repr { flex: 1; overflow: auto; padding: 10px 12px; font-family: var(--mono); font-size: 11px; white-space: pre; line-height: 1.65; color: var(--text); background: var(--bg2); }

/* stats */
#stats-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 7px; margin-bottom: 12px; }
.stat-card { background: var(--bg2); border: 1px solid var(--border); border-radius: 6px; padding: 8px 10px; }
.stat-card .s-label { font-size: 10px; color: var(--muted); margin-bottom: 2px; }
.stat-card .s-val { font-size: 20px; font-weight: 600; font-family: var(--mono); color: var(--text); }
.stat-card .s-sub { font-size: 10px; color: var(--dim); }

/* policy bars */
.pb-section { margin-bottom: 12px; }
.pbar-row { display: flex; align-items: center; gap: 7px; margin-bottom: 3px; }
.pbar-name { font-size: 10px; color: var(--muted); width: 90px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: var(--mono); }
.pbar-track { flex: 1; height: 11px; background: var(--bg3); border-radius: 3px; overflow: hidden; }
.pbar-fill { height: 100%; border-radius: 3px; background: var(--blue); transition: width .3s; }
.pbar-pct { font-size: 10px; color: var(--muted); width: 34px; text-align: right; font-family: var(--mono); }

/* manual move control */
#move-ctrl { display: flex; align-items: center; gap: 8px; }
#manual-toggle { appearance: none; width: 32px; height: 18px; border-radius: 9px; background: var(--bg3); border: 1px solid var(--border2); cursor: pointer; position: relative; transition: background .2s; flex-shrink: 0; }
#manual-toggle:checked { background: var(--amber); border-color: var(--amber); }
#manual-toggle::after { content: ''; position: absolute; top: 2px; left: 2px; width: 12px; height: 12px; border-radius: 50%; background: var(--muted); transition: left .2s, background .2s; }
#manual-toggle:checked::after { left: 16px; background: #fff; }
#toggle-label { font-size: 11px; color: var(--muted); }
#pending-move { font-family: var(--mono); font-size: 11px; color: var(--dim); margin-left: auto; }

#manual-input-row { display: flex; gap: 6px; padding: 6px 12px 8px; align-items: center; border-top: 1px solid var(--border); background: var(--bg); display: none; }
#manual-input-row.visible { display: flex; }
#move-input { flex: 1; background: var(--bg2); border: 1px solid var(--border2); border-radius: 4px; color: var(--text); font-family: var(--mono); font-size: 12px; padding: 5px 8px; outline: none; }
#move-input:focus { border-color: var(--amber); }
#send-btn { background: var(--amber); border: none; border-radius: 4px; color: #1a1000; font-weight: 600; font-size: 12px; padding: 5px 12px; cursor: pointer; white-space: nowrap; }
#send-btn:hover { filter: brightness(1.1); }
#send-btn:active { filter: brightness(.9); }

/* status bar */
#status { font-size: 10px; color: var(--dim); }
.dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; margin-right: 4px; background: var(--dim); }
.dot.live { background: var(--green); }
.dot.manual { background: var(--amber); }

#no-sel { display: flex; align-items: center; justify-content: center; height: 100%; color: var(--dim); font-size: 12px; }
</style>
</head>
<body>
<div id="root">
  <div id="left">
    <div id="left-top">
      <div class="panel-title">type belief matrix <span id="dim-lbl" style="float:right;text-transform:none;letter-spacing:0"></span></div>
      <div id="matrix-wrap"><div id="matrix"></div></div>
    </div>
    <div id="left-bottom">
      <div id="status"><span class="dot" id="dot"></span><span id="status-txt">connecting…</span></div>
    </div>
  </div>

  <div id="right">
    <div id="search-panel" id="search-panel">
      <div class="panel-title" id="search-title">search statistics</div>
      <div id="right-content"><div id="no-sel">← select a type pair</div></div>
    </div>
    <div id="battle-panel">
      <div id="battle-header-row">
        <div class="panel-title" style="margin:0">battle state</div>
        <div id="move-ctrl">
          <input type="checkbox" id="manual-toggle">
          <label id="toggle-label" for="manual-toggle">manual</label>
          <span id="pending-move"></span>
        </div>
      </div>
      <div id="battle-repr" style="color:#555560">no battle loaded</div>
      <div id="manual-input-row">
        <input id="move-input" type="text" placeholder="/choose move 1" autocomplete="off">
        <button id="send-btn">send</button>
      </div>
    </div>
  </div>
</div>

<script>
const state = { sel: null, cells: {}, p1Types: [], p2Types: [], probs: [], pendingMove: '', manual: false };
let ws, reconnTimer;

const $= id => document.getElementById(id);

function probColor(p) {
  if (p < .05) return '#1a3a5c';
  if (p < .15) return '#185FA5';
  if (p < .30) return '#378ADD';
  if (p < .50) return '#85B7EB';
  return '#B5D4F4';
}

function buildMatrix() {
  const { p1Types: p1, p2Types: p2, probs } = state;
  const mat = $('matrix');
  const m = p1.length, n = p2.length;
  if (!m || !n) return;
  $('dim-lbl').textContent = `${m}×${n}`;
  const cw = Math.max(44, Math.min(80, Math.floor(220/n)));
  mat.style.gridTemplateColumns = `22px ${Array(n).fill(cw+'px').join(' ')}`;
  mat.innerHTML = '';

  const corner = document.createElement('div');
  corner.className = 'ax-corner'; corner.textContent = 'p1\\p2'; mat.appendChild(corner);
  p2.forEach(t => { const d = document.createElement('div'); d.className='ax-col'; d.textContent=t; d.title=t; mat.appendChild(d); });

  p1.forEach((t1, i) => {
    const rl = document.createElement('div'); rl.className='ax-row'; rl.textContent=t1; rl.title=t1; mat.appendChild(rl);
    p2.forEach((_, j) => {
      const p = (probs[i]||[])[j] || 0;
      const key = `${i},${j}`;
      const cell = document.createElement('div');
      cell.className = 'cell' + (state.sel===key?' active':'');
      cell.dataset.key = key;
      cell.innerHTML = `<div class="c-prob">${(p*100).toFixed(1)}%</div><div class="c-heat" style="background:${probColor(p)}"></div>`;
      cell.addEventListener('click', () => selectCell(key));
      mat.appendChild(cell);
    });
  });
}

function selectCell(key) {
  state.sel = key;
  document.querySelectorAll('.cell').forEach(c => c.classList.toggle('active', c.dataset.key===key));
  renderRight();
}

function renderRight() {
  const key = state.sel;
  if (!key) return;
  const [i,j] = key.split(',').map(Number);
  const d = state.cells[key] || {};
  const t1 = state.p1Types[i]||`p1[${i}]`, t2 = state.p2Types[j]||`p2[${j}]`;
  const prob = (state.probs[i]||[])[j] || 0;
  $('search-title').textContent = `${t1} vs ${t2}  ·  p=${(prob*100).toFixed(2)}%`;

  const policy = d.policy || [];
  const policyHtml = policy.length ? `
    <div class="pb-section">
      <div class="panel-title">policy</div>
      ${policy.map(p=>`
        <div class="pbar-row">
          <div class="pbar-name" title="${p.name}">${p.name}</div>
          <div class="pbar-track"><div class="pbar-fill" style="width:${(p.prob*100).toFixed(1)}%"></div></div>
          <div class="pbar-pct">${(p.prob*100).toFixed(1)}%</div>
        </div>`).join('')}
    </div>` : '';

  $('right-content').innerHTML = `
    <div id="stats-grid">
      <div class="stat-card"><div class="s-label">value</div><div class="s-val">${d.value!=null?Number(d.value).toFixed(3):'—'}</div><div class="s-sub">expected outcome</div></div>
      <div class="stat-card"><div class="s-label">visits</div><div class="s-val">${d.visits!=null?d.visits:'—'}</div><div class="s-sub">node visits</div></div>
      <div class="stat-card"><div class="s-label">depth</div><div class="s-val">${d.depth!=null?d.depth:'—'}</div><div class="s-sub">search depth</div></div>
      <div class="stat-card"><div class="s-label">q</div><div class="s-val">${d.q!=null?Number(d.q).toFixed(3):'—'}</div><div class="s-sub">q-value</div></div>
    </div>
    ${policyHtml}`;

  $('battle-repr').textContent = d.battle_repr || '(no battle repr for this cell)';
  $('battle-repr').style.color = d.battle_repr ? '' : '#555560';
}

// manual move toggle
$('manual-toggle').addEventListener('change', e => {
  state.manual = e.target.checked;
  $('manual-input-row').classList.toggle('visible', state.manual);
  $('dot').className = 'dot' + (state.manual?' manual':' live');
  if (ws && ws.readyState===1) ws.send(JSON.stringify({type:'manual_mode', enabled: state.manual}));
});

$('send-btn').addEventListener('click', sendMove);
$('move-input').addEventListener('keydown', e => { if (e.key==='Enter') sendMove(); });

function sendMove() {
  const val = $('move-input').value.trim();
  if (!val || !ws || ws.readyState!==1) return;
  ws.send(JSON.stringify({type:'move_override', move: val}));
  $('move-input').value = '';
}

function updatePendingMove(move) {
  state.pendingMove = move;
  $('pending-move').textContent = move ? `bot: ${move}` : '';
}

// WebSocket
function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => {
    $('dot').className = 'dot' + (state.manual?' manual':' live');
    $('status-txt').textContent = 'connected';
    clearTimeout(reconnTimer);
  };
  ws.onmessage = e => {
    const msg = JSON.parse(e.data);
    if (msg.type==='init' || msg.type==='update') {
      if (msg.p1_types) state.p1Types = msg.p1_types;
      if (msg.p2_types) state.p2Types = msg.p2_types;
      if (msg.probs) state.probs = msg.probs;
      if (msg.cells) Object.assign(state.cells, msg.cells);
      if (msg.pending_move !== undefined) updatePendingMove(msg.pending_move);
      buildMatrix();
      if (state.sel) renderRight();
      $('status-txt').textContent = `updated ${new Date().toLocaleTimeString()}`;
    }
  };
  ws.onclose = () => {
    $('dot').className = 'dot';
    $('status-txt').textContent = 'disconnected — retrying…';
    reconnTimer = setTimeout(connect, 2000);
  };
  ws.onerror = () => ws.close();
}
connect();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# DebugViz class
# ---------------------------------------------------------------------------


class DebugViz:
    """
    Lightweight debug visualizer for a Bayesian Pokémon battle agent.

    Parameters
    ----------
    port : int
        Port to serve on (default 8765).
    auto_open : bool
        Open a browser tab on start() (default True).
    """

    def __init__(self, port: int = 8765, auto_open: bool = True):
        self.port = port
        self.auto_open = auto_open

        self._data: dict = {
            "p1_types": [],
            "p2_types": [],
            "probs": [],
            "cells": {},
            "pending_move": "",
        }
        self._lock = threading.Lock()
        self._clients: set[WebSocket] = set()

        # move override flow
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
        """Start the server in a background thread and open the browser."""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        # give uvicorn a moment to bind
        threading.Event().wait(0.8)
        if self.auto_open:
            webbrowser.open(f"http://localhost:{self.port}")

    def update(
        self,
        p1_types: list = None,
        p2_types: list = None,
        probs=None,  # list[list] or np.ndarray, shape m×n
        cells: dict = None,  # {"{i},{j}": {...}}
        pending_move: str = None,
    ):
        """
        Push new data to all connected browser clients.
        Call this from your battle loop after each search.
        """
        with self._lock:
            if p1_types is not None:
                self._data["p1_types"] = list(p1_types)
            if p2_types is not None:
                self._data["p2_types"] = list(p2_types)
            if probs is not None:
                if isinstance(probs, np.ndarray):
                    probs = probs.tolist()
                self._data["probs"] = probs
            if cells is not None:
                self._data["cells"].update(cells)
            if pending_move is not None:
                self._data["pending_move"] = pending_move

            payload = json.dumps({"type": "update", **self._data})

        if self._loop:
            asyncio.run_coroutine_threadsafe(self._broadcast(payload), self._loop)

    def get_move_override(self) -> Optional[str]:
        """
        Call this instead of sending the bot's move automatically.
        Returns None immediately if manual mode is off.
        Blocks until the user submits a move if manual mode is on.
        """
        if not self._manual_mode:
            return None
        self._move_event.clear()
        self._move_event.wait()  # blocks until browser sends a move
        move = self._move_override
        self._move_override = None
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
                # send current state immediately on connect
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
        for ws in list(self._clients):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        config = uvicorn.Config(
            self._app,
            host="127.0.0.1",
            port=self.port,
            log_level="error",  # silent — don't spam your terminal
            loop="asyncio",
        )
        server = uvicorn.Server(config)
        self._loop.run_until_complete(server.serve())


# ---------------------------------------------------------------------------
# Quick smoke-test:  python debug_viz.py
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import time, math, random

    viz = DebugViz(port=8765)
    viz.start()
    print("Visualizer running at http://localhost:8765  (Ctrl-C to stop)")

    p1 = ["Starmie", "Alakazam", "Lapras"]
    p2 = ["Gengar", "Tauros", "Snorlax", "Chansey"]
    moves = ["Blizzard", "Surf", "Psychic", "Thunder Wave", "Recover"]

    t = 0
    while True:
        t += 1
        m, n = len(p1), len(p2)
        raw = [
            [abs(math.sin(t * 0.1 + i + j * 0.7)) for j in range(n)] for i in range(m)
        ]
        total = sum(sum(r) for r in raw)
        probs = [[v / total for v in row] for row in raw]

        cells = {}
        for i in range(m):
            for j in range(n):
                policy_raw = [random.random() for _ in moves]
                ps = sum(policy_raw)
                cells[f"{i},{j}"] = {
                    "value": math.sin(t * 0.05 + i - j) * 0.8,
                    "visits": 100 + t * 3 + i * 10,
                    "depth": 4 + (t % 6),
                    "q": math.cos(t * 0.07 + j) * 0.5,
                    "policy": [
                        {"name": mv, "prob": policy_raw[k] / ps}
                        for k, mv in enumerate(moves)
                    ],
                    "battle_repr": (
                        f"Turn {t}  —  {p1[i]} vs {p2[j]}\n\n"
                        f"p1: {p1[i]}\n"
                        f"  hp: {max(10, 383 - t*2 - i*5)}/383  status: —\n"
                        f"  last move: {moves[t % len(moves)]}\n\n"
                        f"p2: {p2[j]}\n"
                        f"  hp: {max(10, 353 - t*3 - j*4)}/353  status: par\n"
                        f"  last move: Body Slam\n\n"
                        f"available: {' / '.join(moves[:4])}"
                    ),
                }

        pending = f"/choose move {(t % 4) + 1}"
        viz.update(
            p1_types=p1, p2_types=p2, probs=probs, cells=cells, pending_move=pending
        )

        # demonstrate manual override
        override = viz.get_move_override()
        if override:
            print(f"[manual] user sent: {override}")
        else:
            print(f"[auto]   bot sends: {pending}")

        time.sleep(0.5)
