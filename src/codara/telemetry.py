from __future__ import annotations

import contextvars
import json
import logging
import os
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter, time
from typing import TYPE_CHECKING, Any, Optional
from uuid import uuid4

from codara.config import get_settings

if TYPE_CHECKING:
    from codara.database.manager import DatabaseManager


logger = logging.getLogger("codara.telemetry")

_REDACTED = "***REDACTED***"
_SENSITIVE_FIELD_PARTS = (
    "token",
    "secret",
    "password",
    "authorization",
    "credential",
    "api_key",
    "key_hash",
    "cookie",
)


@dataclass(frozen=True)
class TraceContext:
    trace_id: str
    span_id: str
    parent_span_id: Optional[str]
    request_id: Optional[str]
    component: Optional[str]
    span_name: Optional[str]


_current_trace_context: contextvars.ContextVar[Optional[TraceContext]] = contextvars.ContextVar(
    "codara_trace_context",
    default=None,
)


def _next_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def current_trace_context() -> Optional[TraceContext]:
    return _current_trace_context.get()


def current_trace_id() -> Optional[str]:
    context = current_trace_context()
    return context.trace_id if context else None


def current_request_id() -> Optional[str]:
    context = current_trace_context()
    return context.request_id if context else None


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_FIELD_PARTS)


def sanitize_attributes(value: Any, *, max_attr_length: Optional[int] = None, key_hint: Optional[str] = None) -> Any:
    settings = get_settings()
    limit = int(max_attr_length or settings.telemetry_max_attr_length)
    if key_hint and _is_sensitive_key(key_hint):
        return _REDACTED
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        return value[:limit] + "...[truncated]"
    if isinstance(value, dict):
        return {str(key): sanitize_attributes(item, max_attr_length=limit, key_hint=str(key)) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [sanitize_attributes(item, max_attr_length=limit) for item in value]
    return sanitize_attributes(str(value), max_attr_length=limit, key_hint=key_hint)


def emit_structured_log(
    *,
    level: int,
    message: str,
    event_name: Optional[str] = None,
    component: Optional[str] = None,
    attributes: Optional[dict[str, Any]] = None,
    exc_info: Any = None,
) -> None:
    context = current_trace_context()
    safe_attributes = sanitize_attributes(attributes or {})
    logging.getLogger(component or "codara").log(
        level,
        message,
        extra={
            "trace_id": context.trace_id if context else None,
            "span_id": context.span_id if context else None,
            "parent_span_id": context.parent_span_id if context else None,
            "request_id": context.request_id if context else None,
            "component": component or (context.component if context else None),
            "event_name": event_name,
            "event_attributes": safe_attributes or None,
        },
        exc_info=exc_info,
    )


def record_event(
    name: str,
    *,
    component: Optional[str] = None,
    attributes: Optional[dict[str, Any]] = None,
    db: Optional["DatabaseManager"] = None,
    level: str = "INFO",
    status: Optional[str] = None,
) -> None:
    settings = get_settings()
    if not settings.telemetry_enabled:
        return
    context = current_trace_context()
    safe_attributes = sanitize_attributes(attributes or {})
    log_level = getattr(logging, level.upper(), logging.INFO)
    emit_structured_log(
        level=log_level,
        message=name,
        event_name=name,
        component=component,
        attributes=safe_attributes,
    )
    if not settings.telemetry_persist_traces or db is None or context is None:
        return
    
    is_test = os.environ.get("PYTEST_CURRENT_TEST") is not None
    db.record_trace_event(
        trace_id=context.trace_id,
        span_id=context.span_id,
        parent_span_id=context.parent_span_id,
        kind="event",
        name=name,
        component=component or context.component,
        level=level.upper(),
        status=status,
        request_id=context.request_id,
        started_at_ms=int(time() * 1000),
        ended_at_ms=None,
        duration_ms=None,
        attributes=safe_attributes,
        sync=is_test,
    )


class TraceSpan(AbstractContextManager["TraceSpan"], AbstractAsyncContextManager["TraceSpan"]):
    def __init__(
        self,
        name: str,
        *,
        component: Optional[str] = None,
        db: Optional["DatabaseManager"] = None,
        attributes: Optional[dict[str, Any]] = None,
        request_id: Optional[str] = None,
        new_trace: bool = False,
    ):
        self.name = name
        self.component = component
        self.db = db
        self.attributes = attributes or {}
        self.request_id = request_id
        self.new_trace = new_trace
        self._token: Optional[contextvars.Token] = None
        self._context: Optional[TraceContext] = None
        self._started_at_ms = 0
        self._started_perf = 0.0

    def __enter__(self) -> "TraceSpan":
        self._open()
        return self

    async def __aenter__(self) -> "TraceSpan":
        return self.__enter__()

    def __exit__(self, exc_type, exc, tb) -> None:
        self._close(exc)
        return None

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.__exit__(exc_type, exc, tb)
        return None

    @property
    def trace_id(self) -> Optional[str]:
        return self._context.trace_id if self._context else None

    def _open(self) -> None:
        settings = get_settings()
        if not settings.telemetry_enabled:
            return
        parent = current_trace_context()
        trace_id = _next_id("trc") if self.new_trace or parent is None else parent.trace_id
        parent_span_id = None if self.new_trace or parent is None else parent.span_id
        self._context = TraceContext(
            trace_id=trace_id,
            span_id=_next_id("spn"),
            parent_span_id=parent_span_id,
            request_id=self.request_id or (parent.request_id if parent else None),
            component=self.component or (parent.component if parent else None),
            span_name=self.name,
        )
        self._token = _current_trace_context.set(self._context)
        self._started_at_ms = int(time() * 1000)
        self._started_perf = perf_counter()
        emit_structured_log(
            level=logging.INFO,
            message=f"{self.name}.started",
            event_name=f"{self.name}.started",
            component=self.component,
            attributes=sanitize_attributes(self.attributes),
        )
        if settings.telemetry_persist_traces and self.db is not None:
             is_test = os.environ.get("PYTEST_CURRENT_TEST") is not None
             self.db.record_trace_event(
                trace_id=self._context.trace_id,
                span_id=self._context.span_id,
                parent_span_id=self._context.parent_span_id,
                kind="span.started",
                name=self.name,
                component=self.component or self._context.component,
                level="INFO",
                status="ok",
                request_id=self._context.request_id,
                started_at_ms=self._started_at_ms,
                ended_at_ms=None,
                duration_ms=None,
                attributes=sanitize_attributes(self.attributes),
                sync=is_test,
            )

    def _close(self, exc: Optional[BaseException]) -> None:
        settings = get_settings()
        if not settings.telemetry_enabled or self._context is None:
            return
        duration_ms = round((perf_counter() - self._started_perf) * 1000, 2)
        status = "error" if exc else "ok"
        attributes = dict(self.attributes)
        if exc is not None:
            attributes["error"] = str(exc)
            attributes["exception_type"] = exc.__class__.__name__
        safe_attributes = sanitize_attributes(attributes)
        emit_structured_log(
            level=logging.ERROR if exc else logging.INFO,
            message=f"{self.name}.completed",
            event_name=f"{self.name}.completed",
            component=self.component,
            attributes={**(safe_attributes or {}), "duration_ms": duration_ms, "status": status},
            exc_info=exc,
        )
        if settings.telemetry_persist_traces and self.db is not None:
            is_test = os.environ.get("PYTEST_CURRENT_TEST") is not None
            self.db.record_trace_event(
                trace_id=self._context.trace_id,
                span_id=self._context.span_id,
                parent_span_id=self._context.parent_span_id,
                kind="span.completed",
                name=self.name,
                component=self.component or self._context.component,
                level="ERROR" if exc else "INFO",
                status=status,
                request_id=self._context.request_id,
                started_at_ms=self._started_at_ms,
                ended_at_ms=int(time() * 1000),
                duration_ms=duration_ms,
                attributes=safe_attributes,
                sync=is_test,
            )
        if self._token is not None:
            _current_trace_context.reset(self._token)
            self._token = None


def start_trace(
    name: str,
    *,
    component: Optional[str] = None,
    db: Optional["DatabaseManager"] = None,
    attributes: Optional[dict[str, Any]] = None,
    request_id: Optional[str] = None,
) -> TraceSpan:
    return TraceSpan(
        name,
        component=component,
        db=db,
        attributes=attributes,
        request_id=request_id,
        new_trace=True,
    )


def start_span(
    name: str,
    *,
    component: Optional[str] = None,
    db: Optional["DatabaseManager"] = None,
    attributes: Optional[dict[str, Any]] = None,
) -> TraceSpan:
    return TraceSpan(
        name,
        component=component,
        db=db,
        attributes=attributes,
        new_trace=False,
    )


def serialize_log_record(record: logging.LogRecord) -> str:
    payload = {
        "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
        "level": record.levelname,
        "logger": record.name,
        "message": record.getMessage(),
        "trace_id": getattr(record, "trace_id", None),
        "span_id": getattr(record, "span_id", None),
        "parent_span_id": getattr(record, "parent_span_id", None),
        "request_id": getattr(record, "request_id", None),
        "component": getattr(record, "component", None),
        "event_name": getattr(record, "event_name", None),
        "attributes": getattr(record, "event_attributes", None),
    }
    if record.exc_info:
        payload["exception"] = logging.Formatter().formatException(record.exc_info)
    return json.dumps(payload, ensure_ascii=True, sort_keys=True)
