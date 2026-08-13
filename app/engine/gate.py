"""
Deterministic per-candidate GATE (pure python-chess, no engine).

Absolute, per-move checks used to ELIMINATE bad candidates DURING establishment
-- before they ever fill one of the three candidate slots -- rather than
reviewing a move after it is chosen. Three checks, any of which flags:

  * vision   -- the move leaves one of our pieces attacked and undefended
                (a full hang), via engine.vision.hanging_after. Value-blind for
                now (per the standing decision: "just avoid full hangs").
  * material -- the move loses >= hang_threshold by static exchange (SEE), via
                engine.deteval.move_safety (catches defended-but-losing trades).
  * fork     -- the move walks into a fork/double-attack >= warn_threshold, via
                engine.threats.move_threat.

A flagged candidate is not automatically failed: the proposing agent gets one
appeal (see game/coach.py). This module only produces the deterministic verdict.
"""
from __future__ import annotations

import chess

from .vision import hanging_after
from .deteval import move_safety
from .threats import move_threat


def gate_candidate(board: chess.Board, move: chess.Move,
                   hang_threshold: int = 100, warn_threshold: int = 150,
                   checks=("vision", "material", "fork")) -> dict:
    """Verdict for a single legal candidate `move` in `board` (before the move).
    Returns {flagged, reason, kinds:[...], facts:{...}}."""
    reasons: list[str] = []
    kinds: list[str] = []
    facts: dict = {}

    if "vision" in checks:
        hung = hanging_after(board, move)
        if hung:
            names = ", ".join(f"{h['type']} on {h['square']}" for h in hung)
            reasons.append(f"leaves your {names} attacked and undefended")
            kinds.append("vision_hang")
            facts["hung"] = hung

    if "material" in checks:
        ms = move_safety(board, move)
        if ms["loss_cp"] >= hang_threshold and "vision_hang" not in kinds:
            reasons.append(ms["sentence"] or f"loses ~{ms['loss_cp']}cp to a capture")
            kinds.append("material_hang")
            facts["material"] = ms
        elif ms["loss_cp"] >= hang_threshold:
            facts["material"] = ms      # corroborates the vision hang; don't double-word

    if "fork" in checks:
        mt = move_threat(board, move)
        if mt.get("threat_cp", 0) >= warn_threshold:
            s = (mt.get("threat") or {}).get("sentence")
            reasons.append(s or f"walks into a fork (~{mt['threat_cp']}cp)")
            kinds.append("fork")
            facts["threat"] = mt

    return {"flagged": bool(kinds), "reason": "; ".join(reasons),
            "kinds": kinds, "facts": facts}
