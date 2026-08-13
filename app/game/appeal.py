"""
The Appeal step (roadmap module 3).

After a move is selected AND the deterministic evaluator (module 1 material
safety / module 2 fork threats) flags it, the *same* player agent is confronted
once: "your move is flagged because R -- do you agree it's a mistake?"

  * Agree (or disagree with no concrete plan)  -> the move joins a per-turn
    bad[] list and a fresh pick is made from the remaining slate; the loop
    re-assesses the new pick and may appeal again.
  * Disagree WITH a concrete plan              -> the move stands (the
    phenomenological engine may see compensation a shallow flag misses).

Bounded by `max_appeals` rounds per turn. This module is pure control-flow: the
board/LLM/engine specifics are injected as three callables so it can be unit
tested without a chess engine or a model (see tests/test_appeal.py).

Injected callables
------------------
  assess(chosen_uci, slate_ucis) -> {"reason": str|None, "deteval": dict|None,
                                     "threats": dict|None}
      Deterministic verdict for `chosen_uci` compared against `slate_ucis`.
      reason is a human sentence when the move is flagged, else None.
  confront(chosen_uci, reason)   -> {"agree": bool, "plan": str}
      The agent's one reply. A bare disagree with no plan should already be
      collapsed to agree by the caller, but this module treats "disagree + empty
      plan" as a concession defensively too.
  reselect(remaining_ucis)       -> uci  (must be in remaining_ucis)
      Pick a replacement move from the moves not yet banned.

Returns
-------
  {"chosen_uci": final uci, "changed": bool, "bad": [uci...],
   "rounds": [{move, san?, reason, agreed, plan, outcome}...],
   "assessment": the final assess() dict for the standing move}
"""
from __future__ import annotations


def run_appeal(chosen_uci, all_ucis, assess, confront, reselect, max_appeals=2):
    all_ucis = list(all_ucis)
    bad: list = []
    rounds: list = []
    changed = False
    cur = chosen_uci

    for _ in range(max(0, int(max_appeals))):
        remaining = [u for u in all_ucis if u not in bad]      # includes cur
        a = assess(cur, remaining) or {}
        reason = a.get("reason")
        if not reason:
            break                                              # current move is clean

        verdict = confront(cur, reason) or {}
        agree = bool(verdict.get("agree", True))
        plan = (verdict.get("plan") or "").strip()
        rnd = {"move": cur, "reason": reason, "agreed": agree,
               "plan": plan, "outcome": None}
        rounds.append(rnd)

        if (not agree) and plan:
            rnd["outcome"] = "defended"                        # a real plan -> play it
            break

        # conceded: agreed it's bad, or "disagreed" with no concrete plan
        bad.append(cur)
        nxt = [u for u in all_ucis if u not in bad]
        if not nxt:
            rnd["outcome"] = "conceded (no alternative left)"
            break
        rnd["outcome"] = "conceded"
        cur = reselect(nxt)
        changed = True

    remaining = [u for u in all_ucis if u not in bad] or list(all_ucis)
    assessment = assess(cur, remaining) or {}
    return {"chosen_uci": cur, "changed": changed, "bad": bad,
            "rounds": rounds, "assessment": assessment}
