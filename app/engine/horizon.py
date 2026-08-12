"""
The comprehension-horizon principle.

A move that Stockfish dislikes is only *counted against* the player if the cost
is understandable within the player's horizon.  Concretely: if the move already
looks bad when the engine is constrained to a shallow, human-like search depth
(~horizon plies), the player could have seen it -> it is rejected.  If the move
only turns bad under deep engine search, the refutation lives beyond the horizon
-> it is allowed to go forward ("moves bad only because of a machine-level tactic
too deep to understand aren't actually bad" at this level).

We also locate the *reveal depth*: the shallowest depth at which the move first
looks bad, which the coach uses to explain how far away the refutation lived.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import chess

from .classify import classify_move, Classification, FLAGGED
from .stockfish_pool import StockfishPool


@dataclass
class HorizonVerdict:
    within_horizon: bool          # True -> the cost is comprehensible -> reject
    shallow: Classification       # classification at ~horizon depth
    deep: Classification          # classification at deep engine depth
    reveal_depth: int | None      # shallowest depth at which it first looked bad
    horizon_plies: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "within_horizon": self.within_horizon,
            "shallow": self.shallow.to_dict(),
            "deep": self.deep.to_dict(),
            "reveal_depth": self.reveal_depth,
            "horizon_plies": self.horizon_plies,
        }


def _shallow_depth_for(horizon_plies: int) -> int:
    # Map a ply horizon to a Stockfish search depth used as the "human sight"
    # proxy.  Stockfish depth is already selective/iterative, so depth==horizon
    # is a reasonable, slightly generous proxy.
    return max(2, horizon_plies)


def assess_horizon(pool: StockfishPool, board: chess.Board, move: chess.Move,
                   cfg_class: dict, horizon_plies: int,
                   deep_movetime: float = 1.5) -> HorizonVerdict:
    """Two bounded probes instead of a deep depth scan:
      * shallow: a depth==horizon search (human sight) -- cheap.
      * deep:    a movetime-bounded search (machine sight) -- capped latency.
    A move is 'within horizon' (understandable, so counted) iff it already reads
    as flagged at the shallow, human depth.  The reveal depth is approximated by
    the depth the bounded deep search reached when it (and only it) flags the move.
    """
    shallow_depth = _shallow_depth_for(horizon_plies)

    shallow = classify_move(pool, board, move, cfg_class, depth=shallow_depth)
    deep = classify_move(pool, board, move, cfg_class, movetime=deep_movetime)

    within = shallow.label in FLAGGED

    reveal_depth: int | None = None
    if deep.label in FLAGGED:
        reveal_depth = shallow_depth if within else deep.depth

    return HorizonVerdict(
        within_horizon=within,
        shallow=shallow,
        deep=deep,
        reveal_depth=reveal_depth,
        horizon_plies=horizon_plies,
    )


def consequence_line(pool: StockfishPool, board: chess.Board, move: chess.Move,
                     plies: int, depth: int) -> dict:
    """Play the move, then best play by both sides for `plies`, returning the
    SAN/FEN trajectory so the player can *see* why the move fails."""
    b = board.copy()
    trajectory = []
    first_san = b.san(move)
    b.push(move)
    trajectory.append({"san": first_san, "fen": b.fen(), "by": "candidate"})
    for _ in range(plies):
        if b.is_game_over():
            break
        bm = pool.best_move(b, depth=depth)
        if bm is None:
            break
        san = b.san(bm)
        b.push(bm)
        trajectory.append({"san": san, "fen": b.fen(), "by": "best"})
    return {"start_fen": board.fen(), "trajectory": trajectory}
