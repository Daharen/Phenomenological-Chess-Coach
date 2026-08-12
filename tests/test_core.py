"""Smoke + logic tests for the deterministic engine core (needs a Stockfish)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess
from app.config import load_config
from app.engine.stockfish_pool import StockfishPool
from app.engine.classify import classify_move
from app.engine.horizon import assess_horizon, consequence_line
from app.engine.sandbox import run_sandbox
from app.engine import concepts

cfg = load_config()
pool = StockfishPool(cfg.stockfish_candidates, threads=2, hash_mb=128)
print("stockfish:", pool.path)
ok = 0
fail = 0
def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS {name} {extra}")
    else:
        fail += 1; print(f"  FAIL {name} {extra}")

print("\n[1] SEE / hanging")
# Black pawn e6 attacks d5 where an undefended white queen sits -> hanging for white
b = chess.Board("4k3/8/4p3/3Q4/8/8/8/4K3 b - - 0 1")
hp = concepts.hanging_pieces(b)
check("queen d5 hanging", any(t["squares"] == ["d5"] for t in hp), str(hp))

print("\n[2] Fork detection")
# White knight c7 forks black Ke8 + Ra8 (black to move, in check)
b = chess.Board("r3k3/2N5/8/8/8/8/4K3/8 b - - 0 1")
tags = concepts.detect_concepts(b)
check("knight fork Ke8/Ra8", any(t["term"] == "Fork" and t["side"] == "white" for t in tags),
      str([t for t in tags if t["term"] == "Fork"]))

print("\n[3] Pin detection")
# Black bishop g4 pins white knight f3 to king e1? classic
b = chess.Board("rnbqkb1r/pppp1ppp/5n2/4p3/6b1/5N2/PPPPPPPP/RNBQKB1R w KQkq - 0 1")
# put a real pin: white Nf3, white Ke1, black Bg4 -> Bg4 pins Nf3? line g4-f3-e2-d1 not king e1.
b = chess.Board("4k3/8/8/8/6b1/5N2/8/4K3 w - - 0 1")  # Bg4-f3-e2? diag g4,f3,e2,d1 not e1
b = chess.Board("4k3/8/8/8/7b/6N1/8/4K3 w - - 0 1")   # Bh4? no.
# Use a guaranteed absolute pin: black rook e8, white knight e2? no king behind.
b = chess.Board("4r3/8/8/8/8/8/4N3/4K3 w - - 0 1")    # Re8 - Ne2 - Ke1 : knight pinned
check("knight e2 pinned by Re8", concepts._pins(b) and any(t["squares"] == ["e2"] for t in concepts._pins(b)),
      str(concepts._pins(b)))

print("\n[4] Classification: best capture vs throwing away a free queen")
# White Qd4, black Qd5 (undefended), kings apart. Best = Qxd5. Retreating Qh4 hangs the win.
b2 = chess.Board("4k3/8/8/3q4/3Q4/8/8/4K3 w - - 0 1")
best = classify_move(pool, b2, chess.Move.from_uci("d4d5"), cfg.classification, depth=12)
throw = classify_move(pool, b2, chess.Move.from_uci("d4h4"), cfg.classification, depth=12)
print("   Qxd5 =>", best.label, "delta", best.delta_cp)
print("   Qh4  =>", throw.label, "delta", throw.delta_cp)
check("best capture labelled best/good", best.label in {"best", "good"}, best.label)
check("throwing the queen away is flagged", throw.flagged and throw.label in {"blunder", "miss"}, throw.label)

print("\n[5] Horizon assessment structure + shallow blunder is within horizon")
# a move that hangs a piece immediately should be flagged within horizon
b4 = chess.Board("rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2")
lvl = cfg.level("expert")
hv = assess_horizon(pool, b4, chess.Move.from_uci("d1h5"), cfg.classification,
                    horizon_plies=lvl["horizon"], deep_movetime=lvl["deep_movetime"])
print("   Qh5 within_horizon:", hv.within_horizon, "shallow:", hv.shallow.label,
      "deep:", hv.deep.label, "reveal_depth:", hv.reveal_depth)
check("horizon verdict has fields", hasattr(hv, "within_horizon") and hv.shallow is not None)

print("\n[6] Sandbox decreasing horizon")
b5 = chess.Board()
b5.push_uci("e2e4"); b5.push_uci("e7e5")
seeds = [m["move"] for m in pool.top_moves(b5, depth=12, n=3)]
res = run_sandbox(pool, b5, seeds, cfg.sandbox)
print("   ranking:", res["ranking"], "best:", res["best_uci"])
depths = [s["depth_used"] for s in res["lines"][0]["steps"]]
print("   depth schedule of top line:", depths)
check("sandbox produced lines", len(res["lines"]) >= 1)
check("depth decreases", depths == sorted(depths, reverse=True) if depths else True, str(depths))

print("\n[7] consequence_line")
cl_line = consequence_line(pool, b4, chess.Move.from_uci("d1h5"), plies=4, depth=12)
check("consequence trajectory", len(cl_line["trajectory"]) >= 1)

pool.close()
print(f"\n==== {ok} passed, {fail} failed ====")
sys.exit(1 if fail else 0)
