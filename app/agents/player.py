"""
Agent 2 -- the One-Off Frontier Player.

The player proposes ONE move at a time from an almost-empty context: just the
board (FEN) and, at most, a one-line general note from the orchestrator. It gets
NO manifest dump, NO move history, and NO precomputed legal/illegal move list --
small models play worse when a move list is stuffed into the prompt.

Moves enter a ban-list only by trial: a proposal that fails legality is added to
the turn's `illegal` list and the proposer is asked again from a fresh minimal
state (see game/coach.py). The slate of K distinct legal candidates is built the
same way -- one legal move at a time.

Selection among the established moves depends on the mode (see game/coach.py):
  * guided/assist  -> Stockfish ranks (sandbox) and picks.
  * autonomous     -> the player itself picks via choose_among().

Stockfish top-moves are only used as candidates when there is NO LLM available
(the "Stockfish-only" brain), never to top up a partial LLM slate.
"""

from __future__ import annotations

import chess

from .base import LLMClient
from ..engine.legality import parse_move
from ..engine.stockfish_pool import StockfishPool

SYS_ONE = ("You are a chess player choosing ONE move from the position in front of you. "
           "Judge from the board itself. Keep the rationale to a single short sentence. "
           "Answer in strict JSON only.")

SYS_CHOOSE = ("You are choosing which of YOUR OWN candidate moves to actually play, using "
              "your own judgment -- no engine help. Always answer in strict JSON.")


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

    # -- propose ONE move from a minimal, trial-accumulated context ------------
    def propose_one(self, board: chess.Board, note: str = "",
                    ruled_out: list[str] | None = None,
                    already_chosen: list[str] | None = None) -> "Proposal":
        """Ask the LLM for a single move. Context is FEN + one-line note + the
        small trial-accumulated ban/chosen lists (only when non-empty). Returns a
        Proposal; move=None means the response was illegal/unparseable (raw kept
        so the caller can add it to the illegal list)."""
        if not self.client.available:
            return Proposal(None, None, "(no LLM)", "fallback", raw="")
        parts = [
            f"FEN: {board.fen()}",
            f"You are playing {'White' if board.turn else 'Black'}; it is your move.",
        ]
        if board.is_check():
            parts.append("You are in CHECK -- your move MUST get your king out of check.")
        if note:
            parts.append(f"General plan (a hint only): {note}")
        if ruled_out:
            parts.append("Do NOT choose these (already tried, not allowed): "
                         + ", ".join(ruled_out))
        if already_chosen:
            parts.append("You already chose: " + ", ".join(already_chosen)
                         + " -- pick a DIFFERENT legal move.")
        parts.append('Respond as JSON: {"move":"SAN or UCI","rationale":"one short sentence"}. '
                     "Only output JSON.")
        data = self.client.chat_json(SYS_ONE, "\n".join(parts), temperature=0.5, max_tokens=220)
        if not data:
            return Proposal(None, None, "(no response)", "llm", raw="")
        raw = str(data.get("move", ""))
        rat = str(data.get("rationale", "")).strip()
        mv = parse_move(board, raw)
        if mv is None:
            return Proposal(None, raw or None, rat or "(illegal/unparsed)", "llm", raw=raw)
        return Proposal(mv, board.san(mv), rat, "llm", raw=raw)

    # -- autonomous selection among the player's own candidates ----------------
    def choose_among(self, board: chess.Board, context: str, candidates: list[dict]) -> dict | None:
        """The player picks one of ITS candidate moves. Returns {uci, reasoning} or None."""
        if not self.client.available or not candidates:
            return None
        lines = "\n".join(
            f"{i+1}. {c['san']} -- {c.get('proposal', {}).get('rationale', '') or ''}"
            for i, c in enumerate(candidates)
        )
        user = (
            f"{context}\nYour candidate moves:\n{lines}\n\n"
            "Pick the SINGLE best move to play from YOUR list above, on your own judgment. "
            'Respond as JSON: {"move":"<one of the listed moves, SAN or UCI>",'
            '"reasoning":"why this one over the others"}. Only output JSON.'
        )
        data = self.client.chat_json(SYS_CHOOSE, user, temperature=0.4, max_tokens=400)
        if not data:
            return None
        mv = parse_move(board, str(data.get("move", "")))
        if mv is None:
            return None
        uci = mv.uci()
        if uci not in {c["uci"] for c in candidates}:
            return None
        return {"uci": uci, "reasoning": str(data.get("reasoning", "")).strip()}

    # -- Stockfish candidate slate (only when there is NO LLM) -----------------
    def stockfish_top(self, board: chess.Board, n: int = 3, class_depth: int = 12) -> list[chess.Move]:
        top = self.pool.top_moves(board, depth=class_depth, n=n)
        return [t["move"] for t in top]
