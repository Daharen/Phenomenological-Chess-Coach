"""
Candidate-slate establishment control-flow (pure; board/LLM/engine injected).

Replaces the old "give up after N attempts and take a Stockfish slate" behaviour.
Two phases, driven entirely by injected callables so it unit-tests without a model
or engine (see tests/test_establish.py):

  Phase 1 -- FREE proposal. The proposer offers one move at a time from minimal
  context. Each legal move is GATED; a flagged move gets a first-pass appeal (NO
  budget -- we keep confronting). Agree/no-plan -> the move is banned and appended
  to the bad-list; defend-with-a-plan -> kept (override); clean -> kept. Illegal
  moves accumulate. When the proposer hallucinates `illegal_cap` illegal moves (or
  stalls for `no_prog_cap` repeats), we stop trusting free proposal and switch to:

  Phase 2 -- CONSTRAINED selection. We now show the proposer a RANDOM handful
  (<= `max_presented`) of the remaining legal moves and make it PICK one (degrades
  quality -- it now sees a move list -- but guarantees legality). The pick is still
  gated; a failed pick is banned and we present a fresh random handful, whittling
  the legal set until a clean slate is found or the moves run out. Not pre-gated:
  the model does the thinking, the gate judges the result.

Injected callables
------------------
  propose(ruled_out_sans, chosen_ucis) -> {"kind":"legal","uci","san","rationale","source"}
                                          | {"kind":"illegal","raw"}
  gate(uci)              -> {"flagged":bool,"reason":str,"kinds":[...]}
  appeal(uci, reason)    -> {"agree":bool,"plan":str}
  select(pairs)          -> {"uci","reasoning"} | None   (pairs: [{"uci","san"}...])

Returns {established, rejected, illegal, banned, constrained, appeals_made}.
Each established item: {uci, san, proposal:{uci,san,rationale,source}, via, gate}.
"""
from __future__ import annotations

import random as _random


def run_establishment(all_ucis, san_of, target, propose, gate, appeal, select,
                      illegal_cap=5, max_presented=5, no_prog_cap=8, rng=None):
    rng = rng or _random.Random()
    all_ucis = list(all_ucis)
    established: list = []
    rejected: list = []
    illegal: list = []
    banned: set = set()
    appeals_made = 0

    def est_ucis():
        return {e["uci"] for e in established}

    def consider(uci, san, rationale, source, via):
        nonlocal appeals_made
        proposal = {"uci": uci, "san": san, "rationale": rationale, "source": source}
        g = gate(uci) or {"flagged": False}
        if not g.get("flagged"):
            established.append({"uci": uci, "san": san, "proposal": proposal,
                                "via": via, "gate": {"flagged": False}})
            return
        appeals_made += 1
        v = appeal(uci, g.get("reason", "")) or {}
        if (not v.get("agree", True)) and v.get("plan"):
            established.append({"uci": uci, "san": san, "proposal": proposal, "via": via,
                                "gate": {"flagged": True, "overridden": True,
                                         "reason": g.get("reason", ""),
                                         "kinds": g.get("kinds", []),
                                         "override_plan": v.get("plan", "")}})
            return
        rejected.append({"uci": uci, "san": san, "proposal": proposal, "via": via,
                         "reason": g.get("reason", ""), "kinds": g.get("kinds", []),
                         "appealed": True, "verdict": v})
        banned.add(uci)

    hard_cap = 4 * len(all_ucis) + 20
    iters = 0
    constrained = False
    stagnant = 0

    # ---- Phase 1: free proposal ----
    while len(established) < target and iters < hard_cap:
        iters += 1
        ruled_out = list(illegal) + [r["san"] for r in rejected]
        p = propose(ruled_out, sorted(est_ucis())) or {"kind": "illegal", "raw": "?"}
        if p.get("kind") != "legal":
            raw = (p.get("raw") or "?").strip() or "?"
            if raw not in illegal:
                illegal.append(raw)
            if len(illegal) >= illegal_cap:
                constrained = True
                break
            stagnant += 1
            if stagnant >= no_prog_cap:
                constrained = True
                break
            continue
        uci = p["uci"]
        if uci in est_ucis() or uci in banned:
            stagnant += 1
            if stagnant >= no_prog_cap:
                constrained = True
                break
            continue
        stagnant = 0
        consider(uci, p["san"], p.get("rationale", ""), p.get("source", "llm"), "free")

    # ---- Phase 2: constrained selection ----
    if constrained and len(established) < target:
        miss = 0
        while len(established) < target and iters < hard_cap:
            iters += 1
            remaining = [u for u in all_ucis if u not in est_ucis() and u not in banned]
            if not remaining:
                break
            sample = (remaining if len(remaining) <= max_presented
                      else rng.sample(remaining, max_presented))
            res = select([{"uci": u, "san": san_of[u]} for u in sample])
            pick = (res or {}).get("uci")
            if pick not in set(sample):
                miss += 1
                if miss >= no_prog_cap:                 # model can't choose -> deterministic pick
                    u = rng.choice(sample)
                    consider(u, san_of[u],
                             "constrained random pick (proposer could not choose)",
                             "fallback", "constrained-random")
                    miss = 0
                continue
            miss = 0
            consider(pick, san_of[pick], (res or {}).get("reasoning", ""), "llm", "constrained")

    return {"established": established, "rejected": rejected, "illegal": illegal,
            "banned": sorted(banned), "constrained": constrained,
            "appeals_made": appeals_made}
