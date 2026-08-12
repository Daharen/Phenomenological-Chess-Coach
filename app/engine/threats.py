"""
Deterministic evaluator -- MODULE 2: fork / double-attack threats (2-ply).

Module 1 (material safety) only sees the opponent's IMMEDIATE captures -- one
ply. Its blind spot is the tactic that most often wrecks a developing player:
walking into a FORK. This module looks one move deeper. For the position AFTER
our move it asks, for every opponent reply, "does this single move attack two of
our pieces at once -- a knight hitting king + rook, a queen double attack, a pawn
fork -- such that we can save only one?" and estimates the material we then lose.

How it decides a fork is real (deliberately conservative -- this is a teaching
signal, so a false alarm is worse than silence):
  * The forking piece must land SAFELY: if we can simply capture it without losing
    material (SEE >= 0), the fork is refuted and ignored.
  * At least two of our pieces must be attacked by the forker, and each counted
    one must be genuinely winnable -- undefended, the king (a check we must
    answer), or an up-trade (worth more than the forker).
  * With a check in the mix we must answer the check, so we lose the best OTHER
    winnable piece. With a quiet double attack we save the most valuable and lose
    the next -- that "next" value is the threat.

Scope, honestly: it models the opponent's ONE forking move and OUR ONE reply,
material only, via SEE. It does NOT model discovered attacks by a non-moving
piece, our defensive counter-threats, or a pin that would freeze the forker. That
residual uncertainty is exactly why the default action is WARN, not veto.
"""

from __future__ import annotations

import chess

from .concepts import see, VALUES


def _winnable(board: chess.Board, sq: int, attacker_val: int) -> tuple[bool, int]:
    """Our (non-king) piece on `sq`: can the opponent win material off it? True if
    it is undefended (opponent wins the whole piece) or worth more than the
    forker (an up-trade -- opponent wins the difference)."""
    p = board.piece_at(sq)
    if p is None or p.piece_type == chess.KING:
        return False, 0
    val = VALUES[p.piece_type]
    defended = bool(board.attackers(p.color, sq))   # p.color == us -> our defenders
    if not defended:
        return True, val
    if val > attacker_val:
        return True, val - attacker_val
    return False, 0


def _can_capture_forker(board: chess.Board, fsq: int, us: chess.Color) -> bool:
    """Can `us` (the side to move on `board`) LEGALLY capture the piece on `fsq`
    without losing material (SEE >= 0)?  If so, the fork is refuted."""
    if not board.is_attacked_by(us, fsq):
        return False
    for mv in board.legal_moves:
        if mv.to_square == fsq and board.is_capture(mv):
            if see(board, fsq, us) >= 0:
                return True
    return False


def best_fork_threat(board: chess.Board) -> dict | None:
    """`board`: the OPPONENT is to move (i.e. the position right after OUR move).
    Return the opponent's strongest fork / double-attack reply, or None.

    Result: {gain_cp, forker_san, forker_to, targets:[sqname...], is_check, sentence}
    """
    opp = board.turn
    us = not opp
    best: dict | None = None
    for om in board.legal_moves:
        mover = board.piece_at(om.from_square)
        if mover is None:
            continue
        fval = VALUES[mover.piece_type]
        try:
            fsan = board.san(om)
        except Exception:
            fsan = om.uci()
        b2 = board.copy()
        b2.push(om)                      # now it is OUR turn (b2.turn == us)
        fsq = om.to_square
        is_check = b2.is_check()         # our king to move -> true if we are checked

        targets = []                     # (square, gain_cp, is_king)
        for t in b2.attacks(fsq):
            tp = b2.piece_at(t)
            if tp is None or tp.color != us:
                continue
            if tp.piece_type == chess.KING:
                targets.append((t, 0, True))
            else:
                w, g = _winnable(b2, t, fval)
                if w:
                    targets.append((t, g, False))

        if len(targets) < 2:             # not a double attack
            continue
        if _can_capture_forker(b2, fsq, us):   # we just take the forker
            continue

        material_targets = [g for (_, g, k) in targets if not k]
        king_hit = any(k for (_, _, k) in targets)
        if is_check or king_hit:
            if not material_targets:
                continue
            gain = max(material_targets)         # answer the check, lose the best other
        else:
            if len(material_targets) < 2:
                continue
            material_targets.sort(reverse=True)
            gain = material_targets[1]           # save the top, lose the next

        if gain <= 0:
            continue
        if best is None or gain > best["gain_cp"]:
            tnames = [chess.square_name(t) for (t, _, _) in targets]
            chk = " with check" if (is_check or king_hit) else ""
            best = {
                "gain_cp": int(gain), "forker_san": fsan,
                "forker_to": chess.square_name(fsq), "targets": tnames,
                "is_check": bool(is_check or king_hit),
                "sentence": (f"{fsan} forks {', '.join(tnames)}{chk}: you can save one "
                             f"but lose ~{int(gain)}cp next move"),
            }
    return best


def move_threat(board: chess.Board, move: chess.Move) -> dict:
    """Opponent's best fork/double-attack in the position AFTER we play `move`."""
    b = board.copy()
    try:
        san = b.san(move)
    except Exception:
        san = move.uci()
    b.push(move)
    threat = best_fork_threat(b)
    return {"san": san, "threat_cp": (threat["gain_cp"] if threat else 0),
            "threat": threat}


def assess_candidates(board: chess.Board, candidate_ucis: list[str],
                      chosen_uci: str, warn_threshold: int = 150) -> dict:
    """Score every candidate by the worst fork it walks into, and decide whether
    the chosen move walks into one a safe sibling would avoid.

    Returns:
      per_candidate: [{uci, san, threat_cp, sentence}]
      chosen_cp, safe_cp, safe_alt (least-threat sibling; tie keeps chosen)
      has_threat  : chosen walks into a fork >= warn_threshold
      avoidable   : a sibling allows strictly less  (so re-picking helps)
      chosen_threat: the full threat dict for the chosen move (or None)
    """
    per = {u: move_threat(board, chess.Move.from_uci(u)) for u in candidate_ucis}
    safe_alt = min(candidate_ucis,
                   key=lambda u: (per[u]["threat_cp"], 0 if u == chosen_uci else 1))
    safe_cp = per[safe_alt]["threat_cp"]
    chosen_cp = per.get(chosen_uci, {"threat_cp": safe_cp})["threat_cp"]
    has_threat = chosen_cp >= warn_threshold
    avoidable = chosen_cp > safe_cp
    return {
        "per_candidate": [{"uci": u, "san": per[u]["san"],
                           "threat_cp": per[u]["threat_cp"],
                           "sentence": (per[u]["threat"]["sentence"]
                                        if per[u]["threat"] else None)}
                          for u in candidate_ucis],
        "chosen_cp": chosen_cp, "safe_cp": safe_cp, "safe_alt": safe_alt,
        "has_threat": has_threat, "avoidable": avoidable,
        "chosen_threat": per.get(chosen_uci, {}).get("threat"),
        "warn_threshold": warn_threshold,
    }
