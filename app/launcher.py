"""
Single entry point.  Started by the one .bat on C:.  Ensures data dirs exist,
starts the server, and opens the browser once -- one console window, GUI in the
browser.
"""

from __future__ import annotations

import sys
import threading
import time
import webbrowser

try:  # Windows consoles default to cp1252; coaching text may contain unicode
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from .config import load_config
from . import server


def _open_browser(url: str, delay: float = 1.5):
    def _go():
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:
            pass
    threading.Thread(target=_go, daemon=True).start()


def main(argv=None):
    argv = argv or sys.argv[1:]
    cfg = load_config()
    cfg.ensure_dirs()
    host = cfg.server["host"]
    port = cfg.server["port"]
    url = f"http://{host}:{port}"

    print("=" * 60)
    print(" Phenomenological Chess Coach")
    print(f" Stockfish : {cfg.stockfish_path}")
    print(f" Provider  : {cfg.provider}")
    print(f" Data dir  : {cfg.data_dir}")
    print(f" URL       : {url}")
    print("=" * 60)

    if cfg.server.get("open_browser", True) and "--no-browser" not in argv:
        _open_browser(url)
    server.run(host=host, port=port)


if __name__ == "__main__":
    main()
