"""
Tests for the deterministic piece-vision map (app/engine/vision.py).
Run:  python -m tests.test_vision   Prints ALL VISION TESTS PASSED / non-zero on fail.
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import chess
from app.engine.vision import sees, piece_lines, vision_map

FAILS = []


def check(name, cond, detail=""):
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (f"  -- {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def sqs(recs):
    return sorted(r["square"] for r in recs)


# The disputed position: after 1.d4 d5 2.Qd3 c5 (Black just played c5).
b = chess.Board("rnbqkbnr/pp2pppp/8/2pp4/3P4/3Q4/PPP1PPPP/RNB1KBNR w KQkq - 0 3")

print("Case 1: the d4 pawn sees (attacks) the c5 pawn")
check("d4 sees c5", sees(b, chess.D4, chess.C5))
check("c5 attacked_by includes d4",
      "d4" in sqs([r for r in vision_map(b) if r["square"] == "c5"][0]["attacked_by"]))

print("Case 2: the c5 pawn has NO black defender")
c5 = [r for r in vision_map(b) if r["square"] == "c5"][0]
check("c5 defended_by is empty", c5["defended_by"] == [], str(c5["defended_by"]))

print("Case 3: the bishop on c8 does NOT see c5 (same file; bishops move diagonally)")
check("Bc8 does not see c5", not sees(b, chess.C8, chess.C5))
bc8 = piece_lines(b, chess.C8)
check("Bc8 defends only b7 here", sqs(bc8["defends"]) == ["b7"], str(sqs(bc8["defends"])))

print("Case 4: the white queen on d3 does NOT see c5 (knight-shaped offset)")
check("Qd3 does not see c5", not sees(b, chess.D3, chess.C5))

print("Case 5: a rook's lines are blocked (no x-ray) -- Ra1 stops at its own a2 and b1")
r_a1 = piece_lines(b, chess.A1)
# up the a-file it stops at its own pawn a2; along rank 1 it stops at its own knight b1
check("Ra1 defends exactly a2 and b1 (no x-ray beyond either blocker)",
      sqs(r_a1["defends"]) == ["a2", "b1"] and r_a1["attacks"] == [], str(sqs(r_a1["defends"])))

print("Case 6: symmetry -- if X attacks Y then Y is attacked_by X")
vm = {r["square"]: r for r in vision_map(b)}
ok = True
for r in vm.values():
    for a in r["attacks"]:
        tgt = vm.get(a["square"])
        if not tgt or r["square"] not in [x["square"] for x in tgt["attacked_by"]]:
            ok = False
check("attacks / attacked_by are consistent", ok)

print()
if FAILS:
    print(f"{len(FAILS)} VISION TEST(S) FAILED: " + ", ".join(FAILS)); sys.exit(1)
print("ALL VISION TESTS PASSED")
