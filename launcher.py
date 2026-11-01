"""Operator entry point for the PyInstaller-built Windows executable.

Development: use `uv run python main.py` instead.
"""
from __future__ import annotations

import os
import sys
import traceback

os.environ.setdefault("HOST", "127.0.0.1")
os.environ.setdefault("OPEN_BROWSER", "1")

_BASE = os.path.dirname(os.path.abspath(__file__))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

if __name__ == "__main__":
    try:
        from main import run_application

        run_application()
    except Exception:  # keep the console open so the operator can read/photograph the error
        traceback.print_exc()
        try:
            input("\nThe station stopped with an error (see above and logs\\station.log). Press Enter to close...")
        except EOFError:
            pass
        raise SystemExit(1)
