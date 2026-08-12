"""
Agent 2 -- the One-Off Frontier Player.

The player establishes its OWN slate of candidate moves.  Given the board and the
orchestrator's continuity payload, it proposes a LIST of distinct legal moves
(the moves it would genuinely consider).  Illegal proposals are kicked back and
blacklisted for the turn (told only that they're illegal), and the loop keeps
asking until it has the required number of distinct legal moves -- so Stockfish
never substitutes its own picks to fill the slate.

Selection among the established moves depends on the mode (see game/coach.py):
  * guided/assist  -> Stockfish ranks (sandbox) and picks.
  * autonomous     -> the player itself picks via choose_among().

Stockfish top-moves are only used as candidates when there is NO LLM available
(the "Stockfish-only" brain), never to top up a partial LLM slate.
"""

from __future__ import annotations

import random

import chess

from .base import LLMClient
from ..engine.legality import parse_move
from ..engine.stockfish_pool import StockfishPool

SYS_CANDS = ("You are a strong, intuitive chess player (about master strength). You "
             "propose the moves YOU would actually consider -- from plans, patterns and "
             "short concrete lines, not engine lookups. Always answer in strict JSON.")

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

    # -- establish a LIST of the player's own candidate moves (LLM only) --------
    def propose_candidates(self, board: chess.Board, context: str, forbidden: list[str],
                           need: int = 3, feedback: str | None = None) -> dict:
        """Ask the LLM for `need` distinct legal candidate moves.

        Returns {"legal": [Proposal...], "illegal": [raw str...]}. No Stockfish."""
        if not self.client.available:
            return {"legal": [], "illegal": []}
        forbid = ""
        if forbidden:
            forbid = ("\nAlready ruled out this turn (illegal or already listed) -- do NOT "
                      "repeat these: " + ", ".join(forbidden))
        fb = f"\n{feedback}\n" if feedback else ""
        user = (
            f"{context}{forbid}{fb}\n"
            f"Propose your top {need} DISTINCT legal candidate moves for the side to move "
            f"-- the moves you would genuinely consider. Respond as JSON: "
            f'{{"candidates":[{{"move":"SAN or UCI","rationale":"one sentence"}}, ...]}} '
            f"with at least {need} entries. Only output JSON."
        )
        data = self.client.chat_json(SYS_CANDS, user, temperature=0.6, max_tokens=700)
        legal: list[Proposal] = []
        illegal: list[str] = []
        if not data:
            return {"legal": legal, "illegal": illegal}
        cands = data.get("candidates")
        if isinstance(cands, dict):
            cands = list(cands.values())
        if not isinstance(cands, list):
            # tolerate a single {move, rationale} or a bare {move: ...}
            cands = [data] if data.get("move") else []
        for c in cands:
            if isinstance(c, str):
                raw, rat = c, ""
            elif isinstance(c, dict):
                raw, rat = str(c.get("move", "")), str(c.get("rationale", "")).strip()
            else:
                continue
            mv = parse_move(board, raw)
            if mv is None:
                if raw:
                    illegal.append(raw)
            else:
                legal.append(Proposal(mv, board.san(mv), rat, "llm", raw=raw))
        return {"legal": legal, "illegal": illegal}

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
