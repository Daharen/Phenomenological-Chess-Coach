"""
Agent 1 -- the Frontier Orchestrator.

Owns the long-horizon narrative: it opens the game with a plan, keeps a mutable
Strategic Manifest across turns (continuity), feeds the one-off player its
board-state-plus-continuity payload, and audits the chosen move against
Stockfish for blind spots.  The Blunder Protocol (manifest archive + crisis
mode) lives in Memory and is triggered from the game loop.

If no LLM is reachable it degrades to a small deterministic opening/plan book so
the system still plays and narrates.
"""

from __future__ import annotations

import json

import chess

from .base import LLMClient
from ..engine.concepts import detect_concepts, concept_terms
from ..engine.stockfish_pool import StockfishPool, MATE
from ..game.memory import Memory

SYS = ("You are the Strategic Orchestrator of a phenomenological chess engine. "
       "You think in human strategic language (plans, structures, targets), not "
       "raw calculation. Always answer in strict JSON.")

_BOOK = {
    "white": {"opening": "Queen's Pawn / Ruy-style development",
              "theme": "Seize the center, develop quickly, castle, then expand where the structure invites it",
              "goals": ["Control d4/e4", "Rapid piece development", "King safety by move 8",
                        "Choose a wing to expand based on pawn structure"]},
    "black": {"opening": "Solid classical defense",
              "theme": "Contest the center, complete development, castle, seek a freeing pawn break",
              "goals": ["Neutralize White's center", "Develop with tempo", "King safety",
                        "Prepare a timely ...c5 or ...e5 break"]},
}


class Orchestrator:
    def __init__(self, client: LLMClient, pool: StockfishPool, memory: Memory, cfg):
        self.client = client
        self.pool = pool
        self.memory = memory
        self.cfg = cfg

    # -------------------------------------------------------------------------
    def start_game(self, color: str):
        color = "white" if color.lower().startswith("w") else "black"
        book = _BOOK[color]
        opening, theme, goals, opp = book["opening"], book["theme"], book["goals"], "Unknown"
        if self.client.available:
            data = self.client.chat_json(
                SYS,
                f"You play {color}. Choose an opening approach and an overarching "
                f"strategic plan for the whole game. Respond as JSON with keys: "
                f'"opening" (short name), "theme" (one sentence), '
                f'"long_term_goals" (list of 3-5 short strings), '
                f'"opponent_intent" (one sentence).',
                temperature=0.6, max_tokens=400,
            )
            if data:
                opening = data.get("opening", opening)
                theme = data.get("theme", theme)
                goals = data.get("long_term_goals", goals) or goals
                opp = data.get("opponent_intent", opp)
        self.memory.start(color, opening, theme, goals, opp)
        return self.memory.manifest.to_dict()

    # -------------------------------------------------------------------------
    def context_for_player(self, board: chess.Board) -> str:
        """The continuous payload handed to the one-off player: FEN + history +
        manifest + the current position's detected concepts."""
        m = self.memory.manifest
        tags = detect_concepts(board)
        concepts_here = ", ".join(concept_terms(tags)) or "none flagged"
        history = " ".join(_san_history(board)[-12:])
        return (
            f"FEN: {board.fen()}\n"
            f"Side to move: {'white' if board.turn else 'black'} (you are {m.color})\n"
            f"Recent moves: {history or 'game start'}\n"
            f"--- Strategic Manifest (mode: {m.mode}) ---\n"
            f"Opening: {m.opening}\n"
            f"Theme: {m.theme}\n"
            f"Long-term goals: {'; '.join(m.long_term_goals)}\n"
            f"Targets: {'; '.join(m.targets) or 'to be identified'}\n"
            f"King safety: {m.king_safety}\n"
            f"Opponent intent: {m.opponent_intent}\n"
            f"Concepts present now: {concepts_here}\n"
        )

    # -------------------------------------------------------------------------
    def update_after_move(self, board: chess.Board, played: chess.Move,
                          classification, our_eval_cp: int):
        """Mutate the manifest lightly each move; refresh king safety and targets."""
        m = self.memory.manifest
        m.move_number = board.fullmove_number
        # king safety heuristic
        color = chess.WHITE if m.color == "white" else chess.BLACK
        ksq = board.king(color)
        if ksq is not None:
            if board.has_castling_rights(color):
                m.king_safety = "Not yet castled"
            else:
                m.king_safety = f"King on {chess.square_name(ksq)}"
        # crisis / recovery handled by memory.note_eval in the loop
        # periodically ask the LLM to refresh the plan (every 4 full moves)
        if self.client.available and m.move_number % 4 == 0 and board.turn == (color):
            data = self.client.chat_json(
                SYS,
                f"Update the strategic manifest given the position.\n"
                f"{self.context_for_player(board)}\n"
                f"Respond JSON with keys \"theme\", \"targets\" (list), "
                f"\"opponent_intent\". Keep continuity with the existing plan unless "
                f"the position clearly changed.",
                temperature=0.5, max_tokens=350,
            )
            if data:
                m.theme = data.get("theme", m.theme)
                m.targets = data.get("targets", m.targets) or m.targets
                m.opponent_intent = data.get("opponent_intent", m.opponent_intent)

    # -------------------------------------------------------------------------
    def audit_blind_spot(self, board: chess.Board, chosen: chess.Move,
                         movetime: float = 1.5) -> dict | None:
        """Compare the chosen move to Stockfish's best; if a materially better
        move was overlooked, surface it for the evaluator/fast-follow."""
        top = self.pool.top_moves(board, movetime=movetime, n=2)
        if not top:
            return None
        best = top[0]
        if best["move"] == chosen:
            return None
        # eval of chosen vs best (mover pov)
        chosen_cp = None
        for t in top:
            if t["move"] == chosen:
                chosen_cp = t["cp"]
        if chosen_cp is None:
            b = board.copy()
            b.push(chosen)
            chosen_cp = self.pool.eval_cp(b, pov=board.turn, movetime=movetime)
        gap = best["cp"] - (chosen_cp or 0)
        if gap >= 150:
            return {
                "overlooked_move": board.san(best["move"]),
                "gap_cp": int(gap),
                "note": f"Stockfish prefers {board.san(best['move'])} "
                        f"({gap}cp better) over the chosen move.",
            }
        return None


def _san_history(board: chess.Board) -> list[str]:
    b = chess.Board()
    out = []
    for mv in board.move_stack:
        try:
            out.append(b.san(mv))
        except Exception:
            out.append(mv.uci())
        b.push(mv)
    return out
