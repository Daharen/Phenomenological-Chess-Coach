"""
Tests for the deterministic evaluator, module 1 (material safety).

Run:  /home/claude/testvenv/bin/python tests/test_deteval.py
Exits non-zero on any failed assertion; prints a per-case trace so we can see
exactly what net_cp the module assigns to each move.
"""
from __future__ import annotations

import os
import sys

# make `app` importable when run from the project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess
from app.engine.deteval import move_safety, best_capture_gain, assess_candidates, material_cp
from app.engine.concepts import see

FAILS = []


def check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print(f"  [{tag}] {name}" + (f"  -- {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def show(board, ucis):
    for u in ucis:
        ms = move_safety(board, chess.Move.from_uci(u))
        print(f"      {u:6s} {ms['san']:6s} net={ms['net_cp']:+5d} loss={ms['loss_cp']:4d}"
              f" {'@'+ms['loss_sq'] if ms['loss_sq'] else ''}"
              + (f"  | {ms['sentence']}" if ms['sentence'] else ""))


# --------------------------------------------------------------------------- #
print("Case 0: static-exchange evaluation itself (regression for the fold bug)")
# pawn x undefended knight -> +320 ; pawn x undefended queen -> +900
b0a = chess.Board("4k3/8/8/8/5p2/6N1/8/4K3 b - - 0 1")
check("SEE pawn x undefended knight = 320", see(b0a, chess.G3, chess.BLACK) == 320,
      f"got {see(b0a, chess.G3, chess.BLACK)}")
b0b = chess.Board("4k3/8/8/8/5p2/6Q1/8/4K3 b - - 0 1")
check("SEE pawn x undefended queen = 900", see(b0b, chess.G3, chess.BLACK) == 900,
      f"got {see(b0b, chess.G3, chess.BLACK)}")
# knight x undefended pawn -> +100 (the old fold returned -220 here)
b0c = chess.Board("4k3/8/8/8/8/5p2/8/4K1N1 w - - 0 1")
check("SEE knight x undefended pawn = 100", see(b0c, chess.F3, chess.WHITE) == 100,
      f"got {see(b0c, chess.F3, chess.WHITE)}")
# rook x pawn defended by a pawn -> +100 - 500 = -400 (losing the exchange)
b0d = chess.Board("4k3/8/2p5/3p4/8/8/8/3RK3 w - - 0 1")
check("SEE Rxd5 into a pawn recapture = -400", see(b0d, chess.D5, chess.WHITE) == -400,
      f"got {see(b0d, chess.D5, chess.WHITE)}")

# --------------------------------------------------------------------------- #
print("Case 1: knight walks onto a square guarded by a pawn (hangs) vs a safe square")
# White Ne2; black pawn f4 guards g3 and e3. Ng3 and Ne3 hang; Nc3/Nc1/Ng1 are safe.
b1 = chess.Board("4k3/8/8/8/5p2/8/4N3/4K3 w - - 0 1")
show(b1, ["e2g3", "e2c3", "e2c1"])
ng3 = move_safety(b1, chess.Move.from_uci("e2g3"))
nc3 = move_safety(b1, chess.Move.from_uci("e2c3"))
check("Ng3 hangs the knight (loss 320)", ng3["loss_cp"] == 320, f"loss={ng3['loss_cp']}")
check("Nc3 is safe (no loss)", nc3["loss_cp"] == 0, f"loss={nc3['loss_cp']}")
check("Ng3 net is >=300cp worse than Nc3", nc3["net_cp"] - ng3["net_cp"] >= 300,
      f"nc3={nc3['net_cp']} ng3={ng3['net_cp']}")

a1 = assess_candidates(b1, ["e2g3", "e2c3", "e2c1"], chosen_uci="e2g3", hang_threshold=100)
check("assess flags the hang", a1["hangs"] is True)
check("safe_alt is a genuinely safe sibling", a1["safe_alt"] in ("e2c3", "e2c1"),
      f"safe_alt={a1['safe_alt']}")
check("no false-positive when the SAFE move is chosen",
      assess_candidates(b1, ["e2g3", "e2c3", "e2c1"], "e2c3")["hangs"] is False)

# --------------------------------------------------------------------------- #
print("Case 2: a free capture must outrank a quiet retreat")
# White Qd4, black Qd5 hanging (undefended). Qxd5 wins the queen; Qh4 leaves it.
b2 = chess.Board("4k3/8/8/3q4/3Q4/8/8/4K3 w - - 0 1")
show(b2, ["d4d5", "d4h4"])
qxd5 = move_safety(b2, chess.Move.from_uci("d4d5"))
qh4 = move_safety(b2, chess.Move.from_uci("d4h4"))
check("Qxd5 wins ~900 (free queen)", qxd5["net_cp"] - qh4["net_cp"] >= 800,
      f"qxd5={qxd5['net_cp']} qh4={qh4['net_cp']}")
check("Qxd5 itself does not hang", qxd5["loss_cp"] == 0, f"loss={qxd5['loss_cp']}")
a2 = assess_candidates(b2, ["d4d5", "d4h4"], chosen_uci="d4h4")
check("choosing the retreat over a free queen is flagged", a2["hangs"] is True)
check("safe_alt points at the capture", a2["safe_alt"] == "d4d5", f"safe_alt={a2['safe_alt']}")

# --------------------------------------------------------------------------- #
print("Case 3: capturing a DEFENDED piece with a bigger piece is a losing trade")
# White Rd1 x pawn d5, but d5 pawn is defended by pawn c6 -> Rxd5 loses the exchange.
b3 = chess.Board("4k3/8/2p5/3p4/8/8/8/3RK3 w - - 0 1")
show(b3, ["d1d5", "d1d4", "d1a1"])
rxd5 = move_safety(b3, chess.Move.from_uci("d1d5"))
check("Rxd5 (rook takes pawn, recaptured) loses material", rxd5["net_cp"] < 0,
      f"net={rxd5['net_cp']}")
a3 = assess_candidates(b3, ["d1d5", "d1d4", "d1a1"], chosen_uci="d1d5")
check("the losing capture is flagged as a hang", a3["hangs"] is True)

# --------------------------------------------------------------------------- #
print("Case 4: pinned attacker cannot 'win' material (legal-capture driven)")
# Black knight e4 is pinned to the black king (Re1 behind it, black Ke8... actually
# build an absolute pin): White Re1, black Ne4? Use: white rook e1, black bishop e5
# pinned to black king e8; that bishop cannot capture a white pawn on d4.
b4 = chess.Board("4k3/8/8/4b3/3P4/8/8/4RK2 b - - 0 1")
# It is black to move; Be5 is pinned by Re1 to Ke8. Bxd4 would be illegal (exposes king).
bxd4_legal = chess.Move.from_uci("e5d4") in b4.legal_moves
check("pinned bishop's capture Bxd4 is illegal", bxd4_legal is False)
gain, sq = best_capture_gain(b4, b4.turn)
check("best_capture_gain ignores the pinned (illegal) capture", gain == 0,
      f"gain={gain} sq={sq}")

# --------------------------------------------------------------------------- #
print("Case 5: quiet move in a balanced position nets the standing material, no hang")
b5 = chess.Board()  # startpos
e4 = move_safety(b5, chess.Move.from_uci("e2e4"))
check("1.e4 nets 0 and hangs nothing", e4["net_cp"] == 0 and e4["loss_cp"] == 0,
      f"net={e4['net_cp']} loss={e4['loss_cp']}")
a5 = assess_candidates(b5, ["e2e4", "d2d4", "g1f3"], chosen_uci="e2e4")
check("no hang flagged among sound opening moves", a5["hangs"] is False)

# --------------------------------------------------------------------------- #
print()
if FAILS:
    print(f"FAILED ({len(FAILS)}): " + ", ".join(FAILS))
    sys.exit(1)
print("ALL DETEVAL TESTS PASSED")
