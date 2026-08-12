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
    def engine_move(self) -> dict:
        if self.board.is_game_over():
            return {"ok": False, "error": "game over", "state": self.state()}
        board = self.board
        cfg = self.cfg
        lvl = cfg.level(self.level)
        deep_mt = lvl.get("deep_movetime", 1.5)
        class_depth = cfg.classification["class_depth"]
        K = cfg.sandbox.get("candidate_k", 3)
        max_attempts = cfg.sandbox.get("max_player_attempts", 6)
        max_illegal = cfg.sandbox.get("max_illegal_retries", 8)

        context = self.orch.context_for_player(board)

        viable: list[dict] = []
        rejected: list[dict] = []
        illegal_attempts: list[str] = []
        blocked: list[str] = []          # uci ruled out (illegal or refuted)
        seen: set[str] = set()
        feedback = None
        attempts = 0
        illegal_count = 0

        while len(viable) < K and attempts < max_attempts:
            prop = self.player.propose(board, context, blocked, feedback, class_depth)

            # illegal / unparseable
            if prop.move is None or prop.move not in board.legal_moves:
                illegal_count += 1
                raw = prop.raw or (prop.move.uci() if prop.move else "?")
                illegal_attempts.append(raw)
                if raw not in blocked:
                    blocked.append(raw)
                feedback = (f"The move '{raw}' is not legal here. Pick a different, "
                            f"legal move.")
                if illegal_count >= max_illegal:
                    break
                continue

            uci = prop.move.uci()
            if uci in seen:
                if uci not in blocked:
                    blocked.append(uci)
                feedback = "You already proposed that move; choose a different candidate."
                attempts += 1
                continue
            seen.add(uci)
            attempts += 1

            cls = classify_move(self.pool, board, prop.move, cfg.classification, depth=class_depth)

            if not cls.flagged:
                viable.append({"uci": uci, "san": prop.san, "proposal": prop.to_dict(),
                               "classification": cls.to_dict(), "horizon": None,
                               "beyond_horizon": False})
                feedback = None
                continue

            # flagged -> comprehension-horizon gate
            hv = assess_horizon(self.pool, board, prop.move, cfg.classification,
                                horizon_plies=lvl["horizon"], deep_movetime=deep_mt)
            if not hv.within_horizon:
                # the refutation lives beyond the player's horizon -> allow it
                viable.append({"uci": uci, "san": prop.san, "proposal": prop.to_dict(),
                               "classification": cls.to_dict(), "horizon": hv.to_dict(),
                               "beyond_horizon": True})
                feedback = None
                continue

            # within horizon -> reject, show the consequence, try again
            cons = consequence_line(self.pool, board, prop.move,
                                    plies=lvl["horizon"], depth=class_depth)
            first = " ".join(s["san"] for s in cons["trajectory"][:5])
            reason = (f"{cls.label} understandable within the {lvl['horizon']}-ply "
                      f"horizon (refutation visible by depth {hv.reveal_depth})")
            rejected.append({"uci": uci, "san": prop.san, "reason": reason,
                             "classification": cls.to_dict(), "horizon": hv.to_dict(),
                             "consequence": cons})
            if uci not in blocked:
                blocked.append(uci)
            feedback = (f"Your move {prop.san} is a {cls.label}. After {first}, the "
                        f"evaluation falls to about {cls.played_cp}cp for you. "
                        f"Choose a sounder move.")

        # safety net: if nothing viable, take Stockfish's best directly
        if not viable:
            bm = self.pool.best_move(board, movetime=deep_mt)
            if bm is None:
                return {"ok": False, "error": "no legal moves", "state": self.state()}
            cls = classify_move(self.pool, board, bm, cfg.classification, depth=class_depth)
            viable.append({"uci": bm.uci(), "san": board.san(bm),
                           "proposal": {"uci": bm.uci(), "san": board.san(bm),
                                        "rationale": "Engine safety net.", "source": "engine"},
                           "classification": cls.to_dict(), "horizon": None,
                           "beyond_horizon": False, "safety_net": True})

        # sandbox the viable candidates (decreasing beam width / horizon)
        seeds = [chess.Move.from_uci(v["uci"]) for v in viable]
        sandbox = run_sandbox(self.pool, board, seeds, cfg.sandbox)
        chosen_uci = sandbox.get("best_uci") or viable[0]["uci"]
        chosen_v = next((v for v in viable if v["uci"] == chosen_uci), viable[0])
        chosen_move = chess.Move.from_uci(chosen_uci)

        # blind-spot audit before committing
        audit = self.orch.audit_blind_spot(board, chosen_move, movetime=deep_mt)

        # commit the move
        board_before = board.copy()
        chosen_san = board.san(chosen_move)
        board.push(chosen_move)
        concepts = detect_concepts(board, chosen_move, board_before)

        # eval from the engine's perspective for the Blunder Protocol
        our_eval = self.pool.eval_cp(board, depth=class_depth, pov=self.engine_color)
        crisis = self.memory.note_eval(our_eval)
        self.memory.recover_if_stable(our_eval)
        self.orch.update_after_move(board, chosen_move,
                                    chosen_v.get("classification"), our_eval)

        rec = {
            "fen_before": board_before.fen(),
            "chosen": {**chosen_v, "san": chosen_san,
                       "rationale": chosen_v["proposal"].get("rationale")},
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
            "label": chosen_v["classification"]["label"],
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
