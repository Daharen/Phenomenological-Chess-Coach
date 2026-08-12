"""Health check: Stockfish, LLM provider reachability, data dirs, glossary.

Run:  python -m ops.doctor        (from the program dir)
"""
from __future__ import annotations
import json, os, sys
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import load_config, program_root
from app.agents.base import make_client


def main():
    cfg = load_config()
    report = {"ok": True, "checks": {}}

    # Stockfish
    try:
        from app.engine.stockfish_pool import StockfishPool
        pool = StockfishPool(cfg.stockfish_candidates,
                             threads=cfg.raw.get("stockfish_threads", 2), hash_mb=64)
        import chess
        _ = pool.eval_cp(chess.Board(), depth=8)
        report["checks"]["stockfish"] = {"ok": True, "path": pool.path}
        pool.close()
    except Exception as e:
        report["ok"] = False
        report["checks"]["stockfish"] = {"ok": False, "error": str(e)}

    # Data dirs
    try:
        cfg.ensure_dirs()
        subs = {name: (cfg.sub(name).exists()) for name in
                ("logs", "games", "runs", "glossary", "cache")}
        report["checks"]["data_dir"] = {"ok": all(subs.values()),
                                        "path": str(cfg.data_dir), "subdirs": subs}
        if not all(subs.values()):
            report["ok"] = False
    except Exception as e:
        report["ok"] = False
        report["checks"]["data_dir"] = {"ok": False, "error": str(e)}

    # Glossary
    gpath = program_root() / "config" / "glossary.json"
    try:
        data = json.loads(gpath.read_text(encoding="utf-8"))
        report["checks"]["glossary"] = {"ok": True, "terms": len(data.get("terms", {}))}
    except Exception as e:
        report["ok"] = False
        report["checks"]["glossary"] = {"ok": False, "error": str(e)}

    # LLM provider
    client = make_client(cfg)
    report["checks"]["provider"] = {"provider": cfg.provider,
                                    "available": client.available,
                                    "describe": client.describe()}
    if cfg.provider == "gemini":
        report["checks"]["provider"]["api_key_present"] = bool(cfg.gemini_key())

    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
