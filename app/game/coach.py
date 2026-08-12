"""
The session controller that runs a full phenomenological turn.

Turn pipeline (per the design):
  1. Orchestrator builds the continuity payload.
  2. One-off player proposes a move.
       - illegal  -> kicked back, appended to the ruled-out list, retried
                     (deterministically, does not consume a candidate slot).
       - legal    -> classified by Stockfish.
            * not flagged            -> a viable candidate.
            * flagged (loss/blunder/ -> horizon gate:
              miss/inaccuracy)           - beyond horizon -> allowed (goes forward)
                                         - within horizon -> rejected; the
                                           consequence line is shown and the
                                           player tries again (consumes a slot).
  3. Up to K viable candidates are carried into the decreasing beam-width sandbox.
  4. Best-of-K by sandbox trajectory becomes the move.
  5. Evaluator coaches from the full trace; orchestrator audits for blind spots;
     memory updates (with the Blunder Protocol).
"""

from __future__ import annotations

import time

import chess

from ..config import Config
from ..engine.stockfish_pool import StockfishPool
from ..engine.classify import classify_move
from ..engine.horizon import assess_horizon, consequence_line
from ..engine.sandbox import run_sandbox
from ..engine.concepts import detect_concepts
from ..engine.glossary import load_glossary
from ..agents.base import make_client
from ..agents.orchestrator import Orchestrator, _san_history
from ..agents.player import OneOffPlayer
from ..agents.evaluator import Evaluator
from .memory import Memory

# Material values for the deterministic (non-Stockfish) leaf evaluation used by
# the LLM-calculated sandbox. King excluded (never captured).
PIECE_VAL = {chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330,
             chess.ROOK: 500, chess.QUEEN: 900}


def material_cp(board: chess.Board, pov: chess.Color) -> int:
    """Net material (centipawns) from `pov`'s perspective. Deterministic."""
    s = 0
    for pt, v in PIECE_VAL.items():
        s += v * len(board.pieces(pt, pov)) - v * len(board.pieces(pt, not pov))
    return s


class ChessCoach:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        cfg.ensure_dirs()
        self.pool = StockfishPool(cfg.stockfish_candidates,
                                  threads=cfg.raw.get("stockfish_threads", 2),
                                  hash_mb=cfg.raw.get("stockfish_hash_mb", 256))
        self.client = make_client(cfg)
        # glossary lives next to the config file (config/glossary.json)
        from ..config import program_root
        self.glossary = load_glossary(str(program_root() / "config" / "glossary.json"))
        self.memory = Memory(data_dir=cfg.data_dir)
        self.orch = Orchestrator(self.client, self.pool, self.memory, cfg)
        self.player = OneOffPlayer(self.client, self.pool, cfg)
        self.evaluator = Evaluator(self.client, self.glossary, cfg)

        self.board = chess.Board()
        self.engine_color = chess.BLACK       # human is white by default
        self.level = cfg.level_name
        self._start_keepalive()

    def _start_keepalive(self):
        """If the brain is the local 9B, load it now and keep it hot in VRAM."""
        start = getattr(self.client, "start_keepalive", None)
        if callable(start):
            start()

    # -- lifecycle -------------------------------------------------------------
    def new_game(self, human_color: str = "white", level: str | None = None):
        self.board = chess.Board()
        self.level = level or self.cfg.level_name
        self.engine_color = chess.BLACK if human_color.lower().startswith("w") else chess.WHITE
        engine_color_name = "black" if self.engine_color == chess.BLACK else "white"
        self.memory = Memory(data_dir=self.cfg.data_dir)
        self.orch.memory = self.memory
        manifest = self.orch.start_game(engine_color_name)
        return self.state(extra={"manifest": manifest})

    # -- human move ------------------------------------------------------------
    def human_move(self, uci: str) -> dict:
        move = chess.Move.from_uci(uci)
        if move not in self.board.legal_moves:
            return {"ok": False, "error": "illegal move"}
        # lightweight classification of the human's move for feedback
        cls = classify_move(self.pool, self.board, move, self.cfg.classification,
                            depth=self.cfg.classification["class_depth"])
        board_before = self.board.copy()
        self.board.push(move)
        concepts = detect_concepts(self.board, move, board_before)
        return {"ok": True, "classification": cls.to_dict(),
                "concepts": concepts, "state": self.state()}

    # -- LLM-calculated sandbox (optional variant) -----------------------------
    def _llm_move_at(self, board: chess.Board, note: str):
        """One legal move from the LLM for the side to move. Each call is a FRESH,
        context-purged instantiation with its OWN retry allowance -- local inference
        is free, so there is NO accumulative budget across the turn. Bounded only by
        a per-node cap and the no-progress guard (both converge on the finite legal
        set). Returns (Proposal|None, [illegal raw...], attempts)."""
        cap = self.cfg.sandbox.get("max_establish_attempts", 24)
        no_prog_cap = self.cfg.sandbox.get("no_progress_cap", 6)
        tried: list[str] = []
        seen: set[str] = set()
        attempts = 0
        stagnant = 0
        while attempts < cap:
            attempts += 1
            prop = self.player.propose_one(board, note, tried, None)
            if prop.move is not None and prop.move in board.legal_moves:
                return prop, tried, attempts
            raw = (prop.raw or "").strip() or "?"
            if raw not in seen:
                seen.add(raw)
                tried.append(raw)
                stagnant = 0
            else:
                stagnant += 1
                if stagnant >= no_prog_cap:
                    break
        return None, tried, attempts

    def _llm_sandbox(self, board: chess.Board, root_ucis: list[str], note: str) -> dict:
        """For each root candidate, the LLM plays a line `our_moves` of OUR moves
        deep (interleaving the opponent's best-estimate replies), every move legal.
        Every ply is a fresh instantiation with its own retry allowance (no shared
        budget). The leaf is scored by deterministic net material; roots ranked by it."""
        our_moves = self.cfg.sandbox.get("our_moves", 5)
        collapse_cp = self.cfg.sandbox.get("collapse_cp", -300)
        root_mover = board.turn
        lines = []
        total_calls = 0
        for root in root_ucis:
            b = board.copy()
            mv0 = chess.Move.from_uci(root)
            steps = []
            illegal_here: list[str] = []
            line_calls = 0
            san0 = b.san(mv0)
            b.push(mv0)
            steps.append({"ply": 1, "san": san0, "uci": root, "mover": True,
                          "depth_used": 0, "eval_cp": material_cp(b, root_mover),
                          "fen": b.fen()})
            collapsed = False
            collapse_ply = None
            our_count = 1
            ply = 1
            while our_count < our_moves and not b.is_game_over():
                ply += 1
                is_our = (b.turn == root_mover)
                prop, tried, used = self._llm_move_at(b, note if is_our else "")
                line_calls += used
                total_calls += used
                illegal_here += tried
                if prop is None:
                    break                       # this node couldn't find a legal move; end line
                san = b.san(prop.move)
                b.push(prop.move)
                cp = material_cp(b, root_mover)
                steps.append({"ply": ply, "san": san, "uci": prop.move.uci(),
                              "mover": is_our, "depth_used": 0, "eval_cp": cp,
                              "fen": b.fen()})
                if not collapsed and cp <= collapse_cp:
                    collapsed, collapse_ply = True, ply
                if is_our:
                    our_count += 1
            leaf = material_cp(b, root_mover)
            our_reached = sum(1 for s in steps if s["mover"])
            lines.append({
                "seed_uci": root, "seed_san": san0, "steps": steps,
                "collapsed": collapsed, "collapse_ply": collapse_ply,
                "collapse_reason": (f"material fell to {leaf}cp" if collapsed else None),
                "final_cp": leaf, "score": float(leaf), "illegal": illegal_here,
                "our_reached": our_reached, "complete": our_reached >= our_moves,
                "calls": line_calls,
            })
        lines.sort(key=lambda ln: ln["score"], reverse=True)
        return {
            "engine": "llm", "our_moves": our_moves,
            "lines": lines, "best_uci": (lines[0]["seed_uci"] if lines else None),
            "ranking": [(ln["seed_san"], round(ln["score"], 1), ln["collapsed"]) for ln in lines],
            "calls_used": total_calls,
        }

    def _forced_move_turn(self, mode: str, sb_engine: str) -> dict:
        """The position has exactly one legal move: play it, no LLM, no selection."""
        board = self.board
        cfg = self.cfg
        class_depth = cfg.classification["class_depth"]
        forced = next(iter(board.legal_moves))
        cls = classify_move(self.pool, board, forced, cfg.classification, depth=class_depth)
        board_before = board.copy()
        san = board.san(forced)
        board.push(forced)
        concepts = detect_concepts(board, forced, board_before)
        our_eval = self.pool.eval_cp(board, depth=class_depth, pov=self.engine_color)
        crisis = self.memory.note_eval(our_eval)
        self.memory.recover_if_stable(our_eval)
        self.orch.update_after_move(board, forced, cls, our_eval)
        prop = {"uci": forced.uci(), "san": san, "source": "forced",
                "rationale": "Forced -- the only legal move."}
        cand = {"uci": forced.uci(), "san": san, "proposal": prop,
                "classification": cls.to_dict(), "beyond_horizon": False, "horizon": None}
        rec = {
            "fen_before": board_before.fen(), "mode": mode, "sandbox_engine": sb_engine,
            "established": [cand], "established_source": "forced", "established_count": 1,
            "establish_attempts": 0, "target": 1, "num_legal": 1,
            "selection_by": "forced (only legal move)", "autonomous_pick": None,
            "chosen": {**cand, "rationale": prop["rationale"]},
            "viable": [cand], "rejected": [], "illegal_attempts": [],
            "sandbox": {"engine": sb_engine, "lines": [], "ranking": [], "best_uci": forced.uci()},
            "deteval": None,
            "concepts": concepts, "manifest": self.memory.manifest.to_dict(),
            "audit": None, "our_eval_cp": our_eval, "crisis_triggered": crisis, "level": self.level,
        }
        rec["coaching"] = self.evaluator.coach(rec)
        self.memory.log_turn({"move": san, "uci": forced.uci(), "eval_cp": our_eval,
                              "label": cls.label, "mode": mode, "established": 1,
                              "source": "forced", "n_rejected": 0, "n_illegal": 0, "crisis": crisis})
        self.memory.persist()
        return {"ok": True, "turn": rec, "state": self.state()}

    # -- the engine's phenomenological turn ------------------------------------
    def engine_move(self, mode: str | None = None, sandbox_engine: str | None = None) -> dict:
        if self.board.is_game_over():
            return {"ok": False, "error": "game over", "state": self.state()}
        board = self.board
        cfg = self.cfg
        lvl = cfg.level(self.level)
        deep_mt = lvl.get("deep_movetime", 1.5)
        class_depth = cfg.classification["class_depth"]
        K = cfg.sandbox.get("candidate_k", 3)
        max_attempts = cfg.sandbox.get("max_establish_attempts", 24)
        mode = (mode or cfg.raw.get("selection_mode", "guided")).lower()
        if mode not in ("guided", "assist", "autonomous"):
            mode = "guided"
        sb_engine = (sandbox_engine or cfg.sandbox.get("engine", "stockfish")).lower()

        note = self.orch.minimal_note(board)      # one short line; NOT the manifest
        num_legal = board.legal_moves.count()
        target = min(K, num_legal)                # relax the 3-candidate rule when < 3 exist
        llm = self.client.available

        # Forced move: exactly one legal move -> play it directly. Skips the LLM
        # establishment loop (which otherwise flails in check/forced positions) and
        # any Stockfish selection -- its strength cannot contribute with no choice.
        if num_legal == 1:
            return self._forced_move_turn(mode, sb_engine)

        # ---- Establishment: the player builds its OWN slate of >=target legal
        #      moves, ONE move at a time, from a minimal context. Illegal tries
        #      accumulate by trial (unbounded in intent; the finite move set
        #      converges -- the attempt backstop only guards against endless
        #      unparseable garbage). Stockfish is NOT used to fill the slate.
        established: list[dict] = []
        illegal_attempts: list[str] = []          # raw strings tried and found illegal
        seen_illegal: set[str] = set()
        attempts = 0
        no_prog_cap = cfg.sandbox.get("no_progress_cap", 8)
        if llm:
            stagnant = 0                          # consecutive attempts that added nothing new
            while len(established) < target and attempts < max_attempts:
                attempts += 1
                chosen_ucis = [e["uci"] for e in established]
                prop = self.player.propose_one(board, note, illegal_attempts, chosen_ucis)
                if prop.move is None or prop.move not in board.legal_moves:
                    raw = (prop.raw or "").strip() or "?"
                    if raw not in seen_illegal:   # a NEW illegal move -> progress
                        seen_illegal.add(raw)
                        illegal_attempts.append(raw)
                        stagnant = 0
                    else:
                        stagnant += 1             # repeating the same reject -> no progress
                    if stagnant >= no_prog_cap:
                        break
                    continue
                uci = prop.move.uci()
                if uci in {e["uci"] for e in established}:
                    stagnant += 1                 # already have it; keep asking, but bounded
                    if stagnant >= no_prog_cap:
                        break
                    continue
                established.append({"uci": uci, "san": prop.san, "proposal": prop.to_dict()})
                stagnant = 0

        established_source = "llm" if established else "fallback"
        if not established:
            # No LLM (Stockfish-only brain) or the model produced nothing legal:
            # fall back to a Stockfish candidate slate so a move can still be made.
            for mv in self.player.stockfish_top(board, n=target, class_depth=class_depth):
                established.append({"uci": mv.uci(), "san": board.san(mv),
                                    "proposal": {"uci": mv.uci(), "san": board.san(mv),
                                                 "rationale": "Stockfish candidate (no LLM choices available).",
                                                 "source": "fallback"}})

        # classify every established candidate (for display / gate / coaching);
        # this is never shown to the autonomous picker.
        for e in established:
            cls = classify_move(self.pool, board, chess.Move.from_uci(e["uci"]),
                                cfg.classification, depth=class_depth)
            e["classification"] = cls.to_dict()

        # ---- Mode-specific filtering ----
        rejected: list[dict] = []
        if mode == "guided":
            viable = []
            for e in established:
                if not e["classification"]["flagged"]:
                    e["beyond_horizon"] = False
                    e["horizon"] = None
                    viable.append(e)
                    continue
                mv = chess.Move.from_uci(e["uci"])
                hv = assess_horizon(self.pool, board, mv, cfg.classification,
                                    horizon_plies=lvl["horizon"], deep_movetime=deep_mt)
                if not hv.within_horizon:
                    e["beyond_horizon"] = True
                    e["horizon"] = hv.to_dict()
                    viable.append(e)
                else:
                    cons = consequence_line(self.pool, board, mv,
                                            plies=lvl["horizon"], depth=class_depth)
                    e["horizon"] = hv.to_dict()
                    rejected.append({
                        "uci": e["uci"], "san": e["san"], "proposal": e["proposal"],
                        "classification": e["classification"], "horizon": hv.to_dict(),
                        "consequence": cons,
                        "reason": (f"{e['classification']['label']} understandable within the "
                                   f"{lvl['horizon']}-ply horizon (refutation by depth "
                                   f"{hv.reveal_depth})"),
                    })
            if not viable:                 # every candidate vetoed -> pick least-bad
                viable = established
        else:
            for e in established:
                e["beyond_horizon"] = False
                e["horizon"] = None
            viable = established

        # sandbox the viable set -- trees for display, and the ranking that
        # guided/assist select from. Engine: Stockfish (fast, default) or the
        # LLM-calculated variant (the model plays each line out, ~27+ calls).
        if sb_engine == "llm" and llm:
            sandbox = self._llm_sandbox(board, [v["uci"] for v in viable], note)
        else:
            sb_engine = "stockfish"
            seeds = [chess.Move.from_uci(v["uci"]) for v in viable]
            sandbox = run_sandbox(self.pool, board, seeds, cfg.sandbox)
            sandbox["engine"] = "stockfish"

        # ---- Selection ----
        autonomous_pick = None
        if mode == "autonomous" and llm:
            pick = self.player.choose_among(board, note, viable)
            if pick and pick["uci"] in {v["uci"] for v in viable}:
                chosen_uci = pick["uci"]
                autonomous_pick = pick
                selection_by = "llm"
            else:
                chosen_uci = viable[0]["uci"]
                selection_by = "llm (unparsed pick; used first candidate)"
        else:
            chosen_uci = sandbox.get("best_uci") or viable[0]["uci"]
            selection_by = "stockfish"
            if mode == "autonomous" and not llm:
                selection_by = "stockfish (no LLM available to pick)"

        chosen_v = next((v for v in viable if v["uci"] == chosen_uci), viable[0])
        chosen_move = chess.Move.from_uci(chosen_uci)

        # ---- Deterministic evaluator, module 1: material safety --------------
        # Runs AFTER selection. If the chosen move hangs material that one of the
        # player's OWN other candidates would keep, veto it (guided/assist) or warn
        # (autonomous). Deterministic, 1-ply, not Stockfish.
        deteval = None
        dcfg = cfg.raw.get("deteval", {})
        if dcfg.get("enabled", True) and viable:
            from ..engine.deteval import assess_candidates
            deteval = assess_candidates(board, [v["uci"] for v in viable], chosen_uci,
                                        dcfg.get("hang_threshold_cp", 100))
            netmap = {c["uci"]: c["net_cp"] for c in deteval["per_candidate"]}
            for v in viable:
                if v["uci"] in netmap:
                    v["safety_cp"] = netmap[v["uci"]]
            if deteval["hangs"]:
                veto = mode in ("guided", "assist") or (
                    mode == "autonomous" and dcfg.get("veto_in_autonomous", False))
                if veto:
                    chosen_uci = deteval["safe_alt"]
                    chosen_v = next(v for v in viable if v["uci"] == chosen_uci)
                    chosen_move = chess.Move.from_uci(chosen_uci)
                    deteval["action"] = "re-picked"
                    selection_by = f"{selection_by} → safety veto (module 1)"
                else:
                    deteval["action"] = "warned"   # autonomous: respect the LLM's pick
            else:
                deteval["action"] = "ok"

        # blind-spot audit before committing
        audit = self.orch.audit_blind_spot(board, chosen_move, movetime=deep_mt)

        # commit the move
        board_before = board.copy()
        chosen_san = board.san(chosen_move)
        board.push(chosen_move)
        concepts = detect_concepts(board, chosen_move, board_before)

        our_eval = self.pool.eval_cp(board, depth=class_depth, pov=self.engine_color)
        crisis = self.memory.note_eval(our_eval)
        self.memory.recover_if_stable(our_eval)
        self.orch.update_after_move(board, chosen_move,
                                    chosen_v.get("classification"), our_eval)

        chosen_rationale = (autonomous_pick["reasoning"] if autonomous_pick and autonomous_pick.get("reasoning")
                            else chosen_v["proposal"].get("rationale"))

        rec = {
            "fen_before": board_before.fen(),
            "mode": mode,
            "sandbox_engine": sb_engine,
            "established": established,
            "established_source": established_source,
            "established_count": len(established),
            "establish_attempts": attempts,
            "target": target,
            "num_legal": num_legal,
            "selection_by": selection_by,
            "autonomous_pick": autonomous_pick,
            "chosen": {**chosen_v, "san": chosen_san, "rationale": chosen_rationale},
            "viable": viable,
            "rejected": rejected,
            "illegal_attempts": illegal_attempts,
            "sandbox": sandbox,
            "deteval": deteval,
            "concepts": concepts,
            "manifest": self.memory.manifest.to_dict(),
            "audit": audit,
            "our_eval_cp": our_eval,
            "crisis_triggered": crisis,
            "level": self.level,
        }
        coaching = self.evaluator.coach(rec)
        rec["coaching"] = coaching
        self.memory.log_turn({
            "move": chosen_san, "uci": chosen_uci, "eval_cp": our_eval,
            "label": chosen_v["classification"]["label"], "mode": mode,
            "established": len(established), "source": established_source,
            "n_rejected": len(rejected), "n_illegal": len(illegal_attempts),
            "crisis": crisis,
        })
        self.memory.persist()

        return {"ok": True, "turn": rec, "state": self.state()}

    # -- single-state analysis (no move made) ----------------------------------
    def analyze(self, fen: str | None = None) -> dict:
        board = chess.Board(fen) if fen else self.board
        lvl = self.cfg.level(self.level)
        deep_mt = lvl.get("deep_movetime", 1.5)
        top = self.pool.top_moves(board, movetime=deep_mt, n=3)
        concepts = detect_concepts(board)
        defs = self.glossary.definitions_for(concepts)
        return {
            "ok": True,
            "fen": board.fen(),
            "eval_cp_white": self.pool.eval_cp(board, pov=chess.WHITE, movetime=deep_mt),
            "top_moves": [{"san": board.san(t["move"]), "uci": t["move"].uci(),
                           "cp": t["cp"], "mate": t["mate"]} for t in top],
            "concepts": concepts,
            "glossary": defs,
        }

    # -- state snapshot --------------------------------------------------------
    def state(self, extra: dict | None = None) -> dict:
        b = self.board
        result = None
        if b.is_game_over():
            result = b.result()
        d = {
            "fen": b.fen(),
            "turn": "white" if b.turn else "black",
            "engine_color": "white" if self.engine_color == chess.WHITE else "black",
            "legal_moves": [m.uci() for m in b.legal_moves],
            "history_san": _san_history(b),
            "is_check": b.is_check(),
            "is_game_over": b.is_game_over(),
            "result": result,
            "fullmove": b.fullmove_number,
            "manifest": self.memory.manifest.to_dict(),
            "provider": self.client.describe(),
            "level": self.level,
        }
        if extra:
            d.update(extra)
        return d

    def close(self):
        self.pool.close()
