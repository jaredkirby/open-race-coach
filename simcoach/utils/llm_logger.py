"""Structured logging wrapper used across Open Race Coach."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_CONFIGURED = False


def configure_logging(log_dir: Path | str = "logs", level: int = logging.INFO) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        log_path / "simcoach.log",
        maxBytes=1_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root = logging.getLogger("simcoach")
    root.setLevel(level)
    root.addHandler(console)
    root.addHandler(file_handler)
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)
