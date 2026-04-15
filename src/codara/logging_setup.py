from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from codara.config import Settings, get_settings

_MANAGED_HANDLER_ATTR = "_codara_managed"
_CONFIGURED_LOG_PATH: Optional[Path] = None


def configure_logging(current_settings: Optional[Settings] = None, *, force: bool = False) -> Path:
    settings = current_settings or get_settings()
    logs_root = Path(settings.logs_root).expanduser().resolve()
    logs_root.mkdir(parents=True, exist_ok=True)
    log_path = logs_root / "codara.log"

    global _CONFIGURED_LOG_PATH
    root_logger = logging.getLogger()
    if not force and _CONFIGURED_LOG_PATH == log_path and any(
        getattr(handler, _MANAGED_HANDLER_ATTR, False) for handler in root_logger.handlers
    ):
        return log_path

    _remove_managed_handlers(root_logger)

    level = logging.DEBUG if settings.debug else logging.INFO
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    setattr(file_handler, _MANAGED_HANDLER_ATTR, True)

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)
    setattr(stream_handler, _MANAGED_HANDLER_ATTR, True)

    root_logger.setLevel(level)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(stream_handler)

    for logger_name in ("codara", "uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        logger = logging.getLogger(logger_name)
        logger.setLevel(level)
        logger.handlers = []
        logger.propagate = True

    logging.captureWarnings(True)
    _CONFIGURED_LOG_PATH = log_path
    root_logger.info("Centralized logging initialized at %s", log_path)
    return log_path


def _remove_managed_handlers(root_logger: logging.Logger) -> None:
    for handler in list(root_logger.handlers):
        if not getattr(handler, _MANAGED_HANDLER_ATTR, False):
            continue
        root_logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
