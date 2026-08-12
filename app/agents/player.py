"""
Agent 2 -- the One-Off Frontier Player.

Given a board state and the orchestrator's continuity payload, it proposes ONE
move from memory (no lookups).  Legality is enforced deterministically by the
game loop: an illegal proposal is kicked back and appended to a per-turn
forbidden list (the model is told only that the move is illegal, never what is
better), which snaps it out of loops without dumping the whole legal-move list.

When flagged-and-within-horizon, the loop hands it the consequence line and asks
again.  Without an LLM it falls back to a Stockfish-guided proposal so the
system still plays at strength.
"""

from __future__ import annotations

import random

import chess

from .base import LLMClient
from ..engine.legality import parse_move
from ..engine.stockfish_pool import StockfishPool

SYS = ("You are a strong, intuitive chess player (about master strength) making "
       "ONE move. You think like a human: plans, patterns, and short concrete "
       "lines. You do not have engine lookups. Give your single best move.")


class Proposal:
    def __init__(self, move: chess.Move | None, san: str | None, rationale: str,
                 source: str, raw: str | None = None):
        self.move = move
        self.san = san
        self.rationale = rationale
        self.source = source          # "llm" | "fallback"
        self.raw = raw

    def to_dict(self):
        return {"uci": self.move.uci() if self.move else None, "san": self.san,
                "rationale": self.rationale, "source": self.source}


class OneOffPlayer:
    def __init__(self, client: LLMClient, pool: StockfishPool, cfg):
        self.client = client
        self.pool = pool
        self.cfg = cfg

    def propose(self, board: chess.Board, context: str, forbidden: list[str],
                feedback: str | None = None, class_depth: int = 12) -> Proposal:
        if self.client.available:
            p = self._propose_llm(board, context, forbidden, feedback)
            if p is not None:
                return p
        return self._propose_fallback(board, forbidden, feedback, class_depth)

    # -- LLM proposer ----------------------------------------------------------
    def _propose_llm(self, board, context, forbidden, feedback) -> Proposal | None:
        forbid = ""
        if forbidden:
            forbid = ("\nThese moves are already ruled out this turn (illegal or "
                      "already refuted) — do not repeat them: " + ", ".join(forbidden))
        fb = f"\nFeedback on your previous try:\n{feedback}\n" if feedback else ""
        user = (
            f"{context}{forbid}{fb}\n"
            "Choose your single move. Respond as JSON with keys "
            '"move" (in SAN or UCI), "rationale" (one or two sentences on the plan '
            "and any short line you saw). Only output JSON."
        )
        data = self.client.chat_json(SYS, user, temperature=0.5, max_tokens=350)
        if not data:
            return None
        mv = parse_move(board, str(data.get("move", "")))
        rationale = str(data.get("rationale", "")).strip()
        if mv is None:
            # illegal / unparseable -> still return so the loop can blacklist it
            return Proposal(None, str(data.get("move", "")).strip() or None,
                            rationale or "(no legal move parsed)", "llm",
                            raw=str(data.get("move", "")))
        return Proposal(mv, board.san(mv), rationale, "llm", raw=str(data.get("move")))

    # -- deterministic fallback ------------------------------------------------
    def _propose_fallback(self, board, forbidden, feedback, class_depth) -> Proposal:
        top = self.pool.top_moves(board, depth=class_depth, n=4)
        top = [t for t in top if t["move"].uci() not in forbidden]
        if not top:
            legal = [m for m in board.legal_moves if m.uci() not in forbidden]
            if not legal:
                legal = list(board.legal_moves)
            mv = random.choice(legal) if legal else None
            return Proposal(mv, board.san(mv) if mv else None,
                            "Fallback: only remaining legal option.", "fallback")
        # if we were given feedback (a prior try was rejected), take the best
        # remaining; otherwise add slight human noise among near-top moves.
        if feedback:
            choice = top[0]
        else:
            best_cp = top[0]["cp"]
            near = [t for t in top if best_cp - t["cp"] <= 40] or [top[0]]
            choice = random.choice(near)
        mv = choice["move"]
        return Proposal(mv, board.san(mv),
                        f"Fallback (Stockfish-guided): eval {choice['cp']}cp.",
                        "fallback")
