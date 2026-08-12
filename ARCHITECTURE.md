# Architecture

The system is a deterministic control loop wrapped around four LLM roles. The
determinism (legality, classification, horizon, sandbox, concept detection) is
what keeps the language model honest and the token/latency budget bounded; the
LLM supplies human strategic narrative on top.

## Turn pipeline

```
        Orchestrator.context_for_player(board)      # FEN + manifest + motifs
                        |
                        v
   +-----------------------------------------------+
   |  One-off Player proposes ONE move             |
   |    illegal  -> blacklist (per turn), retry    |
   |    legal    -> classify (Stockfish)           |
   |        not flagged      -> viable candidate    |
   |        flagged          -> horizon gate:       |
   |          beyond horizon -> allowed (viable)    |
   |          within horizon -> reject + show the   |
   |                            consequence line,   |
   |                            try again (uses a    |
   |                            candidate slot)      |
   +-----------------------------------------------+
                        |  up to K=3 viable candidates
                        v
   Sandbox: play each seed forward, depth 5->4->3->2->1,
            beam width [3,2,1,1,1], flag collapses, rank
                        |
                        v
   Best-of-K -> commit move
                        |
     +------------------+-------------------+
     v                                      v
 Orchestrator.audit_blind_spot        Evaluator.coach(trace)
 (chosen vs Stockfish best)           (why chosen / why rejected,
     |                                 keyed to the glossary)
     v
 Memory.note_eval  -> Blunder Protocol (archive manifest, crisis mode)
```

## Modules

```
app/
  config.py            layered config (defaults <- config.json <- env), C:/F: paths
  server.py            Flask API + serves web/index.html (one process/console)
  launcher.py          single entry: ensure dirs, start server, open browser
  engine/
    stockfish_pool.py  thread-safe wrapper on the existing Stockfish, eval cache
    classify.py        best/good/inaccuracy/mistake/blunder/miss/loss (mover pov)
    horizon.py         comprehension-horizon gate + consequence line
    sandbox.py         decreasing beam-width / decreasing-depth calculation
    concepts.py        deterministic motif detector incl. real SEE (hanging/sac)
    glossary.py        loads config/glossary.json, maps tags -> definitions
    legality.py        tolerant SAN/UCI parsing, legal-target helpers
  agents/
    base.py            LLMClient: LocalClient (llama-server) | GeminiClient | Null
    orchestrator.py    manifest, opening plan, continuity, blind-spot audit
    player.py          one-off move proposer (+ Stockfish-guided fallback)
    evaluator.py       phenomenological coach (+ deterministic template)
  game/
    memory.py          dual-tier memory: floating context + strategic manifest
    coach.py           the turn pipeline + session state (the controller)
config/
  config.json          canonical config (paths, provider, thresholds, schedules)
  glossary.json        200-term chess lexicon (category + definition)
web/index.html         single-file board UI (vanilla JS, no external deps)
ops/                   doctor.py (health), analyze_fen.py (headless turn)
tools/Setup-Venv.ps1   builds the venv on F: and installs requirements
tests/test_core.py     deterministic-core tests against a real Stockfish
```

## Key design decisions

- **Scores are always from the side-to-move's perspective** for classification,
  with mate folded into ±100000 centipawns by the Stockfish wrapper.
- **The horizon is a *shallow search depth*, not a hard ply cutoff.** A move is
  "understandably bad" if a search at ~horizon depth already dislikes it. The
  reveal depth (where it first reads as bad) is surfaced for coaching.
- **The sandbox is bounded by construction.** Depth at ply *p* is
  `max(1, max_horizon - p + 1)`; the mover branches by the beam schedule while
  the opponent takes its best reply, so leaves ≤ the product of the beam widths.
- **Concept detection is deterministic, not model-guessed.** Pins, forks
  (SEE-checked), hanging/loose pieces (SEE), pawn structure, files/rooks,
  outposts, king safety, back-rank, batteries, center — computed from
  python-chess so the coach cites verified facts.
- **Graceful degradation.** Every LLM call is fail-soft; if the model is down the
  deterministic layer plays and a templated coach explains. The app runs with
  zero model configured.

## Extension points (roadmap)

- Swap the flat glossary for a vector store (FAISS/Chroma) with positional
  triggers; `glossary.py` is the seam.
- SSE streaming of the reasoning trace to the UI (the API already returns the
  full trace object).
- Persist/replay full games as PGN in `game/` and add an opening book.
- Multi-branch sandbox (beam width > 1 on mover plies is already supported by
  `sandbox.py`; wire a richer tree view in the UI).
- Fast-follow "blind spot" loop: feed `audit_blind_spot` back into the player as
  a second-chance prompt before committing.
```
