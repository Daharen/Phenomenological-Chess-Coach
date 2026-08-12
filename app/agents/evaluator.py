"""
Agent 4 -- the Phenomenological Evaluator / Coach.

It receives the full trace of a turn: the chosen move, the viable candidates,
the rejected candidates with the reason and the consequence line that killed
them, the sandbox trajectories, the deterministically-detected glossary concepts
(with definitions), the strategic manifest, and any Stockfish blind-spot audit.

It explains -- in human language keyed to the glossary -- WHY the chosen move was
chosen and WHY the others were rejected.  With no LLM it produces a structured
template narration from the same verified data, so coaching is always available.
"""

from __future__ import annotations

import chess

from .base import LLMClient
from ..engine.glossary import Glossary

SYS = ("You are a master-level chess coach who explains moves phenomenologically: "
       "what a strong human FEELS and SEES that makes a move right or wrong. You "
       "are given verified engine facts and detected motifs; use ONLY the chess "
       "terms provided in the glossary context, and tie your explanation to the "
       "concrete lines given. Be vivid but precise. 120-220 words.")


class Evaluator:
    def __init__(self, client: LLMClient, glossary: Glossary, cfg):
        self.client = client
        self.glossary = glossary
        self.cfg = cfg

    def coach(self, rec: dict) -> dict:
        defs = self.glossary.definitions_for(rec.get("concepts", []))
        if self.client.available:
            text = self._coach_llm(rec, defs)
            if text:
                return {"text": text, "source": "llm", "glossary": defs}
        return {"text": self._coach_template(rec, defs), "source": "template",
                "glossary": defs}

    # -- LLM coach -------------------------------------------------------------
    def _coach_llm(self, rec, defs) -> str | None:
        gloss = "\n".join(f"- {d['term']}: {d['definition']}" for d in defs) or "- (none)"
        rejected = self._fmt_rejected(rec)
        chosen = rec["chosen"]
        cl = chosen.get("classification", {})
        audit = rec.get("audit")
        audit_s = f"\nBlind-spot audit: {audit['note']}" if audit else ""
        sandbox = self._fmt_sandbox(rec)
        user = (
            f"Manifest theme: {rec['manifest'].get('theme')} "
            f"(mode: {rec['manifest'].get('mode')}).\n"
            f"Chosen move: {chosen.get('san')} — engine label {cl.get('label')}, "
            f"eval {cl.get('played_cp')}cp; player's stated reason: "
            f"{chosen.get('rationale') or 'n/a'}.\n"
            f"Sandbox lines (decreasing horizon):\n{sandbox}\n"
            f"Rejected candidates and why:\n{rejected}{audit_s}\n\n"
            f"Glossary terms in play:\n{gloss}\n\n"
            "Explain, as a coach, why the chosen move fits the plan and what the "
            "rejected moves would have run into. Reference the concrete lines and "
            "the glossary terms."
        )
        return self.client.chat(SYS, user, temperature=0.6, max_tokens=600)

    # -- deterministic template ------------------------------------------------
    def _coach_template(self, rec, defs) -> str:
        chosen = rec["chosen"]
        cl = chosen.get("classification", {})
        m = rec["manifest"]
        lines = []
        lines.append(
            f"Chosen: {chosen.get('san')} ({cl.get('label', 'ok')}, "
            f"{cl.get('played_cp', 0)}cp). This serves the plan — {m.get('theme')}."
        )
        # sandbox trajectory of the chosen line
        best_line = None
        for ln in rec.get("sandbox", {}).get("lines", []):
            if ln["seed_uci"] == chosen.get("uci"):
                best_line = ln
                break
        if best_line and best_line["steps"]:
            traj = " ".join(s["san"] for s in best_line["steps"])
            lines.append(f"Main line explored ({len(best_line['steps'])} plies, "
                         f"depth {best_line['steps'][0]['depth_used']}->"
                         f"{best_line['steps'][-1]['depth_used']}): {traj}.")
        # rejected
        for r in rec.get("rejected", [])[:3]:
            rc = r.get("classification", {})
            lines.append(
                f"Rejected {r.get('san')}: {r.get('reason')} "
                f"(engine {rc.get('label')}, would reach {rc.get('played_cp')}cp)."
            )
        # concepts
        if defs:
            names = ", ".join(d["term"] for d in defs[:8])
            lines.append(f"Motifs present: {names}.")
            key = defs[0]
            lines.append(f"Key idea — {key['term']}: {key['definition']}")
        if rec.get("audit"):
            lines.append("Blind spot: " + rec["audit"]["note"])
        return "\n".join(lines)

    # -- helpers ---------------------------------------------------------------
    def _fmt_rejected(self, rec) -> str:
        rows = []
        for r in rec.get("rejected", [])[:4]:
            rc = r.get("classification", {})
            cons = r.get("consequence")
            traj = ""
            if cons and cons.get("trajectory"):
                traj = " → " + " ".join(s["san"] for s in cons["trajectory"][:6])
            rows.append(f"  {r.get('san')}: {r.get('reason')} "
                        f"[{rc.get('label')}]{traj}")
        return "\n".join(rows) or "  (none rejected this turn)"

    def _fmt_sandbox(self, rec) -> str:
        rows = []
        for ln in rec.get("sandbox", {}).get("lines", [])[:3]:
            traj = " ".join(f"{s['san']}({s['eval_cp']})" for s in ln["steps"])
            flag = f" COLLAPSE@{ln['collapse_ply']}" if ln["collapsed"] else ""
            rows.append(f"  {ln['seed_san']}: {traj}{flag}")
        return "\n".join(rows) or "  (no sandbox lines)"
