"""
Deterministic piece-vision map (pure python-chess, no engine).

"Which piece sees which piece?" For every piece on the board this computes the
OTHER pieces it ATTACKS (enemy pieces standing on squares it controls) and
DEFENDS (friendly pieces on squares it controls), using python-chess
line-of-sight: sliders respect blockers, pawns see their two diagonal capture
squares (not the forward push), knights and kings their fixed pattern. There is
no x-ray -- a blocker ends the line.

This is a PRIMITIVE only. It makes no material, safety, pin, or exchange
judgement; it reports who sees whom. Higher-level checks (hanging pieces, defence
adequacy, pins, regain) can be composed on top of it.
"""
from __future__ import annotations

import chess

PIECE_VAL = {chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330,
             chess.ROOK: 500, chess.QUEEN: 900, chess.KING: 0}


def sees(board: chess.Board, from_sq: int, to_sq: int) -> bool:
    """True iff the piece on `from_sq` sees (attacks or defends) `to_sq` under the
    current occupancy. False if `from_sq` is empty."""
    if board.piece_at(from_sq) is None:
        return False
    return to_sq in board.attacks(from_sq)


def _descr(board: chess.Board, sq: int) -> dict:
    p = board.piece_at(sq)
    return {"square": chess.square_name(sq), "piece": p.symbol(),
            "type": chess.piece_name(p.piece_type),
            "color": "white" if p.color else "black",
            "value": PIECE_VAL[p.piece_type]}


def piece_lines(board: chess.Board, sq: int) -> dict:
    """For the piece on `sq`: {attacks:[enemy pieces it sees],
    defends:[friendly pieces it sees]}. Empty if `sq` is empty."""
    p = board.piece_at(sq)
    if p is None:
        return {"attacks": [], "defends": []}
    attacks, defends = [], []
    for t in board.attacks(sq):
        tp = board.piece_at(t)
        if tp is None:
            continue
        (defends if tp.color == p.color else attacks).append(_descr(board, t))
    return {"attacks": attacks, "defends": defends}


def vision_map(board: chess.Board) -> list[dict]:
    """Who-sees-whom for every occupied square:
       {square, piece, type, color, value,
        attacks:     [enemy pieces this piece attacks],
        defends:     [friendly pieces this piece defends],
        attacked_by: [enemy pieces attacking this square],
        defended_by: [friendly pieces defending this square]}.
    Pure line-of-sight; no pin/SEE/material judgement."""
    out = []
    for sq in chess.SQUARES:
        p = board.piece_at(sq)
        if p is None:
            continue
        lines = piece_lines(board, sq)
        rec = _descr(board, sq)
        rec["attacks"] = lines["attacks"]
        rec["defends"] = lines["defends"]
        rec["attacked_by"] = [_descr(board, s) for s in board.attackers(not p.color, sq)]
        rec["defended_by"] = [_descr(board, s) for s in board.attackers(p.color, sq)]
        out.append(rec)
    return out


def hanging_after(board: chess.Board, move: chess.Move) -> list[dict]:
    """Pieces of the MOVER's colour left attacked by an enemy piece and defended
    by NO friendly piece after `move` -- a "full hang" (undefended, en prise).
    Kings are excluded (that is check/legality, handled elsewhere). Ignores piece
    values and pins for now (a future refinement); this only answers the user's
    rule: our piece is SEEN by an enemy and by no friend of ours. Returns the list
    of hung pieces (each with attacked_by), empty if none."""
    mover = board.turn
    b = board.copy()
    b.push(move)
    hung = []
    for sq in chess.SQUARES:
        p = b.piece_at(sq)
        if p is None or p.color != mover or p.piece_type == chess.KING:
            continue
        attackers = b.attackers(not mover, sq)
        if attackers and not b.attackers(mover, sq):
            rec = _descr(b, sq)
            rec["attacked_by"] = [_descr(b, s) for s in attackers]
            hung.append(rec)
    return hung


def render(board: chess.Board) -> str:
    """Compact human-readable dump of the vision map, one line per piece."""
    lines = []
    for r in vision_map(board):
        a = ",".join(f"{x['piece']}{x['square']}" for x in r["attacks"]) or "-"
        d = ",".join(f"{x['piece']}{x['square']}" for x in r["defends"]) or "-"
        ab = ",".join(f"{x['piece']}{x['square']}" for x in r["attacked_by"]) or "-"
        db = ",".join(f"{x['piece']}{x['square']}" for x in r["defended_by"]) or "-"
        lines.append(f"{r['piece']}{r['square']:<2} attacks[{a}] defends[{d}] "
                     f"attacked_by[{ab}] defended_by[{db}]")
    return "\n".join(lines)
