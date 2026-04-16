import logging
import json
from pathlib import Path
from types import SimpleNamespace

from codara.logging_setup import configure_logging
from codara.telemetry import start_trace


def test_configure_logging_creates_rotating_log_file(tmp_path):
    settings = SimpleNamespace(
        logs_root=str(tmp_path / "logs"),
        log_max_bytes=20 * 1024 * 1024,
        log_backup_count=5,
        log_persistence_backend="datetime_file",
        runtime_log_root="runtime",
        debug=False,
        telemetry_json_logs=True,
    )

    log_path = configure_logging(settings, force=True)
    with start_trace("logging.test", component="tests.logging"):
        logging.getLogger("codara.tests").info("hello centralized log")

    assert log_path == (tmp_path / "logs" / "runtime").resolve()
    shard_files = sorted(log_path.glob("**/*.jsonl"))
    assert shard_files
    lines = [line for line in shard_files[-1].read_text(encoding="utf-8").splitlines() if line.strip()]
    payloads = [json.loads(line) for line in lines]
    payload = next(item for item in payloads if item["message"] == "hello centralized log")
    assert payload["message"] == "hello centralized log"
    assert payload["trace_id"].startswith("trc_")


def test_configure_logging_is_idempotent_for_same_path(tmp_path):
    settings = SimpleNamespace(
        logs_root=str(tmp_path / "logs"),
        log_max_bytes=20 * 1024 * 1024,
        log_backup_count=5,
        log_persistence_backend="datetime_file",
        runtime_log_root="runtime",
        debug=False,
        telemetry_json_logs=True,
    )

    first = configure_logging(settings, force=True)
    second = configure_logging(settings)

    managed_file_handlers = [
        handler
        for handler in logging.getLogger().handlers
        if handler.__class__.__name__ in {"RotatingFileHandler", "DatetimeShardedFileHandler"}
    ]

    assert first == second
    assert len(managed_file_handlers) == 1
