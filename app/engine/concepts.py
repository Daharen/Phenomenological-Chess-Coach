"""
Deterministic detector for chess "glossary" concepts actually present in a
position (and in the transition made by the last move).

This is purely rule-based over python-chess -- no engine required -- so it is
fast and runs every ply.  It gives the phenomenological coach concrete,
verifiable hooks ("there really is an absolute pin on e7") instead of relying on
the language model to hallucinate motifs.  Detected tags are matched by name to
the glossary definitions for explanation.

Each detected item is a dict:
    {"term": "Fork", "side": "white|black|both", "detail": "...", "squares": [...]}
"""

from __future__ import annotations

import chess

VALUES = {chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330,
          chess.ROOK: 500, chess.QUEEN: 900, chess.KING: 20000}

CENTER = [chess.D4, chess.E4, chess.D5, chess.E5]
COLOR_NAME = {chess.WHITE: "white", chess.BLACK: "black"}


# --------------------------------------------------------------------------- #
# Static exchange evaluation (used for hanging pieces & sacrifices)
# --------------------------------------------------------------------------- #

def _lva_square(board: chess.Board, target: int, color: chess.Color) -> int | None:
    best_sq, best_val = None, 10**9
    for sq in board.attackers(color, target):
        pt = board.piece_at(sq).piece_type
        if VALUES[pt] < best_val:
            best_val, best_sq = VALUES[pt], sq
    return best_sq


def see(board: chess.Board, target: int, capturing_side: chess.Color) -> int:
    """Static exchange evaluation: net material for `capturing_side` if it
    initiates captures on `target` and both sides recapture with the least
    valuable attacker.  X-rays are handled by mutating a board copy."""
    victim = board.piece_at(target)
    if victim is None:
        return 0
    b = board.copy(stack=False)
    gain = [VALUES[victim.piece_type]]
    side = capturing_side
    while True:
        sq = _lva_square(b, target, side)
        if sq is None:
            break
        piece = b.piece_at(sq)
        gain.append(VALUES[piece.piece_type] - gain[-1])
        b.remove_piece_at(sq)
        b.set_piece_at(target, piece)  # occupy target (opens x-rays)
        side = not side
    for i in range(len(gain) - 1, 0, -1):
        gain[i - 1] = -max(-gain[i - 1], gain[i])
    return gain[0]


# --------------------------------------------------------------------------- #
# Pawn structure
# --------------------------------------------------------------------------- #

def _pawns(board, color):
    return list(board.pieces(chess.PAWN, color))


def _files_with_pawn(board, color):
    return {chess.square_file(s) for s in _pawns(board, color)}


def pawn_structure(board: chess.Board) -> list[dict]:
    out = []
    for color in (chess.WHITE, chess.BLACK):
        cname = COLOR_NAME[color]
        pawns = _pawns(board, color)
        files = [chess.square_file(s) for s in pawns]
        file_counts = {f: files.count(f) for f in set(files)}
        own_files = set(files)
        enemy = not color

        # doubled
        for f, c in file_counts.items():
            if c >= 2:
                out.append({"term": "Doubled Pawns", "side": cname,
                            "detail": f"{c} pawns on the {chr(97+f)}-file", "squares": []})
        # pawn islands
        islands = _count_islands(sorted(own_files))
        if islands >= 3:
            out.append({"term": "Pawn Island", "side": cname,
                        "detail": f"{islands} pawn islands (structural weakness)", "squares": []})
        # isolated / passed / backward
        for s in pawns:
            f = chess.square_file(s)
            if (f - 1) not in own_files and (f + 1) not in own_files:
                out.append({"term": "Isolated Pawn", "side": cname,
                            "detail": f"isolated pawn on {chess.square_name(s)}",
                            "squares": [chess.square_name(s)]})
            if _is_passed(board, s, color):
                term = "Passed Pawn"
                if _is_protected_passed(board, s, color):
                    term = "Protected Passed Pawn"
                out.append({"term": term, "side": cname,
                            "detail": f"passed pawn on {chess.square_name(s)}",
                            "squares": [chess.square_name(s)]})
        # connected passed
        cpp = _connected_passed(board, color)
        if cpp:
            out.append({"term": "Connected Passed Pawns", "side": cname,
                        "detail": "connected passed pawns: " + ", ".join(cpp), "squares": cpp})
    return out


def _count_islands(sorted_files):
    if not sorted_files:
        return 0
    islands, prev = 1, sorted_files[0]
    for f in sorted_files[1:]:
        if f - prev > 1:
            islands += 1
        prev = f
    return islands


def _is_passed(board, square, color):
    f = chess.square_file(square)
    r = chess.square_rank(square)
    enemy = not color
    for s in board.pieces(chess.PAWN, enemy):
        ef, er = chess.square_file(s), chess.square_rank(s)
        if abs(ef - f) <= 1:
            if color == chess.WHITE and er > r:
                return False
            if color == chess.BLACK and er < r:
                return False
    return True


def _is_protected_passed(board, square, color):
    for s in board.attackers(color, square):
        if board.piece_at(s).piece_type == chess.PAWN:
            return True
    return False


def _connected_passed(board, color):
    passed = [s for s in _pawns(board, color) if _is_passed(board, s, color)]
    names = []
    for i, a in enumerate(passed):
        for b in passed[i + 1:]:
            if abs(chess.square_file(a) - chess.square_file(b)) == 1:
                names.extend([chess.square_name(a), chess.square_name(b)])
    return sorted(set(names))


# --------------------------------------------------------------------------- #
# Files, rooks, outposts
# --------------------------------------------------------------------------- #

def files_and_rooks(board: chess.Board) -> list[dict]:
    out = []
    wf = _files_with_pawn(board, chess.WHITE)
    bf = _files_with_pawn(board, chess.BLACK)
    for f in range(8):
        if f not in wf and f not in bf:
            out.append({"term": "Open File", "side": "both",
                        "detail": f"the {chr(97+f)}-file is open", "squares": []})
    for color in (chess.WHITE, chess.BLACK):
        cname = COLOR_NAME[color]
        own = wf if color == chess.WHITE else bf
        enemy = bf if color == chess.WHITE else wf
        seventh = 6 if color == chess.WHITE else 1
        for s in board.pieces(chess.ROOK, color):
            f = chess.square_file(s)
            if f not in own and f not in enemy:
                out.append({"term": "Open File", "side": cname,
                            "detail": f"rook on the open {chess.square_name(s)} file",
                            "squares": [chess.square_name(s)]})
            elif f not in own and f in enemy:
                out.append({"term": "Semi-Open File", "side": cname,
                            "detail": f"rook on the semi-open {chr(97+f)}-file",
                            "squares": [chess.square_name(s)]})
            if chess.square_rank(s) == seventh:
                out.append({"term": "Seventh Rank", "side": cname,
                            "detail": f"rook on the 7th rank ({chess.square_name(s)})",
                            "squares": [chess.square_name(s)]})
    # rook battery: two rooks (or rook+queen) sharing a file
    for color in (chess.WHITE, chess.BLACK):
        heavy = list(board.pieces(chess.ROOK, color)) + list(board.pieces(chess.QUEEN, color))
        byfile = {}
        for s in heavy:
            byfile.setdefault(chess.square_file(s), []).append(s)
        for f, sqs in byfile.items():
            if len(sqs) >= 2:
                out.append({"term": "Battery", "side": COLOR_NAME[color],
                            "detail": f"heavy-piece battery on the {chr(97+f)}-file",
                            "squares": [chess.square_name(s) for s in sqs]})
    return out


def outposts(board: chess.Board) -> list[dict]:
    out = []
    for color in (chess.WHITE, chess.BLACK):
        cname = COLOR_NAME[color]
        enemy = not color
        for pt in (chess.KNIGHT, chess.BISHOP):
            for s in board.pieces(pt, color):
                r = chess.square_rank(s)
                in_enemy_half = (color == chess.WHITE and r >= 4) or (color == chess.BLACK and r <= 3)
                if not in_enemy_half:
                    continue
                if _pawn_supported(board, s, color) and not _attackable_by_enemy_pawn(board, s, color):
                    name = "Knight Outpost" if pt == chess.KNIGHT else "Outpost"
                    out.append({"term": name, "side": cname,
                                "detail": f"{chess.piece_name(pt)} outpost on {chess.square_name(s)}",
                                "squares": [chess.square_name(s)]})
    return out


def _pawn_supported(board, square, color):
    for s in board.attackers(color, square):
        if board.piece_at(s).piece_type == chess.PAWN:
            return True
    return False


def _attackable_by_enemy_pawn(board, square, color):
    """Could an enemy pawn ever advance to attack `square`?"""
    f = chess.square_file(square)
    r = chess.square_rank(square)
    enemy = not color
    for ef in (f - 1, f + 1):
        if 0 <= ef <= 7:
            for s in board.pieces(chess.PAWN, enemy):
                if chess.square_file(s) == ef:
                    er = chess.square_rank(s)
                    if enemy == chess.WHITE and er < r:
                        return True
                    if enemy == chess.BLACK and er > r:
                        return True
    return False


# --------------------------------------------------------------------------- #
# King safety, bishop pair, center, fianchetto
# --------------------------------------------------------------------------- #

def king_and_bishops(board: chess.Board) -> list[dict]:
    out = []
    for color in (chess.WHITE, chess.BLACK):
        cname = COLOR_NAME[color]
        ksq = board.king(color)
        if ksq is not None:
            shield = _pawn_shield(board, ksq, color)
            if shield <= 1 and _king_has_moved_region(ksq, color):
                out.append({"term": "King Safety", "side": cname,
                            "detail": f"thin pawn shield around {chess.square_name(ksq)} "
                                      f"({shield} shielding pawns)",
                            "squares": [chess.square_name(ksq)]})
            if _back_rank_weak(board, color, ksq):
                out.append({"term": "Back-Rank Weakness", "side": cname,
                            "detail": f"king on {chess.square_name(ksq)} boxed in by its own pawns",
                            "squares": [chess.square_name(ksq)]})
        # bishop pair
        if len(board.pieces(chess.BISHOP, color)) >= 2:
            out.append({"term": "Two Bishops", "side": cname,
                        "detail": "holds the bishop pair", "squares": []})
        # fianchetto
        for sq, need in _fianchetto_squares(color):
            p = board.piece_at(sq)
            if p and p.piece_type == chess.BISHOP and p.color == color:
                out.append({"term": "Fianchetto", "side": cname,
                            "detail": f"fianchettoed bishop on {chess.square_name(sq)}",
                            "squares": [chess.square_name(sq)]})
    return out


def _pawn_shield(board, ksq, color):
    f = chess.square_file(ksq)
    r = chess.square_rank(ksq)
    step = 1 if color == chess.WHITE else -1
    count = 0
    for df in (-1, 0, 1):
        ff = f + df
        rr = r + step
        if 0 <= ff <= 7 and 0 <= rr <= 7:
            p = board.piece_at(chess.square(ff, rr))
            if p and p.piece_type == chess.PAWN and p.color == color:
                count += 1
    return count


def _king_has_moved_region(ksq, color):
    # only flag thin shields once the king is on/near its back two ranks
    r = chess.square_rank(ksq)
    return (color == chess.WHITE and r <= 1) or (color == chess.BLACK and r >= 6)


def _back_rank_weak(board, color, ksq):
    back = 0 if color == chess.WHITE else 7
    if chess.square_rank(ksq) != back:
        return False
    enemy = not color
    # a real back-rank threat needs enemy heavy pieces AND a lane to the rank
    if not (board.pieces(chess.ROOK, enemy) or board.pieces(chess.QUEEN, enemy)):
        return False
    own_pawn_files = _files_with_pawn(board, color)
    if all(f in own_pawn_files for f in range(8)):
        return False  # no open/semi-open file exists yet (e.g. the opening)
    step = 1 if color == chess.WHITE else -1
    f = chess.square_file(ksq)
    escapes = 0
    for df in (-1, 0, 1):
        ff = f + df
        rr = back + step
        if 0 <= ff <= 7 and 0 <= rr <= 7:
            p = board.piece_at(chess.square(ff, rr))
            if not (p and p.piece_type == chess.PAWN and p.color == color):
                escapes += 1
    return escapes == 0


def _fianchetto_squares(color):
    if color == chess.WHITE:
        return [(chess.G2, None), (chess.B2, None)]
    return [(chess.G7, None), (chess.B7, None)]


def center_and_space(board: chess.Board) -> list[dict]:
    out = []
    wc = sum(1 for s in CENTER if board.attackers(chess.WHITE, s)) + \
        sum(1 for s in CENTER if (board.piece_at(s) and board.piece_at(s).color == chess.WHITE))
    bc = sum(1 for s in CENTER if board.attackers(chess.BLACK, s)) + \
        sum(1 for s in CENTER if (board.piece_at(s) and board.piece_at(s).color == chess.BLACK))
    if wc - bc >= 2:
        out.append({"term": "Center", "side": "white", "detail": f"white dominates the center ({wc} vs {bc})", "squares": []})
    elif bc - wc >= 2:
        out.append({"term": "Center", "side": "black", "detail": f"black dominates the center ({bc} vs {wc})", "squares": []})
    return out


# --------------------------------------------------------------------------- #
# Tactics from the transition (last move) and standing tactics
# --------------------------------------------------------------------------- #

def hanging_pieces(board: chess.Board) -> list[dict]:
    """Pieces that lose material if the opponent captures (SEE > 0)."""
    out = []
    for color in (chess.WHITE, chess.BLACK):
        enemy = not color
        for pt in (chess.PAWN, chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN):
            for s in board.pieces(pt, color):
                if board.attackers(enemy, s):
                    if see(board, s, enemy) > 0:
                        defended = bool(board.attackers(color, s))
                        term = "Hanging Piece" if not defended else "Loose Piece"
                        out.append({"term": term, "side": COLOR_NAME[color],
                                    "detail": f"{chess.piece_name(pt)} on {chess.square_name(s)} "
                                              f"is {'undefended and ' if not defended else ''}en prise",
                                    "squares": [chess.square_name(s)]})
    return out


def _forks(board, mover):
    """Detect forks by the side that just moved: a piece attacking 2+ higher-value
    (or royal) targets simultaneously."""
    out = []
    enemy = not mover
    for s in list(board.pieces(chess.KNIGHT, mover)) + list(board.pieces(chess.PAWN, mover)) + \
            list(board.pieces(chess.BISHOP, mover)) + list(board.pieces(chess.ROOK, mover)) + \
            list(board.pieces(chess.QUEEN, mover)):
        attacker = board.piece_at(s)
        targets = []
        for t in board.attacks(s):
            tp = board.piece_at(t)
            if tp and tp.color == enemy:
                if tp.piece_type == chess.KING or VALUES[tp.piece_type] >= VALUES[attacker.piece_type]:
                    # only count if the target isn't trivially defended for equal trades
                    if tp.piece_type == chess.KING or see(board, t, mover) >= 0:
                        targets.append(t)
        if len(targets) >= 2:
            out.append({"term": "Fork", "side": COLOR_NAME[mover],
                        "detail": f"{chess.piece_name(attacker.piece_type)} on {chess.square_name(s)} "
                                  f"forks {', '.join(chess.square_name(t) for t in targets)}",
                        "squares": [chess.square_name(s)] + [chess.square_name(t) for t in targets]})
    return out


def _pins(board):
    out = []
    for color in (chess.WHITE, chess.BLACK):
        enemy = not color
        for pt in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN, chess.PAWN):
            for s in board.pieces(pt, color):
                if board.is_pinned(color, s):
                    out.append({"term": "Pin", "side": COLOR_NAME[enemy],
                                "detail": f"{COLOR_NAME[color]}'s {chess.piece_name(pt)} on "
                                          f"{chess.square_name(s)} is pinned to its king",
                                "squares": [chess.square_name(s)]})
    return out


def _discovered_check(board, mover, last_move):
    """If the mover is giving check but the moved piece does not itself attack the
    king, it is a discovered check."""
    out = []
    if last_move is None or not board.is_check():
        return out
    enemy_king = board.king(board.turn)  # side to move is the checked side
    moved_to = last_move.to_square
    piece = board.piece_at(moved_to)
    if piece is None:
        return out
    gives_directly = enemy_king in board.attacks(moved_to)
    if not gives_directly:
        out.append({"term": "Discovered Check", "side": COLOR_NAME[mover],
                    "detail": "check delivered by uncovering a line, not by the moved piece",
                    "squares": [chess.square_name(moved_to)]})
    return out


def move_tactics(board: chess.Board, last_move: chess.Move | None,
                 board_before: chess.Board | None) -> list[dict]:
    """Tactics tied to the move that was just played (board is AFTER the move)."""
    out = []
    if last_move is None:
        return out
    mover = not board.turn  # the side that just moved
    if board.is_check():
        term = "Check"
        out.append({"term": term, "side": COLOR_NAME[mover],
                    "detail": "gives check", "squares": []})
        out += _discovered_check(board, mover, last_move)
    # capture / sacrifice
    if board_before is not None and board_before.is_capture(last_move):
        out.append({"term": "Capture", "side": COLOR_NAME[mover],
                    "detail": f"captured on {chess.square_name(last_move.to_square)}",
                    "squares": [chess.square_name(last_move.to_square)]})
    if board_before is not None:
        seev = see(board_before, last_move.to_square, mover) if board_before.piece_at(last_move.to_square) or board_before.is_en_passant(last_move) else None
        # sacrifice: moved to a square where SEE loses material for the mover
        if board_before.is_capture(last_move):
            if see(board_before, last_move.to_square, mover) < -60:
                out.append({"term": "Sacrifice", "side": COLOR_NAME[mover],
                            "detail": "gives up material for initiative/attack",
                            "squares": [chess.square_name(last_move.to_square)]})
    return out


# --------------------------------------------------------------------------- #
# Top-level
# --------------------------------------------------------------------------- #

def detect_concepts(board: chess.Board, last_move: chess.Move | None = None,
                    board_before: chess.Board | None = None) -> list[dict]:
    """Return the list of glossary concepts present in the position/transition."""
    tags: list[dict] = []
    tags += move_tactics(board, last_move, board_before)
    for _color in (chess.WHITE, chess.BLACK):
        tags += _forks(board, _color)
    tags += _pins(board)
    tags += hanging_pieces(board)
    tags += pawn_structure(board)
    tags += files_and_rooks(board)
    tags += outposts(board)
    tags += king_and_bishops(board)
    tags += center_and_space(board)

    # de-duplicate on (term, side, tuple(squares))
    seen = set()
    uniq = []
    for t in tags:
        key = (t["term"], t["side"], tuple(t.get("squares", [])))
        if key not in seen:
            seen.add(key)
            uniq.append(t)
    return uniq


def concept_terms(tags: list[dict]) -> list[str]:
    return sorted({t["term"] for t in tags})
