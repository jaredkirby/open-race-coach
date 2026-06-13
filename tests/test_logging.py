from __future__ import annotations

import logging
from logging import Handler
from logging.handlers import RotatingFileHandler
from pathlib import Path

from simcoach.utils import llm_logger


def test_configure_logging_uses_console_and_rotating_file_handler(tmp_path: Path) -> None:
    logger = logging.getLogger("simcoach")
    original_handlers: list[Handler] = list(logger.handlers)
    original_level = logger.level
    original_propagate = logger.propagate
    original_configured = llm_logger._CONFIGURED
    logger.handlers.clear()
    llm_logger._CONFIGURED = False
    try:
        llm_logger.configure_logging(log_dir=tmp_path)

        rotating_handlers = [
            handler for handler in logger.handlers if isinstance(handler, RotatingFileHandler)
        ]
        stream_handlers = [
            handler for handler in logger.handlers if type(handler) is logging.StreamHandler
        ]
        assert len(rotating_handlers) == 1
        assert len(stream_handlers) == 1
        assert rotating_handlers[0].baseFilename == str(tmp_path / "simcoach.log")
        assert rotating_handlers[0].backupCount == 5
        assert rotating_handlers[0].maxBytes == 1_000_000
        assert logger.propagate is False
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers[:] = original_handlers
        logger.setLevel(original_level)
        logger.propagate = original_propagate
        llm_logger._CONFIGURED = original_configured
