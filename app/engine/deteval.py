"""
Deterministic evaluator -- MODULE 1: material safety (hanging / en-prise).

This is the first deterministic layer that HELPS the (weak) player: it answers,
without Stockfish, "does this move lose material for nothing?" -- one ply,
pin- and check-aware (it drives off LEGAL captures, so a pinned attacker can't
capture and a move that leaves the king in check is never considered), with the
exchange resolved by a static-exchange evaluation.

It runs AFTER a move is selected: if the chosen move hangs material that one of
the player's OWN other candidates would have kept, it vetoes the pick and
substitutes the safe sibling (guided/assist), or just warns (autonomous).

Scope, honestly: MATERIAL only, ONE ply. It catches "you just hung your knight"
and "that capture loses to the recapture." It does NOT see forks that win
material next move, or positional compensation -- those are later modules.
"""

from __future__ import annotations

import chess

from .concepts import see  # static exchange evaluation (already pin-agnostic; see note)

PIECE_VAL = {chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330,
             chess.ROOK: 500, chess.QUEEN: 900}


def material_cp(board: chess.Board, pov: chess.Color) -> int:
    s = 0
    for pt, v in PIECE_VAL.items():
        s += v * len(board.pieces(pt, pov)) - v * len(board.pieces(pt, not pov))
    return s


def best_capture_gain(board: chess.Board, side: chess.Color) -> tuple[int, int | None]:
    """Most material `side` (which must be the side to move) can WIN via a single
    legal capture right now, exchange resolved by SEE. Returns (gain_cp, target_sq)."""
    best, best_sq = 0, None
    if board.turn != side:
        return best, best_sq
    for mv in board.legal_moves:
        if not board.is_capture(mv):
            continue
        tgt = mv.to_square
        victim = board.piece_at(tgt)
        if victim is None:            # en passant (rare); approximate as a pawn
            gain = PIECE_VAL[chess.PAWN]
        else:
            gain = see(board, tgt, side)
        if gain > best:
            best, best_sq = gain, tgt
    return best, best_sq


def move_safety(board: chess.Board, move: chess.Move) -> dict:
    """1-ply material safety of `move` for the side to move.

    net_cp = (material after our move, our pov) - (opponent's best capture gain).
    A hanging move scores low; a sound capture scores high; a quiet move scores
    the current material. Returns net_cp, the loss, and a phenomenological line."""
    mover = board.turn
    b = board.copy()
    try:
        san = b.san(move)
    except Exception:
        san = move.uci()
    b.push(move)
    loss, sq = best_capture_gain(b, b.turn)        # b.turn is now the opponent
    net = material_cp(b, mover) - max(0, loss)
    sentence = None
    if loss > 0 and sq is not None:
        victim = b.piece_at(sq)
        name = chess.piece_name(victim.piece_type) if victim else "unit"
        sentence = (f"{san} loses material: the opponent wins ~{loss}cp by capturing "
                    f"the {name} on {chess.square_name(sq)}")
    return {"san": san, "net_cp": int(net), "loss_cp": int(loss),
            "loss_sq": (chess.square_name(sq) if sq is not None else None),
            "sentence": sentence}


def assess_candidates(board: chess.Board, candidate_ucis: list[str],
                      chosen_uci: str, hang_threshold: int = 100) -> dict:
    """Score every candidate for material safety and decide whether the chosen
    move hangs material a safe sibling avoids.

    Returns:
      per_candidate: [{uci, san, net_cp, loss_cp, sentence}]
      chosen_net, best_net, best_uci (safest, tie-break keeps chosen)
      hangs: True if chosen_net < best_net - hang_threshold
      safe_alt: uci of the safest sibling (when hangs)
    """
    per = {}
    for u in candidate_ucis:
        per[u] = move_safety(board, chess.Move.from_uci(u))
    best_uci = max(candidate_ucis,
                   key=lambda u: (per[u]["net_cp"], 1 if u == chosen_uci else 0))
    best_net = per[best_uci]["net_cp"]
    chosen_net = per.get(chosen_uci, {"net_cp": best_net})["net_cp"]
    hangs = chosen_net < best_net - hang_threshold
    return {
        "per_candidate": [{"uci": u, **per[u]} for u in candidate_ucis],
        "chosen_net": chosen_net, "best_net": best_net, "best_uci": best_uci,
        "hangs": hangs, "safe_alt": (best_uci if hangs else None),
        "hang_threshold": hang_threshold,
    }
