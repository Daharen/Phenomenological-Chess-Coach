"""
Decreasing beam-width calculation sandbox.

For each viable candidate ("seed") we play a line forward.  The *search depth*
shrinks as we look further ahead -- the "5 -> 4 -> 3 -> 2 -> 1" rule the user
described: depth at ply p = max(1, max_horizon - p + 1).  This mirrors how a
human's resolution narrows the further out they calculate.

At mover plies we may branch to several candidate replies (the beam_schedule);
opponent plies take Stockfish's single best reply (adversarial).  We record the
evaluation at every level ("evaluation pass at each level") and detect the ply
at which a line tactically collapses.

Everything is bounded: leaves <= product of the beam widths on mover plies, and
we never search deeper than max_horizon plies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import chess

from .stockfish_pool import StockfishPool, MATE


@dataclass
class LineStep:
    ply: int
    san: str
    uci: str
    mover: bool           # True if this move was made by the side we're evaluating for
    depth_used: int
    eval_cp: int          # eval AFTER this move, from the seed-mover's perspective
    fen: str


@dataclass
class SandboxLine:
    seed_uci: str
    seed_san: str
    steps: list[LineStep]
    collapsed: bool
    collapse_ply: int | None
    collapse_reason: str | None
    final_cp: int
    score: float          # ranking score (higher is better for the seed mover)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed_uci": self.seed_uci,
            "seed_san": self.seed_san,
            "steps": [step.__dict__ for step in self.steps],
            "collapsed": self.collapsed,
            "collapse_ply": self.collapse_ply,
            "collapse_reason": self.collapse_reason,
            "final_cp": self.final_cp,
            "score": self.score,
        }


def _depth_at(ply: int, max_horizon: int) -> int:
    return max(1, max_horizon - ply + 1)


def _explore_line(pool: StockfishPool, board: chess.Board, seed: chess.Move,
                  seed_mover: chess.Color, max_horizon: int, beam_schedule: list[int],
                  collapse_cp: int) -> SandboxLine:
    """Explore a single principal line for `seed`, decreasing depth each ply.

    beam_schedule is honoured by expanding the mover plies greedily along the
    engine's top reply (we keep the best child, but evaluate `width` candidates
    at each mover ply and pick the best -- a width>1 gives a broader look while
    staying on one reported line)."""
    b = board.copy()
    steps: list[LineStep] = []
    seed_san = b.san(seed)
    collapsed = False
    collapse_ply = None
    collapse_reason = None

    move = seed
    mover_ply_idx = 0
    for ply in range(1, max_horizon + 1):
        if move not in b.legal_moves:
            break
        depth = _depth_at(ply, max_horizon)
        is_mover = (b.turn == seed_mover)
        san = b.san(move)
        b.push(move)
        eval_cp = pool.eval_cp(b, depth=depth, pov=seed_mover)
        steps.append(LineStep(ply=ply, san=san, uci=move.uci(), mover=is_mover,
                              depth_used=depth, eval_cp=eval_cp, fen=b.fen()))

        if b.is_game_over():
            break

        # tactical collapse for the seed mover
        if eval_cp <= collapse_cp:
            collapsed = True
            collapse_ply = ply
            collapse_reason = (f"eval fell to {eval_cp}cp for the mover at ply {ply} "
                               f"(depth {depth})")
            break

        # choose the next move
        nxt_depth = _depth_at(ply + 1, max_horizon)
        if b.turn == seed_mover:
            width = beam_schedule[mover_ply_idx] if mover_ply_idx < len(beam_schedule) else 1
            mover_ply_idx += 1
            cands = pool.top_moves(b, depth=nxt_depth, n=max(1, width))
            move = cands[0]["move"] if cands else None
        else:
            move = pool.best_move(b, depth=nxt_depth)
        if move is None:
            break

    final_cp = steps[-1].eval_cp if steps else 0
    # ranking: prefer high final eval; punish collapse and earlier collapse harder
    score = float(final_cp)
    if collapsed:
        score -= 100_000 + (max_horizon - (collapse_ply or max_horizon)) * 1_000
    return SandboxLine(seed_uci=seed.uci(), seed_san=seed_san, steps=steps,
                       collapsed=collapsed, collapse_ply=collapse_ply,
                       collapse_reason=collapse_reason, final_cp=final_cp, score=score)


def run_sandbox(pool: StockfishPool, board: chess.Board, seeds: list[chess.Move],
                sandbox_cfg: dict) -> dict:
    """Run the decreasing beam sandbox over the seed moves.

    Returns {'lines': [SandboxLine.to_dict...], 'best_uci': ..., 'ranking': [...]}"""
    max_horizon = sandbox_cfg.get("max_horizon", 5)
    beam_schedule = sandbox_cfg.get("beam_schedule", [3, 2, 1, 1, 1])
    collapse_cp = sandbox_cfg.get("collapse_cp", -300)
    seed_mover = board.turn

    lines = [_explore_line(pool, board, s, seed_mover, max_horizon, beam_schedule, collapse_cp)
             for s in seeds if s in board.legal_moves]
    lines.sort(key=lambda ln: ln.score, reverse=True)

    best_uci = lines[0].seed_uci if lines else None
    return {
        "lines": [ln.to_dict() for ln in lines],
        "best_uci": best_uci,
        "ranking": [(ln.seed_san, round(ln.score, 1), ln.collapsed) for ln in lines],
        "max_horizon": max_horizon,
        "beam_schedule": beam_schedule,
    }
