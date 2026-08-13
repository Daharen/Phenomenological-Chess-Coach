"""
Tests for the deterministic candidate GATE (app/engine/gate.py + vision.hanging_after).
Pure python-chess. Run: python -m tests.test_gate  -> ALL GATE TESTS PASSED / nonzero.
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import chess
from app.engine.gate import gate_candidate
from app.engine.vision import hanging_after

FAILS = []


def check(name, cond, detail=""):
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (f"  -- {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def gate(fen, uci):
    b = chess.Board(fen)
    return gate_candidate(b, chess.Move.from_uci(uci))


F = "rnbqkbnr/ppp1pppp/8/3p4/3P4/3Q4/PPP1PPPP/RNB1KBNR b KQkq - 1 2"  # 1.d4 d5 2.Qd3, Black to move

print("Case 1: ...e5 is a full hang (undefended, attacked by the d4 pawn)")
g = gate(F, "e7e5")
check("e5 flagged", g["flagged"], str(g["kinds"]))
check("e5 flagged by vision_hang", "vision_hang" in g["kinds"], str(g["kinds"]))

print("Case 2: ...c5 is ALSO a full hang (the equivalence we established)")
g = gate(F, "c7c5")
check("c5 flagged", g["flagged"] and "vision_hang" in g["kinds"], str(g["kinds"]))

print("Case 3: ...Nc6 is clean (defended by b7, not attacked)")
g = gate(F, "b8c6")
check("Nc6 not flagged", not g["flagged"], str(g["kinds"]))

print("Case 4: hanging the queen (Qd1-a4 into ...bxa4) is flagged")
g = gate("rnbqkbnr/p1pppppp/8/1p6/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 2", "d1a4")
check("Qa4 flagged", g["flagged"] and "vision_hang" in g["kinds"], str(g["kinds"]))

print("Case 5: a DEFENDED attacked pawn is NOT a full hang (Caro-Kann ...d5)")
# after 1.e4 c6 2.d4, ...d5: d5 attacked by e4 pawn but defended by the c6 pawn
g = gate("rnbqkbnr/pp1ppppp/2p5/8/3PP3/8/PPP2PPP/RNBQKBNR b KQkq - 0 2", "d7d5")
check("d5 not a full hang", not g["flagged"], str(g["kinds"]) + " " + g["reason"])
check("hanging_after(d5) is empty",
      hanging_after(chess.Board("rnbqkbnr/pp1ppppp/2p5/8/3PP3/8/PPP2PPP/RNBQKBNR b KQkq - 0 2"),
                    chess.Move.from_uci("d7d5")) == [])

print("Case 6: walking into a knight royal fork is flagged by the fork check")
# White to move; Black Nb4 threatens ...Nc2+ forking Ke1+Ra1. A passive g3 allows it.
g = gate("4k3/8/8/8/1n6/8/6P1/R3K3 w - - 0 1", "g2g3")
check("g3 flagged", g["flagged"], str(g["kinds"]) + " " + g["reason"])
check("flagged by fork", "fork" in g["kinds"], str(g["kinds"]))

print()
if FAILS:
    print(f"{len(FAILS)} GATE TEST(S) FAILED: " + ", ".join(FAILS)); sys.exit(1)
print("ALL GATE TESTS PASSED")
