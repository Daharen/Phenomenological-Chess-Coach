# Phenomenological Chess Coach

A four-agent chess system that plays *and explains itself*. Stockfish supplies
the raw skill; a language model supplies the human-style reasoning; a
deterministic layer keeps everything legal, bounded, and honest. The result is a
move plus a phenomenological account of **why** it was chosen and **why** the
alternatives were rejected — keyed to a 200-term chess glossary.

This is the MVP of a larger idea: a strategic orchestrator that plans over a long
horizon and feeds a single-pass player, with a calculation sandbox and a coaching
evaluator layered on top.

## The one thing you click

`Play-ChessCoach.bat` (in this folder) is the only launcher. It starts one local
server (one console window) and opens the board in your browser. Everything —
new game, level, engine brain, analysis — is in that one page.

First-time setup builds the Python environment on the **F: data drive** (kept off
C: on purpose, to avoid bloat):

```powershell
pwsh -NoProfile -File .\tools\Setup-Venv.ps1
```

Then double-click `Play-ChessCoach.bat`.

## Where things live

The program (this repo) lives on C: and stays lean — code, the launcher, config,
the web page, docs. Everything heavy or generated lives on F::

```
C:\...\Phenomenological_Chess_Coach\     <- this repo (code, launcher, docs)
F:\...\Phenomonological_Chess_Coach_Data\
   venv\        the Python environment (big; off C: by design)
   runs\        per-game JSON transcripts (manifest + move log)
   games\ logs\ cache\ glossary\
```

Stockfish is **not** bundled — the app connects to your existing binary (see
`config/config.json` → `stockfish_path`).

## The engine brain (LLM) is switchable

All four agents talk to one pluggable client. Pick the backend in the header
dropdown, or set it in `config/config.json` (`llm.provider`), or via
`CHESS_COACH_PROVIDER`:

- **`local`** — your llama-server 9B (OpenAI-compatible, `http://127.0.0.1:8080`).
  No paid tokens; the default, so you can validate the whole system for free.
- **`gemini`** — Google Gemini. Set your model id under `llm.gemini.model`; the
  API key is read from the `GEMINI_CHESS_API` environment variable.
- **`null` / Stockfish-only** — no model; the deterministic fallbacks play and a
  templated coach explains. The app never hard-fails if a model is unreachable.

## The four agents

1. **Orchestrator** — opens with a plan and keeps a *Strategic Manifest* across
   the whole game (continuity). Runs the Blunder Protocol: on a severe eval swing
   it archives the plan and flips to crisis management.
2. **One-off Player** — proposes a single move from the board + the manifest.
   Illegal proposals are kicked back deterministically and blacklisted for the
   turn (it's told only that a move is illegal, never what's better).
3. **Sandbox** (Stockfish-fused) — plays each surviving candidate forward with a
   **decreasing search horizon** (depth 5→4→3→2→1) and a shrinking beam, flags
   tactical collapses, and ranks the candidates.
4. **Evaluator** — the phenomenological coach. Explains the chosen move and the
   rejected lines using the detected motifs and the glossary.

## The comprehension-horizon principle

A move Stockfish dislikes is only *counted against* the player if the refutation
is visible within the player's horizon (a shallow, human-like search). If the
move only turns bad under deep engine search, the refutation lives beyond the
horizon and the move is allowed — "a move that's only bad because of a
machine-level tactic too deep to see isn't actually bad *at this level*." Levels
(club → GM) set the horizon (5 → 15 plies).

## Health check

```powershell
F:\My_Programs\Phenomonological_Chess_Coach_Data\venv\Scripts\python.exe -m ops.doctor
```

Verifies Stockfish, the data dirs, the glossary, and the LLM provider. A full
headless turn: `python -m ops.analyze_fen`.

See `ARCHITECTURE.md` for the design and the module map (useful for extending the
system or delegating work to another agent).
