"""
Dual-tier memory for continuity.

  * FloatingContext  -- short-term, wiped and rebuilt every real move (candidate
    moves, immediate threats, this-turn tactical notes).
  * StrategicManifest -- long-term, mutable narrative (theme, targets, king
    safety, opponent intent, mode).  Updated periodically; archived to history.

The Blunder Protocol: when the real evaluation swings severely against us, the
manifest is archived and the mode flips to "crisis" (single-state survival)
instead of continuing a plan that reality just refuted.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class FloatingContext:
    candidates: list[dict] = field(default_factory=list)
    threats: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def wipe(self):
        self.candidates.clear()
        self.threats.clear()
        self.notes.clear()


@dataclass
class StrategicManifest:
    color: str = "white"
    opening: str = "Undetermined"
    theme: str = "Development and central control"
    long_term_goals: list[str] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    king_safety: str = "Not yet castled"
    opponent_intent: str = "Unknown"
    mode: str = "strategic"           # "strategic" | "crisis"
    move_number: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Memory:
    def __init__(self, data_dir: Path | None = None, game_id: str | None = None):
        self.floating = FloatingContext()
        self.manifest = StrategicManifest()
        self.history: list[dict] = []          # archived manifests
        self.move_log: list[dict] = []         # per-turn records
        self.data_dir = Path(data_dir) if data_dir else None
        self.game_id = game_id or f"game-{int(time.time())}"
        self.crisis_swing_cp = 250             # eval swing that triggers crisis mode
        self._last_eval_cp: int | None = None

    # ---- manifest lifecycle --------------------------------------------------
    def start(self, color: str, opening: str, theme: str, goals: list[str],
              opponent_intent: str = "Unknown"):
        self.manifest = StrategicManifest(
            color=color, opening=opening, theme=theme,
            long_term_goals=list(goals), opponent_intent=opponent_intent,
            king_safety="Not yet castled", mode="strategic", move_number=0,
        )
        self.history.clear()

    def archive_manifest(self, reason: str):
        snap = self.manifest.to_dict()
        snap["_archived_reason"] = reason
        snap["_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.history.append(snap)

    def note_eval(self, eval_cp_mover_pov: int) -> bool:
        """Feed the post-move eval (from our perspective). Returns True if the
        Blunder Protocol fired (crisis mode entered)."""
        fired = False
        if self._last_eval_cp is not None:
            swing = self._last_eval_cp - eval_cp_mover_pov
            if swing >= self.crisis_swing_cp and self.manifest.mode != "crisis":
                self.archive_manifest(f"severe swing {swing}cp -> crisis")
                self.manifest.mode = "crisis"
                self.manifest.theme = "Crisis management: king safety and counterplay"
                fired = True
        self._last_eval_cp = eval_cp_mover_pov
        return fired

    def recover_if_stable(self, eval_cp_mover_pov: int):
        """Leave crisis mode if the position has stabilised."""
        if self.manifest.mode == "crisis" and eval_cp_mover_pov > -120:
            self.archive_manifest("stabilised -> resume strategic play")
            self.manifest.mode = "strategic"

    # ---- turn logging --------------------------------------------------------
    def log_turn(self, record: dict):
        self.move_log.append(record)

    def persist(self):
        if not self.data_dir:
            return
        try:
            runs = self.data_dir / "runs"
            runs.mkdir(parents=True, exist_ok=True)
            out = {
                "game_id": self.game_id,
                "manifest": self.manifest.to_dict(),
                "history": self.history,
                "move_log": self.move_log,
            }
            (runs / f"{self.game_id}.json").write_text(
                json.dumps(out, indent=2), encoding="utf-8")
        except OSError:
            pass

    def snapshot(self) -> dict:
        return {
            "manifest": self.manifest.to_dict(),
            "floating": asdict(self.floating),
            "history_len": len(self.history),
        }
