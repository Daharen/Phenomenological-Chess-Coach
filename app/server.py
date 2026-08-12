"""
Flask server -- the single console process behind the browser UI.

One process, one console window.  All engine/agent work happens here; the
browser is a thin client.  Provider (local 9B / gemini / null) can be switched
live from the UI without restarting.
"""

from __future__ import annotations

import threading
from pathlib import Path

from flask import Flask, jsonify, request, send_file

from .config import load_config, program_root
from .game.coach import ChessCoach
from .agents.base import make_client

app = Flask(__name__)
_lock = threading.Lock()
_cfg = load_config()
_coach: ChessCoach | None = None


def coach() -> ChessCoach:
    global _coach
    if _coach is None:
        _coach = ChessCoach(_cfg)
    return _coach


@app.get("/")
def index():
    return send_file(str(program_root() / "web" / "index.html"))


@app.get("/api/health")
def health():
    c = coach()
    return jsonify({
        "ok": True,
        "stockfish": c.pool.path,
        "provider": c.client.describe(),
        "level": c.level,
        "data_dir": str(_cfg.data_dir),
        "glossary_terms": len(c.glossary.terms),
    })


@app.post("/api/new")
def new_game():
    data = request.get_json(force=True, silent=True) or {}
    with _lock:
        st = coach().new_game(human_color=data.get("human_color", "white"),
                              level=data.get("level"))
    return jsonify(st)


@app.get("/api/state")
def state():
    with _lock:
        return jsonify(coach().state())


@app.get("/api/legal")
def legal():
    sq = request.args.get("square", "")
    from .engine.legality import legal_targets
    with _lock:
        return jsonify({"targets": legal_targets(coach().board, sq)})


@app.post("/api/human_move")
def human_move():
    data = request.get_json(force=True, silent=True) or {}
    with _lock:
        return jsonify(coach().human_move(data.get("uci", "")))


@app.post("/api/engine_move")
def engine_move():
    data = request.get_json(force=True, silent=True) or {}
    with _lock:
        return jsonify(coach().engine_move(mode=data.get("mode")))


@app.post("/api/analyze")
def analyze():
    data = request.get_json(force=True, silent=True) or {}
    with _lock:
        return jsonify(coach().analyze(fen=data.get("fen")))


@app.get("/api/glossary")
def glossary():
    with _lock:
        return jsonify(coach().glossary.terms)


@app.post("/api/provider")
def set_provider():
    data = request.get_json(force=True, silent=True) or {}
    prov = data.get("provider")
    import os
    if prov in ("local", "gemini", "null"):
        os.environ["CHESS_COACH_PROVIDER"] = prov
    with _lock:
        c = coach()
        c.client = make_client(_cfg)
        c.orch.client = c.client
        c.player.client = c.client
        c.evaluator.client = c.client
        c._start_keepalive()
    return jsonify({"ok": True, "provider": c.client.describe()})


def run(host: str | None = None, port: int | None = None):
    host = host or _cfg.server["host"]
    port = port or _cfg.server["port"]
    # eager init so Stockfish/errors surface in the console before the browser opens
    coach()
    try:
        from waitress import serve
        print(f"[server] waitress on http://{host}:{port}")
        serve(app, host=host, port=port, threads=8)
    except ImportError:
        print(f"[server] flask dev server on http://{host}:{port}")
        app.run(host=host, port=port, threaded=True, use_reloader=False)
