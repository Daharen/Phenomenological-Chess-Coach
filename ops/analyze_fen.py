"""Headless end-to-end smoke test: run one full engine turn on a FEN (or start).

Run:  python -m ops.analyze_fen ["<FEN>"]
"""
from __future__ import annotations
import json, os, sys
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess
from app.config import load_config
from app.game.coach import ChessCoach


def main():
    fen = sys.argv[1] if len(sys.argv) > 1 else None
    cfg = load_config()
    coach = ChessCoach(cfg)
    if fen:
        coach.board = chess.Board(fen)
        # engine plays the side to move
        coach.engine_color = coach.board.turn
    else:
        coach.new_game("white", level=cfg.level_name)
        coach.human_move("e2e4")

    res = coach.engine_move()
    if not res["ok"]:
        print(json.dumps(res, indent=2)); return 1
    t = res["turn"]
    out = {
        "provider": coach.client.describe(),
        "stockfish": coach.pool.path,
        "chosen": t["chosen"]["san"],
        "label": t["chosen"]["classification"]["label"],
        "viable": [v["san"] for v in t["viable"]],
        "rejected": [(r["san"], r["reason"]) for r in t["rejected"]],
        "sandbox_ranking": t["sandbox"]["ranking"],
        "concepts": sorted({c["term"] for c in t["concepts"]}),
        "coaching_source": t["coaching"]["source"],
        "coaching": t["coaching"]["text"],
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    coach.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
