"""Legality helpers and tolerant move parsing for LLM output."""

from __future__ import annotations

import re
import chess


def parse_move(board: chess.Board, text: str) -> chess.Move | None:
    """Parse a move from noisy LLM text, trying UCI then SAN.

    Returns a legal chess.Move or None. Never raises."""
    if not text:
        return None
    t = text.strip().strip(".")
    # direct UCI (e2e4, e7e8q)
    m = re.search(r"\b([a-h][1-8][a-h][1-8][qrbn]?)\b", t.lower())
    if m:
        try:
            mv = chess.Move.from_uci(m.group(1))
            if mv in board.legal_moves:
                return mv
        except ValueError:
            pass
    # SAN tokens (Nf3, exd5, O-O, e8=Q+, Qxh7#)
    for tok in re.findall(r"O-O-O|O-O|[KQRBN]?x?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?", t):
        try:
            mv = board.parse_san(tok)
            if mv in board.legal_moves:
                return mv
        except ValueError:
            continue
    return None


def legal_uci(board: chess.Board) -> list[str]:
    return [m.uci() for m in board.legal_moves]


def legal_targets(board: chess.Board, square_name: str) -> list[str]:
    """Legal destination squares (UCI incl. promotion) for the piece on a square."""
    try:
        sq = chess.parse_square(square_name)
    except ValueError:
        return []
    return [m.uci() for m in board.legal_moves if m.from_square == sq]
