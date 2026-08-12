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

    # -- the engine's phenomenological turn ------------------------------------
    def engine_move(self, mode: str | None = None) -> dict:
        if self.board.is_game_over():
            return {"ok": False, "error": "game over", "state": self.state()}
        board = self.board
        cfg = self.cfg
        lvl = cfg.level(self.level)
        deep_mt = lvl.get("deep_movetime", 1.5)
        class_depth = cfg.classification["class_depth"]
        K = cfg.sandbox.get("candidate_k", 3)
        max_attempts = cfg.sandbox.get("max_establish_attempts", 64)
        mode = (mode or cfg.raw.get("selection_mode", "guided")).lower()
        if mode not in ("guided", "assist", "autonomous"):
            mode = "guided"

        note = self.orch.minimal_note(board)      # one short line; NOT the manifest
        num_legal = board.legal_moves.count()
        target = min(K, num_legal)
        llm = self.client.available

        # ---- Establishment: the player builds its OWN slate of >=target legal
        #      moves, ONE move at a time, from a minimal context. Illegal tries
        #      accumulate by trial (unbounded in intent; the finite move set
        #      converges -- the attempt backstop only guards against endless
        #      unparseable garbage). Stockfish is NOT used to fill the slate.
        established: list[dict] = []
        illegal_attempts: list[str] = []          # raw strings tried and found illegal
        seen_illegal: set[str] = set()
        attempts = 0
        if llm:
            while len(established) < target and attempts < max_attempts:
                attempts += 1
                chosen_ucis = [e["uci"] for e in established]
                prop = self.player.propose_one(board, note, illegal_attempts, chosen_ucis)
                if prop.move is None or prop.move not in board.legal_moves:
                    raw = (prop.raw or "").strip() or "?"
                    if raw not in seen_illegal:
                        seen_illegal.add(raw)
                        illegal_attempts.append(raw)
                    continue
                uci = prop.move.uci()
                if uci in {e["uci"] for e in established}:
                    continue                      # already have it; ask for a different one
                established.append({"uci": uci, "san": prop.san, "proposal": prop.to_dict()})

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

        # sandbox the viable set (decreasing beam width / horizon) -- trees for
        # display, and the ranking that guided/assist select from.
        seeds = [chess.Move.from_uci(v["uci"]) for v in viable]
        sandbox = run_sandbox(self.pool, board, seeds, cfg.sandbox)

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
