"""
Move classification: best / good / inaccuracy / mistake / blunder / miss / loss.

Everything is measured from the perspective of the side that made the move.
The centipawn loss is (best-move eval) - (played-move eval) at the same depth.
Mate scores are folded into a large integer by the Stockfish wrapper.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

import chess

from .stockfish_pool import StockfishPool, MATE

FLAGGED = {"inaccuracy", "mistake", "blunder", "miss", "loss"}


@dataclass
class Classification:
    label: str
    delta_cp: int                 # centipawn loss vs best (>=0 typically)
    played_cp: int                # eval after the move, mover pov
    best_cp: int                  # eval of best move, mover pov
    best_move_uci: str | None
    best_move_san: str | None
    played_uci: str
    played_san: str
    got_mated: bool = False       # move walks into a forced mate (mover pov)
    missed_mate: bool = False     # a forced mate existed and was not played
    depth: int = 0

    @property
    def flagged(self) -> bool:
        return self.label in FLAGGED

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["flagged"] = self.flagged
        return d


def classify_move(pool: StockfishPool, board: chess.Board, move: chess.Move,
                  cfg: dict, depth: int | None = None,
                  movetime: float | None = None) -> Classification:
    if movetime is None and depth is None:
        depth = cfg.get("class_depth", 12)
    if movetime is not None:
        depth = None
    mover = board.turn

    top = pool.analyse(board, depth=depth, movetime=movetime, multipv=1)
    best_move = top[0]["pv"][0] if top[0]["pv"] else None
    best_pov = top[0]["score"].pov(mover)
    best_cp = best_pov.score(mate_score=MATE)
    reached_depth = top[0].get("depth") or (depth or 0)

    played_san = board.san(move)
    board.push(move)
    after = pool.analyse(board, depth=depth, movetime=movetime, multipv=1)
    after_pov = after[0]["score"].pov(mover)
    played_cp = after_pov.score(mate_score=MATE)
    board.pop()

    delta = best_cp - played_cp

    got_mated = after_pov.is_mate() and (after_pov.mate() or 0) < 0
    best_was_mate = best_pov.is_mate() and (best_pov.mate() or 0) > 0
    played_is_mate_for_mover = after_pov.is_mate() and (after_pov.mate() or 0) > 0

    win_thr = cfg.get("win_threshold_cp", 350)
    miss_drop = cfg.get("miss_drop_cp", 200)
    inacc = cfg.get("inaccuracy_cp", 50)
    mist = cfg.get("mistake_cp", 120)
    blun = cfg.get("blunder_cp", 250)

    missed_mate = best_was_mate and not played_is_mate_for_mover

    if got_mated:
        label = "loss"
    elif missed_mate:
        label = "miss"
    elif best_cp >= win_thr and played_cp < (win_thr - 1) and delta >= miss_drop:
        # had a clearly winning move, threw the win away without getting mated
        label = "miss"
    elif delta >= blun:
        label = "blunder"
    elif delta >= mist:
        label = "mistake"
    elif delta >= inacc:
        label = "inaccuracy"
    elif delta > 0:
        label = "good"
    else:
        label = "best"

    return Classification(
        label=label,
        delta_cp=int(delta),
        played_cp=int(played_cp),
        best_cp=int(best_cp),
        best_move_uci=best_move.uci() if best_move else None,
        best_move_san=(board.san(best_move) if best_move else None),
        played_uci=move.uci(),
        played_san=played_san,
        got_mated=bool(got_mated),
        missed_mate=bool(missed_mate),
        depth=int(reached_depth or 0),
    )


def cp_to_winprob(cp: int) -> float:
    """Lichess-style win probability (0..1) for the side to move from a cp eval."""
    import math
    cp = max(-1500, min(1500, cp))
    return 1.0 / (1.0 + math.exp(-0.00368208 * cp))
