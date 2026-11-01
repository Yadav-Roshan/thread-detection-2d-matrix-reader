"""Station logging: console + rotating file in logs/station.log (kept for shop-floor diagnosis)."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from src.core import config

_configured = False


def setup_logging() -> logging.Logger:
    global _configured
    logger = logging.getLogger("station")
    if _configured:
        return logger
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(config.LOG_DIR / "station.log", maxBytes=5_000_000, backupCount=5,
                                       encoding="utf-8")
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console)
    _configured = True
    return logger
