"""
Tests for the appeal step (roadmap module 3) -- confront-and-agree / re-pick.

Pure control-flow: fake assess/confront/reselect callables, no chess engine or
LLM needed. Run:  python -m tests.test_appeal   (or python tests/test_appeal.py)
Prints ALL APPEAL TESTS PASSED / exits non-zero on failure.
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.game.appeal import run_appeal

FAILS = []


def check(name, cond, detail=""):
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (f"  -- {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def flag_for(bad_moves, reason="it hangs a knight (net -520cp) a safe -200cp sibling keeps"):
    """assess: a move is flagged iff it is in bad_moves."""
    def assess(chosen, slate):
        return {"reason": reason if chosen in bad_moves else None,
                "deteval": {"hangs": chosen in bad_moves}, "threats": None}
    return assess


def confront_const(agree, plan=""):
    def confront(chosen, reason):
        return {"agree": agree, "plan": plan}
    return confront


def reselect_order(order):
    """reselect: return the first move in `order` still available."""
    def reselect(remaining):
        for u in order:
            if u in remaining:
                return u
        return remaining[0]
    return reselect


print("Case 1: agent concedes a hanging move -> re-picks a safe sibling")
r = run_appeal("ne4", ["ne4", "a6", "nc6"], flag_for({"ne4"}),
               confront_const(True), reselect_order(["a6", "nc6"]), max_appeals=2)
check("changed", r["changed"] is True, str(r["changed"]))
check("final is a safe sibling", r["chosen_uci"] == "a6", r["chosen_uci"])
check("hanging move banned", r["bad"] == ["ne4"], str(r["bad"]))
check("one round, conceded", len(r["rounds"]) == 1 and r["rounds"][0]["outcome"] == "conceded",
      str(r["rounds"]))

print("Case 2: agent defends with a concrete plan -> move stands")
r = run_appeal("ne4", ["ne4", "a6", "nc6"], flag_for({"ne4"}),
               confront_const(False, "real sac: after Nxe4 dxe4 the pinned bishop falls"),
               reselect_order(["a6", "nc6"]), max_appeals=2)
check("not changed", r["changed"] is False, str(r["changed"]))
check("original stands", r["chosen_uci"] == "ne4", r["chosen_uci"])
check("round marked defended", r["rounds"][0]["outcome"] == "defended", str(r["rounds"]))

print("Case 3: clean pick -> no appeal at all")
r = run_appeal("a6", ["a6", "nc6"], flag_for(set()),
               confront_const(True), reselect_order(["nc6"]), max_appeals=2)
check("no rounds", r["rounds"] == [], str(r["rounds"]))
check("not changed", r["changed"] is False, str(r["changed"]))
check("same move", r["chosen_uci"] == "a6", r["chosen_uci"])

print("Case 4: bounded -- all flagged, always agrees -> stops at max_appeals")
r = run_appeal("m1", ["m1", "m2", "m3", "m4", "m5"], flag_for({"m1", "m2", "m3", "m4", "m5"}),
               confront_const(True), reselect_order(["m2", "m3", "m4", "m5"]), max_appeals=2)
check("at most max_appeals rounds", len(r["rounds"]) <= 2, str(len(r["rounds"])))
check("banned exactly the appealed moves", len(r["bad"]) == len(r["rounds"]), str(r["bad"]))

print("Case 5: concede but no alternative left -> move stands, flagged")
r = run_appeal("ne4", ["ne4"], flag_for({"ne4"}),
               confront_const(True), reselect_order([]), max_appeals=2)
check("not changed (nothing to swap to)", r["changed"] is False, str(r["changed"]))
check("still the flagged move", r["chosen_uci"] == "ne4", r["chosen_uci"])
check("round notes no alternative", "no alternative" in (r["rounds"][0]["outcome"] or ""),
      str(r["rounds"]))

print("Case 6: disagree with NO plan collapses to a concession -> re-picks")
r = run_appeal("ne4", ["ne4", "a6"], flag_for({"ne4"}),
               confront_const(False, ""), reselect_order(["a6"]), max_appeals=2)
check("re-picked on empty-plan disagree", r["chosen_uci"] == "a6", r["chosen_uci"])
check("changed", r["changed"] is True, str(r["changed"]))

print()
if FAILS:
    print(f"{len(FAILS)} APPEAL TEST(S) FAILED: " + ", ".join(FAILS))
    sys.exit(1)
print("ALL APPEAL TESTS PASSED")
