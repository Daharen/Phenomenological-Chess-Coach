"""
Tests for the deterministic evaluator, module 2 (fork / double-attack threats).

Run:  /home/claude/testvenv/bin/python tests/test_threats.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess
from app.engine.threats import best_fork_threat, move_threat, assess_candidates

FAILS = []


def check(name, cond, detail=""):
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (f"  -- {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


# --------------------------------------------------------------------------- #
print("Case 1: knight ROYAL fork (check + rook) is detected")
# Black to move: Nb4-c2+ forks white Ke1 and Ra1 (rook undefended, knight safe).
b1 = chess.Board("6k1/8/8/8/1n6/8/8/R3K3 b - - 0 1")
t1 = best_fork_threat(b1)
check("a fork is found", t1 is not None, str(t1))
check("it is the Nc2+ fork", t1 and t1["forker_to"] == "c2", str(t1))
check("it is flagged as check", t1 and t1["is_check"] is True, str(t1))
check("threat ~= a rook (500)", t1 and t1["gain_cp"] == 500, str(t1 and t1["gain_cp"]))

# --------------------------------------------------------------------------- #
print("Case 2: QUIET knight double attack on two loose rooks (no check available)")
# Black to move: Nf6-d5 forks the c7 and f4 rooks. They are undefended and do not
# guard each other; the knight lands safely and no equal check-fork exists, so the
# result is a genuine QUIET fork worth a whole rook.
b2 = chess.Board("7k/2R5/5n2/8/5R2/8/8/7K b - - 0 1")
t2 = best_fork_threat(b2)
check("a fork is found", t2 is not None, str(t2))
check("not a check", t2 and t2["is_check"] is False, str(t2))
check("it is the Nd5 fork", t2 and t2["forker_to"] == "d5", str(t2))
check("threat ~= a rook (500)", t2 and t2["gain_cp"] == 500, str(t2 and t2["gain_cp"]))

# --------------------------------------------------------------------------- #
print("Case 3: pawn fork of two knights")
# Black to move: e5-e4 attacks the d3 and f3 knights (both undefended); pawn safe.
b3 = chess.Board("6k1/8/8/4p3/8/3N1N2/8/6K1 b - - 0 1")
t3 = best_fork_threat(b3)
check("a fork is found", t3 is not None, str(t3))
check("threat ~= a knight (320)", t3 and t3["gain_cp"] == 320, str(t3 and t3["gain_cp"]))

# --------------------------------------------------------------------------- #
print("Case 4: a 'fork' that hangs the forker is NOT flagged")
# Same geometry as case 2 but the a4 piece is a ROOK that guards d4: Qd4 would be
# met by Rxd4 winning the queen -> refuted.
b4 = chess.Board("6k1/3q4/8/8/R6R/8/8/6K1 b - - 0 1")
t4 = best_fork_threat(b4)
check("no fork (queen would hang to Rxd4)", t4 is None, str(t4))

# --------------------------------------------------------------------------- #
print("Case 5: a quiet safe move allows no fork")
b5 = chess.Board()  # startpos
mt = move_threat(b5, chess.Move.from_uci("e2e4"))
check("1.e4 allows no fork", mt["threat_cp"] == 0, str(mt))

# --------------------------------------------------------------------------- #
print("Case 6: single attack on one piece is not a fork")
# Black to move: Nb4-d3+ only gives check (forks nothing else) -> not flagged.
b6 = chess.Board("6k1/8/8/8/1n6/8/8/4K3 b - - 0 1")
t6 = best_fork_threat(b6)
check("lone check is not a fork", t6 is None, str(t6))

# --------------------------------------------------------------------------- #
print("Case 7: assess_candidates flags walking into a fork vs a safe sibling")
# White to move.  h2h3 leaves the king on e1 -> black plays Nc2+ forking K+R.
# e1f2 steps the king away -> Nc2 then only hits the rook (single attack, no fork).
bc = chess.Board("6k1/8/8/8/1n6/8/7P/R3K3 w - - 0 1")
a = assess_candidates(bc, ["h2h3", "e1f2"], chosen_uci="h2h3", warn_threshold=150)
per = {c["uci"]: c["threat_cp"] for c in a["per_candidate"]}
print(f"      threats: h2h3={per.get('h2h3')}  e1f2={per.get('e1f2')}")
check("h2h3 walks into ~500cp fork", per.get("h2h3") == 500, str(per))
check("e1f2 avoids the fork (0)", per.get("e1f2") == 0, str(per))
check("chosen flagged as has_threat", a["has_threat"] is True)
check("flagged as avoidable", a["avoidable"] is True)
check("safe_alt is the king step", a["safe_alt"] == "e1f2", a["safe_alt"])

# choosing the SAFE move raises no alarm
a2 = assess_candidates(bc, ["h2h3", "e1f2"], chosen_uci="e1f2", warn_threshold=150)
check("no alarm when the safe move is chosen", a2["has_threat"] is False, str(a2["chosen_cp"]))

# --------------------------------------------------------------------------- #
print()
if FAILS:
    print(f"FAILED ({len(FAILS)}): " + ", ".join(FAILS))
    sys.exit(1)
print("ALL THREAT TESTS PASSED")
