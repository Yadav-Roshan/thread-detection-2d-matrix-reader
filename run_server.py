"""Backwards-compatible launcher; prefer `uv run python main.py`."""
from main import run_application

if __name__ == "__main__":
    run_application()
