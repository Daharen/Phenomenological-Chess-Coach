# Move Loop — Spec

How the engine turns a position into a move. Built modularly and extended **one
module at a time**; this file marks what is implemented now vs. planned.

## Principles

- **The proposer sees almost nothing.** The one-off mover is handed only the
  board (FEN) plus at most a one-line general note from the orchestrator. No
  manifest dump, no move history, no detected-concept list, and — critically —
  **no precomputed list of legal or illegal moves**. Small local models measure
  *worse* when a big move list is stuffed into the prompt, so the proposer starts
  from an empty state.
- **Lists grow only by trial, one entry at a time.** A move enters a ban-list
  only *after the proposer actually tried it* and it was determined illegal (or,
  later, bad). We never front-load "here is everything you can't do."
- **Determinism finds; the model relays.** The evaluator (deterministic, not
  Stockfish, once modules exist) runs *after* a move is selected and feeds facts
  back. The proposer is a reflex; the evaluator is the teacher.
- **Rich context is fine downstream.** Only the *proposer* is starved. The
  evaluator/coach still gets the manifest, concepts, and lines — that's where
  explanation belongs.

## Accumulation lists (all start EMPTY; grow by trial)

| list       | filled by                                  | status        |
|------------|--------------------------------------------|---------------|
| `illegal[]`| a proposed move that fails legality        | **now**       |
| `chosen[]` | accepted distinct legal candidates (K=3)   | **now**       |
| `bad[]`    | a *selected* move the evaluator flags bad  | planned       |

`illegal[]` retry is effectively **unbounded**: the real move set is finite, so
banning each tried move converges. A high attempt backstop (`max_establish_attempts`,
default 64) exists only to stop a model that emits endless *unparseable garbage*
strings (which are not real moves and so never "run out"). Real illegal moves are
not capped by intent.

## Turn flow (implemented now)

1. `note = orchestrator.minimal_note(board)` — one short line (the current plan
   theme), or nothing.
2. **Establish** `K=3` distinct legal candidates via **single-move** proposals
   with minimal context. Each call: FEN + note + (only if non-empty) the small
   trial-accumulated `illegal[]` and `chosen[]` lists. Illegal tries append to
   `illegal[]` and loop again from a fresh minimal state. If the position has
   fewer than 3 legal moves, the target drops to that many. No LLM → a Stockfish
   candidate slate is used instead (clearly labeled).
3. **Classify** each candidate with the existing (naive/Stockfish) engine, then
   **select** per mode — guided (Stockfish vetoes + picks), assist (Stockfish
   picks best of the slate), autonomous (the LLM picks). Unchanged this iteration.
4. **Sandbox** the viable set for the move-trees; commit the chosen move.
5. **Evaluator/coach** narrates with full context (this is the rich layer).

So today, move *quality* is still judged exactly as the naive engine judged it —
that is intentional. Intelligence is added later by the evaluator modules, not by
enlarging the proposer's prompt.

## Implemented deterministic modules

- **Module 1 — material safety (`app/engine/deteval.py`).** Runs *after* a move
  is selected. For every candidate the proposer established, it computes a 1-ply
  material score: `net_cp = (material after our move) − (opponent's best legal
  capture, resolved by SEE)`. If the *chosen* move hangs ≥ `hang_threshold_cp`
  (default 100) more than a safe sibling would, it **vetoes** and substitutes the
  safe sibling in `guided`/`assist` (and, if `veto_in_autonomous`, in
  `autonomous`), otherwise it **warns** and leaves the LLM's pick alone. It is
  pin/check-aware because it drives off *legal* captures. Scope is honestly just
  MATERIAL, one ply — it catches "you hung your knight" and "that capture loses
  to the recapture," not forks-next-move or positional compensation.
  - Fixed a latent bug in `concepts.see` this module sits on: the old iterative
    fold ran one phantom recapture too many and *undervalued every undefended
    capture* — it scored winning a free pawn with a knight as **−220**. Replaced
    with an exact recursive SEE (top-level capture forced/signed, recaptures
    optional). This also sharpens the existing hanging-piece / fork / sacrifice
    detectors that share `see`.

- **Module 2 — fork / double-attack threats (`app/engine/threats.py`).** Looks
  one ply deeper than module 1. For each candidate, in the position *after* our
  move, it scans every opponent reply for a single move that attacks two of our
  pieces at once (knight hitting king + rook, queen double attack, pawn fork)
  where we can save only one, and estimates the material lost next move (via SEE).
  It is deliberately conservative: the forking piece must land safely (if we can
  just capture it for SEE ≥ 0 the fork is ignored), and each counted target must
  be genuinely winnable — undefended, the king (a check we must answer), or an
  up-trade. Check-forks lose the best *other* target; quiet double attacks lose
  the second-best. **Default action is WARN, not veto** (`threats.veto` opts in),
  because fork judgement has defensive nuance a 2-ply scan can miss — a false
  alarm in a teaching tool is worse than silence. Does *not* yet model discovered
  attacks by a non-moving piece, our counter-threats, or pins that freeze the
  forker. Shown in the UI as its own safety panel; per-candidate `threat_cp`.

## Planned (each a separate module, shipped one at a time)

- **Further deterministic evaluator modules** (added individually):
  pin-aware capture legality, skewers, discovered-attack threats,
  net-material swing, king-line attacker scan, back-rank, … Each emits a typed
  fact `{type, squares, severity, one-line phenomenological sentence, fact_id}`.
- **Appeal step:** after a move is selected, the evaluator flags it "bad because
  R; do you agree?" The *same* proposal gets exactly one reply. Agree → the move
  goes on `bad[]` and a **fresh** minimal-state proposer picks again. Disagree +
  states a plan → the move is played (the phenomenological engine may see
  compensation the shallow flag doesn't). Bounded appeals per turn.
- **Tier split (rules vs. guidelines):** illegal is always a hard veto; a small
  catastrophic set (mate-in-1, losing a piece for nothing) is also hard and
  **not** appealable; everything softer is an appealable guideline.
- **Forced-move override:** in a forcing line the orchestrator may assert a move;
  the proposer only blunder-checks it.
- **Grounding / verbosity control:** constrain the evaluator to the coaching
  vocabulary and to facts that carry a `fact_id`; deterministically strip any
  claim not backed by a fact — the fact layer both *feeds* and *audits* the model.

## Module contract (for the deterministic layers)

Each detector is independent and testable against known FENs:

```
detect(board, side_to_move) -> [ Fact(
    type,            # e.g. "hanging", "pin", "fork_threat"
    squares,         # evidence squares
    severity,        # value-at-risk, for salience ordering
    sentence,        # phenomenological, student-readable
    fact_id,         # for grounding/verification
) ]
```

Facts are salience-ordered deterministically (value-at-risk first); the model
chooses phrasing and emphasis, not truth.
