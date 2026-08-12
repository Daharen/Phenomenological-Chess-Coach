"""
Configuration loading and path resolution for the Phenomenological Chess Coach.

The canonical config file lives next to the program on C: (config/config.json).
It points at the F: data directory (kept off C: to avoid bloat), the existing
Stockfish binary, and the LLM settings. Everything can be overridden by
environment variables so the exact same code runs under cloud test harnesses.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ----- default configuration -------------------------------------------------

DEFAULTS: dict[str, Any] = {
    # F: data directory (venv, logs, games, glossary cache, per-turn runs)
    "data_dir": "F:\\My_Programs\\Phenomonological_Chess_Coach_Data",

    # Existing Stockfish binary (connect in, do not re-download).
    "stockfish_path": "C:\\Users\\just_\\Opening-Trainer\\tools\\stockfish\\stockfish-windows-x86-64-avx2.exe",
    "stockfish_fallbacks": [
        "F:\\OpeningTrainerContent\\stockfish\\stockfish-windows-x86-64-avx2.exe",
        "F:\\Opening Trainer Large Data File\\Work Surface\\opening_trainer_content_seed_rapid600_v1\\stockfish\\stockfish-windows-x86-64-avx2.exe",
    ],
    "stockfish_threads": 2,
    "stockfish_hash_mb": 256,

    "llm": {
        # provider: "local" (llama-server, no paid tokens) | "gemini" | "null"
        # Default is "local" so the whole app validates end-to-end for free first;
        # flip to "gemini" (or set CHESS_COACH_PROVIDER=gemini) once validated.
        "provider": "local",
        "enabled": True,
        "timeout_seconds": 60,
        "max_retries": 2,

        # Local llama-server (OpenAI-compatible).
        "local": {
            "endpoint": "http://127.0.0.1:8080/v1/chat/completions",
            "model": "local-9b",            # llama-server usually ignores / echoes this
            "api_key": "sk-no-key-required",
            "timeout_seconds": 240,         # survive a cold 9B spin-up (~3 min)
            "keepalive_seconds": 150,       # poke the model so VRAM stays hot (0=off)
        },

        # Google Gemini (Generative Language API).
        "gemini": {
            "model": "gemini-2.5-pro",      # set to your exact model id in config.json
            "model_fallbacks": ["gemini-2.5-flash", "gemini-1.5-pro"],
            "api_key_env": "GEMINI_CHESS_API",
            "endpoint": "https://generativelanguage.googleapis.com/v1beta",
        },
    },

    # Playing "level" governs the comprehension horizon (in plies) used to decide
    # whether a Stockfish-flagged move is *understandably* bad or merely bad at a
    # depth beyond human sight.
    # horizon = comprehension depth in plies (human sight).
    # deep_movetime = seconds for the movetime-bounded "machine sight" probe and
    #                 the strongest evaluations (audit, safety net, analysis).
    "levels": {
        "club":   {"horizon": 5,  "deep_movetime": 1.0},
        "expert": {"horizon": 8,  "deep_movetime": 1.5},
        "master": {"horizon": 11, "deep_movetime": 2.5},
        "gm":     {"horizon": 15, "deep_movetime": 4.0},
    },
    "default_level": "expert",

    # Centipawn-loss thresholds (relative to the side to move) for classifying a
    # move.  Loss magnitudes are compared against the best move at the same depth.
    "classification": {
        "inaccuracy_cp": 50,
        "mistake_cp": 120,
        "blunder_cp": 250,
        "win_threshold_cp": 350,   # eval above which a position is "winning"
        "miss_drop_cp": 200,       # drop that turns a winning position into a "miss"
        "class_depth": 12,         # depth used for per-move classification
    },

    # Decreasing beam-width sandbox.
    #   horizon        : max plies explored (never deeper than this)
    #   beam_schedule  : how many mover-candidates to branch at each mover ply
    #                    (index 0 = first mover ply). Opponent replies use best.
    #   depth at ply p = max(1, horizon - p + 1)   (the "5->4->3->2->1" rule)
    "sandbox": {
        "max_horizon": 5,
        "beam_schedule": [3, 2, 1, 1, 1],
        "collapse_cp": -300,        # eval (mover pov) at/below this = tactical collapse
        "candidate_k": 3,             # the player must establish this many legal moves
        "max_establish_attempts": 64, # backstop only (endless-garbage guard); real illegal
                                      # retries converge on the finite move set well before this
    },

    # How the move is chosen from the player's OWN established slate:
    #   guided     -> Stockfish vetoes within-horizon blunders + picks best (sandbox)
    #   assist     -> Stockfish just ranks and picks the best of the slate (no veto)
    #   autonomous -> the LLM itself picks; Stockfish only checks legality
    "selection_mode": "guided",

    "server": {
        "host": "127.0.0.1",
        "port": 7801,
        "open_browser": True,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@dataclass
class Config:
    raw: dict[str, Any]
    source_path: Path | None = None

    # ---- convenient typed accessors ----
    @property
    def data_dir(self) -> Path:
        return Path(os.environ.get("CHESS_COACH_DATA", self.raw["data_dir"]))

    @property
    def stockfish_path(self) -> str:
        return os.environ.get("CHESS_COACH_STOCKFISH", self.raw["stockfish_path"])

    @property
    def stockfish_candidates(self) -> list[str]:
        return [self.stockfish_path, *self.raw.get("stockfish_fallbacks", [])]

    @property
    def llm(self) -> dict:
        return self.raw["llm"]

    @property
    def level_name(self) -> str:
        return os.environ.get("CHESS_COACH_LEVEL", self.raw["default_level"])

    def level(self, name: str | None = None) -> dict:
        name = name or self.level_name
        return self.raw["levels"].get(name, self.raw["levels"][self.raw["default_level"]])

    @property
    def classification(self) -> dict:
        return self.raw["classification"]

    @property
    def sandbox(self) -> dict:
        return self.raw["sandbox"]

    @property
    def server(self) -> dict:
        return self.raw["server"]

    # ---- resolved data sub-directories (all on F:) ----
    def sub(self, *parts: str) -> Path:
        p = self.data_dir.joinpath(*parts)
        return p

    def ensure_dirs(self) -> None:
        for name in ("logs", "games", "runs", "glossary", "cache"):
            try:
                self.sub(name).mkdir(parents=True, exist_ok=True)
            except OSError:
                pass

    @property
    def provider(self) -> str:
        return os.environ.get("CHESS_COACH_PROVIDER", self.llm.get("provider", "local"))

    def gemini_key(self) -> str | None:
        env = self.llm.get("gemini", {}).get("api_key_env", "GEMINI_CHESS_API")
        return os.environ.get(env)


def program_root() -> Path:
    """Directory that contains app/, config/, web/ (the C: program dir)."""
    return Path(__file__).resolve().parent.parent


def load_config(path: str | os.PathLike | None = None) -> Config:
    if path is None:
        path = os.environ.get("CHESS_COACH_CONFIG")
    if path is None:
        path = program_root() / "config" / "config.json"
    path = Path(path)
    raw = dict(DEFAULTS)
    if path.exists():
        try:
            override = json.loads(path.read_text(encoding="utf-8"))
            raw = _deep_merge(DEFAULTS, override)
        except (OSError, json.JSONDecodeError) as exc:  # pragma: no cover
            print(f"[config] warning: could not read {path}: {exc}; using defaults")
    return Config(raw=raw, source_path=path if path.exists() else None)
