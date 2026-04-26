from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from codara.config import Settings, get_settings
from codara.runtime_log_store import RuntimeLogStore
from codara.telemetry import current_trace_context, serialize_log_record

_MANAGED_HANDLER_ATTR = "_codara_managed"
_CONFIGURED_LOG_PATH: Optional[Path] = None
_APP_LOGGER_NAME = "codara"
_runtime_log_emitter: Optional[callable] = None


def register_runtime_log_emitter(emitter: callable) -> None:
    global _runtime_log_emitter
    _runtime_log_emitter = emitter


class TraceContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        context = current_trace_context()
        record.trace_id = getattr(record, "trace_id", None) or (context.trace_id if context else None)
        record.span_id = getattr(record, "span_id", None) or (context.span_id if context else None)
        record.parent_span_id = getattr(record, "parent_span_id", None) or (context.parent_span_id if context else None)
        record.request_id = getattr(record, "request_id", None) or (context.request_id if context else None)
        record.component = getattr(record, "component", None) or (context.component if context else None)
        record.event_name = getattr(record, "event_name", None)
        record.event_attributes = getattr(record, "event_attributes", None)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return serialize_log_record(record)


class DatetimeShardedFileHandler(logging.Handler):
    def __init__(self, root: Path, formatter: logging.Formatter):
        super().__init__()
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.setFormatter(formatter)
        self._current_path: Optional[Path] = None
        self._stream = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            path = self._path_for_record(record)
            if self._current_path != path:
                self._switch_stream(path)
            if self._stream is None:
                return
            formatted = self.format(record)
            self._stream.write(formatted + "\n")
            self._stream.flush()
            
            if _runtime_log_emitter:
                try:
                    log_data = json.loads(formatted)
                    _runtime_log_emitter(log_data)
                except Exception:
                    pass
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        try:
            if self._stream is not None:
                self._stream.close()
        finally:
            self._stream = None
            self._current_path = None
            super().close()

    def _path_for_record(self, record: logging.LogRecord) -> Path:
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        return self.root / f"{dt.year:04d}" / f"{dt.month:02d}" / f"{dt.day:02d}" / f"{dt.hour:02d}.jsonl"

    def _switch_stream(self, path: Path) -> None:
        if self._stream is not None:
            self._stream.close()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = path.open("a", encoding="utf-8")
        self._current_path = path


def configure_logging(current_settings: Optional[Settings] = None, *, force: bool = False) -> Path:
    settings = current_settings or get_settings()
    logs_root = Path(settings.logs_root).expanduser().resolve()
    logs_root.mkdir(parents=True, exist_ok=True)
    runtime_root = Path(settings.runtime_log_root).expanduser()
    if not runtime_root.is_absolute():
        runtime_root = logs_root / runtime_root
    log_path = runtime_root.resolve()

    global _CONFIGURED_LOG_PATH
    app_logger = logging.getLogger(_APP_LOGGER_NAME)
    if not force and _CONFIGURED_LOG_PATH == log_path and any(
        getattr(handler, _MANAGED_HANDLER_ATTR, False) for handler in app_logger.handlers
    ):
        return log_path

    _remove_managed_handlers(app_logger)

    level = logging.DEBUG if settings.debug else logging.INFO
    formatter: logging.Formatter
    if settings.telemetry_json_logs:
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    trace_filter = TraceContextFilter()

    if settings.telemetry_json_logs and settings.log_persistence_backend == "datetime_file":
        file_handler = DatetimeShardedFileHandler(log_path, formatter)
    else:
        from logging.handlers import RotatingFileHandler

        file_handler = RotatingFileHandler(
            log_path / "codara.log",
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count,
            encoding="utf-8",
        )
    file_handler.setLevel(level)
    if not isinstance(file_handler, DatetimeShardedFileHandler):
        file_handler.setFormatter(formatter)
    file_handler.addFilter(trace_filter)
    setattr(file_handler, _MANAGED_HANDLER_ATTR, True)

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(trace_filter)
    setattr(stream_handler, _MANAGED_HANDLER_ATTR, True)

    app_logger.setLevel(level)
    app_logger.addHandler(file_handler)
    app_logger.addHandler(stream_handler)
    app_logger.propagate = False

    # Leave framework loggers alone by default. Optionally adjust their verbosity (levels only).
    framework_level = getattr(settings, "framework_log_level", None)
    if isinstance(framework_level, str) and framework_level.strip():
        token = framework_level.strip()
        resolved_level = getattr(logging, token.upper(), None)
        if not isinstance(resolved_level, int):
            try:
                resolved_level = int(token)
            except ValueError:
                resolved_level = None
        if isinstance(resolved_level, int):
            for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
                logging.getLogger(logger_name).setLevel(resolved_level)

    logging.captureWarnings(False)
    _CONFIGURED_LOG_PATH = log_path
    retention_days = int(getattr(settings, "log_retention_days", 0) or 0)
    if settings.telemetry_json_logs and settings.log_persistence_backend == "datetime_file" and retention_days > 0:
        cutoff_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000) - retention_days * 24 * 60 * 60 * 1000
        RuntimeLogStore(str(log_path)).prune_older_than(cutoff_ms)
    app_logger.info("Codara logging initialized at %s", log_path)
    return log_path


def _remove_managed_handlers(target_logger: logging.Logger) -> None:
    for handler in list(target_logger.handlers):
        if not getattr(handler, _MANAGED_HANDLER_ATTR, False):
            continue
        target_logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
