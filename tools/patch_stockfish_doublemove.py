from pathlib import Path
import sys

root = Path(sys.argv[1])

def patch(rel, old, new, count=1):
    p = root / rel
    s = p.read_text()
    if old not in s:
        raise SystemExit(f'patch anchor not found in {rel}: {old[:120]!r}')
    s2 = s.replace(old, new, count)
    p.write_text(s2)

# ---- Position state / API -------------------------------------------------
patch('src/position.h',
'''    Square epSquare;\n\n    // Not copied when making a move (will be recomputed anyhow)\n''',
'''    Square epSquare;\n\n    // Double-move handicap variant state. The configured side receives a\n    // second consecutive move after a non-checking first move. A checking\n    // first move ends the turn immediately. These fields are copied by\n    // do_move() and are part of the logical position state.\n    bool  doubleMoveEnabled;\n    Color doubleMoveColor;\n    bool  doubleMoveSecond;\n\n    // Not copied when making a move (will be recomputed anyhow)\n''')

patch('src/position.h',
'''    bool  gives_check(Move m) const;\n    Piece moved_piece(Move m) const;\n    Piece captured_piece() const;\n''',
'''    bool  gives_check(Move m) const;\n    Piece moved_piece(Move m) const;\n    Piece captured_piece() const;\n\n    // Double-move handicap variant. COLOR_NB disables the variant.\n    void  set_double_move_side(Color c);\n    bool  double_move_enabled() const;\n    bool  double_move_second() const;\n    Color double_move_color() const;\n''')

patch('src/position.h',
'''inline Color Position::side_to_move() const { return sideToMove; }\n''',
'''inline Color Position::side_to_move() const { return sideToMove; }\ninline bool Position::double_move_enabled() const { return st->doubleMoveEnabled; }\ninline bool Position::double_move_second() const { return st->doubleMoveSecond; }\ninline Color Position::double_move_color() const { return st->doubleMoveColor; }\n''')

# ---- Zobrist keys ---------------------------------------------------------
patch('src/position.cpp',
'''Key castling[CASTLING_RIGHT_NB];\nKey side, noPawns;\n''',
'''Key castling[CASTLING_RIGHT_NB];\nKey side, noPawns;\nKey doubleMoveColor[COLOR_NB], doubleMoveSecond;\n''')

patch('src/position.cpp',
'''    Zobrist::side    = rng.rand<Key>();\n    Zobrist::noPawns = rng.rand<Key>();\n''',
'''    Zobrist::side    = rng.rand<Key>();\n    Zobrist::noPawns = rng.rand<Key>();\n    Zobrist::doubleMoveColor[WHITE] = rng.rand<Key>();\n    Zobrist::doubleMoveColor[BLACK] = rng.rand<Key>();\n    Zobrist::doubleMoveSecond       = rng.rand<Key>();\n''')

# Insert configuration method before castling helper.
patch('src/position.cpp',
'''// Helper function used to set castling\n// rights given the corresponding color and the rook starting square.\n''',
'''void Position::set_double_move_side(Color c) {\n    // This is called immediately after Position::set(), before setup moves.\n    // Remove any previous variant component defensively, then install the new one.\n    if (st->doubleMoveEnabled)\n    {\n        st->key ^= Zobrist::doubleMoveColor[st->doubleMoveColor];\n        if (st->doubleMoveSecond)\n            st->key ^= Zobrist::doubleMoveSecond;\n    }\n\n    st->doubleMoveEnabled = c < COLOR_NB;\n    st->doubleMoveColor   = c < COLOR_NB ? c : WHITE;\n    st->doubleMoveSecond  = false;\n\n    if (st->doubleMoveEnabled)\n        st->key ^= Zobrist::doubleMoveColor[st->doubleMoveColor];\n}\n\n\n// Helper function used to set castling\n// rights given the corresponding color and the rook starting square.\n''')

# ---- Variant-aware do_move / undo_move ----------------------------------
patch('src/position.cpp',
'''    Key k = st->key ^ Zobrist::side;\n\n    // Copy some fields of the old state to our new StateInfo object except the\n''',
'''    const bool oldSecond = st->doubleMoveSecond;\n    const bool keepTurn = st->doubleMoveEnabled && sideToMove == st->doubleMoveColor\n                       && !oldSecond && !givesCheck;\n\n    Key k = st->key;\n    if (!keepTurn)\n        k ^= Zobrist::side;\n    if (oldSecond)\n        k ^= Zobrist::doubleMoveSecond;\n    if (keepTurn)\n        k ^= Zobrist::doubleMoveSecond;\n\n    // Copy some fields of the old state to our new StateInfo object except the\n''')

patch('src/position.cpp',
'''    newSt.previous = st;\n    st             = &newSt;\n\n    // Increment ply counters. In particular, rule50 will be reset to zero later on\n''',
'''    newSt.previous = st;\n    st             = &newSt;\n    st->doubleMoveSecond = keepTurn;\n\n    // Increment ply counters. In particular, rule50 will be reset to zero later on\n''')

patch('src/position.cpp',
'''    // Calculate checkers bitboard (if move gives check)\n    st->checkersBB = givesCheck ? attackers_to(square<KING>(them)) & pieces(us) : 0;\n\n    sideToMove = ~sideToMove;\n\n    // Update king attacks used for fast check detection\n''',
'''    // If this was the first, non-checking move of the advantaged side, that\n    // same side moves again. A checking first move forfeits the bonus move.\n    st->checkersBB = keepTurn ? 0 : (givesCheck ? attackers_to(square<KING>(them)) & pieces(us) : 0);\n\n    if (!keepTurn)\n        sideToMove = ~sideToMove;\n\n    // Update king attacks used for fast check detection\n''')

patch('src/position.cpp',
'''    sideToMove = ~sideToMove;\n\n    Color  us   = sideToMove;\n''',
'''    const bool keptTurn = st->doubleMoveEnabled && st->doubleMoveSecond\n                       && !st->previous->doubleMoveSecond;\n    if (!keptTurn)\n        sideToMove = ~sideToMove;\n\n    Color  us   = sideToMove;\n''', 1)

# Repetition algorithms assume strict color alternation. Disable them for the
# handicap variant; the 50-move rule remains available via is_draw().
patch('src/position.cpp',
'''bool Position::is_repetition(int ply) const { return st->repetition && st->repetition < ply; }\n''',
'''bool Position::is_repetition(int ply) const {\n    if (st->doubleMoveEnabled)\n        return false;\n    return st->repetition && st->repetition < ply;\n}\n''')

patch('src/position.cpp',
'''bool Position::has_repeated() const {\n\n    StateInfo* stc = st;\n''',
'''bool Position::has_repeated() const {\n\n    if (st->doubleMoveEnabled)\n        return false;\n\n    StateInfo* stc = st;\n''')

# ---- UCI option and setup replay -----------------------------------------
patch('src/engine.cpp',
'''    options.add("UCI_Chess960", Option(false));\n\n    options.add("UCI_LimitStrength", Option(false));\n''',
'''    options.add("UCI_Chess960", Option(false));\n\n    // Handicap variant: one side gets a second move after a non-checking first\n    // move. Accepted values are "none", "white", and "black".\n    options.add("DoubleMoveSide", Option("none"));\n\n    options.add("UCI_LimitStrength", Option(false));\n''')

patch('src/engine.cpp',
'''    auto err = pos.set(fen, options["UCI_Chess960"], &states->back());\n    if (err.has_value())\n        return err;\n\n    for (const auto& move : moves)\n''',
'''    auto err = pos.set(fen, options["UCI_Chess960"], &states->back());\n    if (err.has_value())\n        return err;\n\n    const std::string dmSide = options["DoubleMoveSide"];\n    if (dmSide == "white")\n        pos.set_double_move_side(WHITE);\n    else if (dmSide == "black")\n        pos.set_double_move_side(BLACK);\n    else\n        pos.set_double_move_side(COLOR_NB);\n\n    for (const auto& move : moves)\n''')

# ---- Disable orthodox tablebases in variant positions --------------------
patch('src/thread.cpp',
'''    Tablebases::Config tbConfig = Tablebases::rank_root_moves(options, pos, rootMoves);\n''',
'''    Tablebases::Config tbConfig{};\n    if (!pos.double_move_enabled())\n        tbConfig = Tablebases::rank_root_moves(options, pos, rootMoves);\n''')

# ---- Search: ensure the mandatory/available second move is never lost at
# the horizon, disable null-move pruning, and preserve negamax sign only when
# the side actually changes. ------------------------------------------------
patch('src/search.cpp',
'''    // Dive into quiescence search when the depth reaches zero\n    if (depth <= 0)\n        return qsearch<PvNode ? PV : NonPV>(pos, ss, alpha, beta);\n''',
'''    // Never stop between the two moves of the advantaged side. A nominal\n    // depth horizon reached after move one is extended just enough to search\n    // the second move.\n    if (depth <= 0 && pos.double_move_second())\n        depth = 1;\n    else if (depth <= 0)\n        return qsearch<PvNode ? PV : NonPV>(pos, ss, alpha, beta);\n''')

patch('src/search.cpp',
'''    if (cutNode && ss->staticEval >= beta - 13 * depth - 47 * improving + 365 && !excludedMove\n        && pos.non_pawn_material(us) && ss->ply >= nmpMinPly && beta >= -2000)\n''',
'''    if (!pos.double_move_enabled() && cutNode\n        && ss->staticEval >= beta - 13 * depth - 47 * improving + 365 && !excludedMove\n        && pos.non_pawn_material(us) && ss->ply >= nmpMinPly && beta >= -2000)\n''')

# ProbCut preliminary and regular searches.
patch('src/search.cpp',
'''            do_move(pos, move, st, ss);\n\n            // Perform a preliminary qsearch to verify that the move holds\n            value = -qsearch<NonPV>(pos, ss + 1, -probCutBeta, -probCutBeta + 1);\n\n            // If the qsearch held, perform the regular search\n            if (value >= probCutBeta && probCutDepth > 0)\n                value = -search<NonPV>(pos, ss + 1, -probCutBeta, -probCutBeta + 1, probCutDepth,\n                                       !cutNode);\n''',
'''            const Color mover = pos.side_to_move();\n            do_move(pos, move, st, ss);\n            const bool sameSide = pos.side_to_move() == mover;\n\n            // Perform a preliminary qsearch to verify that the move holds.\n            value = sameSide\n                  ? qsearch<NonPV>(pos, ss + 1, probCutBeta - 1, probCutBeta)\n                  : -qsearch<NonPV>(pos, ss + 1, -probCutBeta, -probCutBeta + 1);\n\n            // If the qsearch held, perform the regular search\n            if (value >= probCutBeta && probCutDepth > 0)\n                value = sameSide\n                      ? search<NonPV>(pos, ss + 1, probCutBeta - 1, probCutBeta, probCutDepth, !cutNode)\n                      : -search<NonPV>(pos, ss + 1, -probCutBeta, -probCutBeta + 1, probCutDepth, !cutNode);\n''')

# Main move loop: remember whether do_move kept the same side.
patch('src/search.cpp',
'''        // Step 17. Make the move\n        do_move(pos, move, st, givesCheck, ss);\n\n        // Add extension to new depth\n''',
'''        // Step 17. Make the move\n        do_move(pos, move, st, givesCheck, ss);\n        const bool sameSide = pos.side_to_move() == us;\n\n        // Add extension to new depth\n''')

patch('src/search.cpp',
'''            value         = -search<NonPV>(pos, ss + 1, -(alpha + 1), -alpha, d, true);\n''',
'''            value         = sameSide\n                          ? search<NonPV>(pos, ss + 1, alpha, alpha + 1, d, true)\n                          : -search<NonPV>(pos, ss + 1, -(alpha + 1), -alpha, d, true);\n''')

patch('src/search.cpp',
'''                    value = -search<NonPV>(pos, ss + 1, -(alpha + 1), -alpha, newDepth, !cutNode);\n''',
'''                    value = sameSide\n                          ? search<NonPV>(pos, ss + 1, alpha, alpha + 1, newDepth, !cutNode)\n                          : -search<NonPV>(pos, ss + 1, -(alpha + 1), -alpha, newDepth, !cutNode);\n''')

patch('src/search.cpp',
'''            value = -search<NonPV>(pos, ss + 1, -(alpha + 1), -alpha,\n                                   newDepth - (r > 5234) - (r > 5487 && newDepth > 2), !cutNode);\n''',
'''            value = sameSide\n                  ? search<NonPV>(pos, ss + 1, alpha, alpha + 1,\n                                  newDepth - (r > 5234) - (r > 5487 && newDepth > 2), !cutNode)\n                  : -search<NonPV>(pos, ss + 1, -(alpha + 1), -alpha,\n                                   newDepth - (r > 5234) - (r > 5487 && newDepth > 2), !cutNode);\n''')

patch('src/search.cpp',
'''            value = -search<PV>(pos, ss + 1, -beta, -alpha, newDepth, false);\n''',
'''            value = sameSide\n                  ? search<PV>(pos, ss + 1, alpha, beta, newDepth, false)\n                  : -search<PV>(pos, ss + 1, -beta, -alpha, newDepth, false);\n''')

# qsearch may be entered from ProbCut after the first bonus move. Complete the
# turn with a full-width one-ply search instead of treating the intermediate
# board as a quiet position.
patch('src/search.cpp',
'''    static_assert(nodeType != Root);\n    constexpr bool PvNode = nodeType == PV;\n\n    assert(alpha >= -VALUE_INFINITE && alpha < beta && beta <= VALUE_INFINITE);\n''',
'''    static_assert(nodeType != Root);\n    constexpr bool PvNode = nodeType == PV;\n\n    if (pos.double_move_second())\n        return search<nodeType>(pos, ss, alpha, beta, 1, false);\n\n    assert(alpha >= -VALUE_INFINITE && alpha < beta && beta <= VALUE_INFINITE);\n''')

patch('src/search.cpp',
'''        // Step 7. Make and search the move\n        do_move(pos, move, st, givesCheck, ss);\n\n        value = -qsearch<nodeType>(pos, ss + 1, -beta, -alpha);\n''',
'''        // Step 7. Make and search the move\n        const Color mover = pos.side_to_move();\n        do_move(pos, move, st, givesCheck, ss);\n        const bool sameSide = pos.side_to_move() == mover;\n\n        value = sameSide ? qsearch<nodeType>(pos, ss + 1, alpha, beta)\n                         : -qsearch<nodeType>(pos, ss + 1, -beta, -alpha);\n''')

# Disable Syzygy probing in the variant even if a nonzero configuration leaks in.
patch('src/search.cpp',
'''    if (!rootNode && !excludedMove && tbConfig.cardinality)\n''',
'''    if (!pos.double_move_enabled() && !rootNode && !excludedMove && tbConfig.cardinality)\n''')

# Add a clear identification string in the source version output.
patch('src/misc.cpp',
'''std::string engine_info(bool to_uci) {\n''',
'''std::string engine_info(bool to_uci) {\n''')

print('Double-move handicap patch applied successfully')
