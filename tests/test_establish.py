"""
Tests for the establishment control-flow (app/game/establish.py).
Pure: scripted propose/gate/appeal/select callables, no engine or model.
Run: python -m tests.test_establish  -> ALL ESTABLISH TESTS PASSED / nonzero.
"""
from __future__ import annotations
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.game.establish import run_establishment

FAILS = []


def check(name, cond, detail=""):
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (f"  -- {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


ALL = ["a", "b", "c", "d", "e"]
SAN = {u: u.upper() for u in ALL}


def legal(u):
    return {"kind": "legal", "uci": u, "san": SAN[u], "rationale": "", "source": "llm"}


def illegals(n):
    return [{"kind": "illegal", "raw": f"bad{i}"} for i in range(n)]


def proposer(items):
    q = list(items)
    return lambda ruled_out, chosen: (q.pop(0) if q else {"kind": "illegal", "raw": "end"})


def gate_of(flagged):
    return lambda u: {"flagged": u in flagged, "reason": "hangs a pawn", "kinds": ["material_hang"]}


def appeal_of(defend=()):
    return lambda u, r: ({"agree": False, "plan": "real gambit"} if u in defend
                         else {"agree": True, "plan": ""})


def selector(picks):
    q = list(picks)
    return lambda pairs: ({"uci": q.pop(0), "reasoning": ""} if q else None)


RNG = random.Random(0)

print("Case 1: free proposal, three clean moves -> a full slate, no constraint")
r = run_establishment(ALL, SAN, 3, proposer([legal("a"), legal("b"), legal("c")]),
                      gate_of(set()), appeal_of(), selector([]), rng=RNG)
check("3 established", len(r["established"]) == 3, str([e["uci"] for e in r["established"]]))
check("not constrained", r["constrained"] is False)
check("no appeals", r["appeals_made"] == 0)

print("Case 2: a flagged move the proposer concedes is banned; a clean one fills in")
r = run_establishment(ALL, SAN, 2, proposer([legal("a"), legal("b"), legal("c")]),
                      gate_of({"a"}), appeal_of(), selector([]), rng=RNG)
check("a banned", "a" in r["banned"], str(r["banned"]))
check("slate is b,c", [e["uci"] for e in r["established"]] == ["b", "c"],
      str([e["uci"] for e in r["established"]]))

print("Case 3: 5 illegal proposals flip to constrained selection, which fills the slate")
r = run_establishment(ALL, SAN, 3, proposer(illegals(5)),
                      gate_of(set()), appeal_of(), selector(["a", "b", "c"]), rng=RNG)
check("constrained engaged", r["constrained"] is True)
check("filled via constrained", len(r["established"]) == 3, str([e["uci"] for e in r["established"]]))
check("all via constrained", all(e["via"] == "constrained" for e in r["established"]))

print("Case 4: in constrained mode a flagged pick is whittled out, next clean pick lands")
r = run_establishment(ALL, SAN, 1, proposer(illegals(5)),
                      gate_of({"a"}), appeal_of(), selector(["a", "b"]), rng=RNG)
check("a banned in constrained", "a" in r["banned"], str(r["banned"]))
check("landed on b", [e["uci"] for e in r["established"]] == ["b"],
      str([e["uci"] for e in r["established"]]))

print("Case 5: a defended (appealed) move is kept as an override, not banned")
r = run_establishment(ALL, SAN, 1, proposer([legal("a")]),
                      gate_of({"a"}), appeal_of(defend={"a"}), selector([]), rng=RNG)
check("a kept", [e["uci"] for e in r["established"]] == ["a"])
check("marked overridden", r["established"][0]["gate"].get("overridden") is True,
      str(r["established"][0]["gate"]))
check("not banned", "a" not in r["banned"])

print("Case 6: every move hangs and is conceded -> empty slate, all banned (coach plays least-bad)")
r = run_establishment(["a", "b"], {"a": "A", "b": "B"}, 3,
                      proposer([legal("a"), legal("b")]),
                      gate_of({"a", "b"}), appeal_of(), selector([]), rng=RNG)
check("empty slate", r["established"] == [], str(r["established"]))
check("both banned", set(r["banned"]) == {"a", "b"}, str(r["banned"]))
check("constrained (stalled out)", r["constrained"] is True)

print("Case 7: repeated duplicate proposals stall -> constrained takes over")
r = run_establishment(ALL, SAN, 2, proposer([legal("a")] + [legal("a")] * 10),
                      gate_of(set()), appeal_of(), selector(["b"]), rng=RNG)
check("a established once", [e["uci"] for e in r["established"]][0] == "a")
check("constrained after stall", r["constrained"] is True)
check("slate completed to 2", len(r["established"]) == 2, str([e["uci"] for e in r["established"]]))

print()
if FAILS:
    print(f"{len(FAILS)} ESTABLISH TEST(S) FAILED: " + ", ".join(FAILS)); sys.exit(1)
print("ALL ESTABLISH TESTS PASSED")
