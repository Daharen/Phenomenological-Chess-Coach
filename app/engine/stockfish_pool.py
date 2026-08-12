"""
Thin, thread-safe wrapper around an existing Stockfish binary.

We *connect in* to the user's already-installed engine (per config) instead of
downloading one.  A single engine process is guarded by a lock; a small LRU-ish
cache keyed on (fen, depth, multipv) avoids re-searching the same node many
times within a turn.
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from typing import Any

import chess
import chess.engine

MATE = 100_000  # centipawn value used to fold mate scores into a single integer


class StockfishPool:
    def __init__(self, candidates: list[str], threads: int = 2, hash_mb: int = 256,
                 cache_size: int = 4096):
        self.path = self._first_existing(candidates)
        if self.path is None:
            raise FileNotFoundError(
                "No Stockfish binary found. Checked: " + " | ".join(candidates)
            )
        self._lock = threading.RLock()
        self._engine: chess.engine.SimpleEngine | None = None
        self._threads = threads
        self._hash = hash_mb
        self._cache: OrderedDict[tuple, list[dict]] = OrderedDict()
        self._cache_size = cache_size

    @staticmethod
    def _first_existing(candidates: list[str]) -> str | None:
        for c in candidates:
            if c and os.path.exists(c):
                return c
        # allow a bare command name resolvable on PATH (e.g. "stockfish")
        for c in candidates:
            if c and os.sep not in c and "/" not in c:
                return c
        return None

    def _ensure(self) -> chess.engine.SimpleEngine:
        if self._engine is None:
            self._engine = chess.engine.SimpleEngine.popen_uci(self.path)
            try:
                self._engine.configure({"Threads": self._threads, "Hash": self._hash})
            except chess.engine.EngineError:
                pass
        return self._engine

    # ---- core query ----------------------------------------------------------
    def analyse(self, board: chess.Board, depth: int | None = None,
                movetime: float | None = None, multipv: int = 1) -> list[dict]:
        """Return a list (len == multipv) of {'pv', 'score', 'depth'} dicts.

        'score' is a chess.engine.PovScore relative to the side to move in board.
        Results are cached on (fen, depth, movetime, multipv).
        """
        key = (board._transposition_key() if hasattr(board, "_transposition_key")
               else board.fen(), depth, movetime, multipv)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached

            limit = chess.engine.Limit(depth=depth, time=movetime)
            eng = self._ensure()
            infos = eng.analyse(board, limit, multipv=max(1, multipv))
            if isinstance(infos, dict):
                infos = [infos]
            out: list[dict] = []
            for info in infos:
                pv = info.get("pv") or []
                out.append({
                    "pv": list(pv),
                    "score": info.get("score"),
                    "depth": info.get("depth"),
                })
            self._cache[key] = out
            if len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
            return out

    # ---- convenience ---------------------------------------------------------
    def best_move(self, board: chess.Board, depth: int | None = 12,
                  movetime: float | None = None) -> chess.Move | None:
        infos = self.analyse(board, depth=depth if movetime is None else None,
                             movetime=movetime, multipv=1)
        pv = infos[0]["pv"]
        return pv[0] if pv else None

    def top_moves(self, board: chess.Board, depth: int | None = 12, n: int = 3,
                  movetime: float | None = None) -> list[dict]:
        """Top-n candidate moves as {'move', 'cp', 'mate', 'pv'} (mover pov)."""
        infos = self.analyse(board, depth=depth if movetime is None else None,
                             movetime=movetime, multipv=n)
        mover = board.turn
        out = []
        for info in infos:
            if not info["pv"]:
                continue
            pov = info["score"].pov(mover)
            out.append({
                "move": info["pv"][0],
                "cp": pov.score(mate_score=MATE),
                "mate": pov.mate(),
                "pv": info["pv"],
            })
        return out

    def eval_cp(self, board: chess.Board, depth: int | None = 12,
                pov: chess.Color | None = None, movetime: float | None = None) -> int:
        """Signed centipawn eval (mate folded to +/-MATE) from `pov` (default: white)."""
        infos = self.analyse(board, depth=depth if movetime is None else None,
                             movetime=movetime, multipv=1)
        score = infos[0]["score"]
        color = pov if pov is not None else chess.WHITE
        return score.pov(color).score(mate_score=MATE)

    def close(self) -> None:
        with self._lock:
            if self._engine is not None:
                try:
                    self._engine.quit()
                except chess.engine.EngineError:
                    pass
                self._engine = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
