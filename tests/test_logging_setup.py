import logging
from pathlib import Path
from types import SimpleNamespace

from codara.logging_setup import configure_logging


def test_configure_logging_creates_rotating_log_file(tmp_path):
    settings = SimpleNamespace(
        logs_root=str(tmp_path / "logs"),
        log_max_bytes=20 * 1024 * 1024,
        log_backup_count=5,
        debug=False,
    )

    log_path = configure_logging(settings, force=True)
    logging.getLogger("codara.tests").info("hello centralized log")

    assert log_path == (tmp_path / "logs" / "codara.log").resolve()
    assert log_path.exists()
    assert "hello centralized log" in log_path.read_text(encoding="utf-8")


def test_configure_logging_is_idempotent_for_same_path(tmp_path):
    settings = SimpleNamespace(
        logs_root=str(tmp_path / "logs"),
        log_max_bytes=20 * 1024 * 1024,
        log_backup_count=5,
        debug=False,
    )

    first = configure_logging(settings, force=True)
    second = configure_logging(settings)

    rotating_handlers = [
        handler
        for handler in logging.getLogger().handlers
        if handler.__class__.__name__ == "RotatingFileHandler"
    ]

    assert first == second
    assert len(rotating_handlers) == 1
