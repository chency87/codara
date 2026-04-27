import logging
import hashlib
import json
import os
import queue
import sqlite3
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List
from amesh.config import get_settings
from amesh.trace_store import FileTraceStore

from amesh.core.models import (
    Session,
    ProviderType,
    SessionStatus,
    User,
    ApiKey,
    UserStatus,
    WorkspaceReset,
    Workspace,
    Task,
)

class DatabaseManager:
    def __init__(self, db_path: str = "amesh.db"):
        self._logger = logging.getLogger("amesh.database")
        self.db_path = db_path
        self._initialize_db()
        settings = get_settings()
        self._trace_backend = settings.telemetry_persistence_backend.strip().lower()
        trace_root = Path(settings.telemetry_trace_root).expanduser()
        if not trace_root.is_absolute():
            trace_root = Path(settings.logs_root).expanduser() / trace_root
        self._trace_store = FileTraceStore(str(trace_root)) if self._trace_backend == "file" else None
        if self._trace_store is not None and settings.telemetry_trace_retention_days > 0:
            cutoff_ms = self._now_ms() - settings.telemetry_trace_retention_days * 24 * 60 * 60 * 1000
            self._trace_store.prune_older_than(cutoff_ms)
        
        runtime_log_root = Path(settings.runtime_log_root).expanduser()
        if not runtime_log_root.is_absolute():
            runtime_log_root = Path(settings.logs_root).expanduser() / runtime_log_root
        from amesh.runtime_log_store import RuntimeLogStore
        self._runtime_log_store = RuntimeLogStore(str(runtime_log_root))
        
        self._trace_queue: queue.Queue = queue.Queue(maxsize=10000)
        self._trace_queue_dropped = 0
        self._trace_worker_thread = threading.Thread(target=self._trace_worker, daemon=True, name="trace-persist-worker")
        self._trace_worker_thread.start()

    def _trace_worker(self):
        while True:
            batch = []
            try:
                # Wait for at least one item
                item = self._trace_queue.get()
                batch.append(item)
                
                # Try to get more items if available, up to 200
                while len(batch) < 200:
                    try:
                        item = self._trace_queue.get_nowait()
                        batch.append(item)
                    except queue.Empty:
                        break
                
                if batch:
                    self._persist_trace_batch(batch)
                
                for _ in range(len(batch)):
                    self._trace_queue.task_done()
                    
            except Exception:
                self._logger.exception("Trace persistence worker crashed; retrying")
                time.sleep(1)

    def _persist_trace_batch(self, batch: List[tuple]):
        try:
            if self._trace_store is not None:
                rows = [
                    {
                        "event_id": item[0],
                        "trace_id": item[1],
                        "span_id": item[2],
                        "parent_span_id": item[3],
                        "kind": item[4],
                        "name": item[5],
                        "component": item[6],
                        "level": item[7],
                        "status": item[8],
                        "request_id": item[9],
                        "started_at": item[10],
                        "ended_at": item[11],
                        "duration_ms": item[12],
                        "attributes": json.loads(item[13]) if item[13] else None,
                    }
                    for item in batch
                ]
                self._trace_store.append_batch(rows)
            
            with self._get_connection() as conn:
                conn.executemany(
                    """
                    INSERT INTO trace_events
                    (event_id, trace_id, span_id, parent_span_id, kind, name, component, level, status, request_id, started_at, ended_at, duration_ms, attributes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    batch
                )
                
                # Logic to split batch into specific tables
                for item in batch:
                    (event_id, trace_id, span_id, parent_span_id, kind, name, 
                    component, level, status, request_id, started_at, ended_at, 
                    duration_ms, attributes) = item

                    if kind == "span.started" and not parent_span_id:
                        # Root span = trace
                        conn.execute(
                            "INSERT OR IGNORE INTO traces (trace_id, request_id, name, component, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                            (trace_id, request_id, name, component, status or "ok", started_at)
                        )
                    
                    if kind in ("span.started", "span.completed"):
                        conn.execute(
                            "INSERT OR REPLACE INTO spans (span_id, trace_id, parent_span_id, name, component, status, start_time, end_time, duration_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (span_id, trace_id, parent_span_id, name, component, status or "ok", started_at, ended_at, duration_ms)
                        )
                    elif kind == "event":
                        conn.execute(
                            "INSERT OR REPLACE INTO events (event_id, trace_id, span_id, name, component, level, message, attributes, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (event_id, trace_id, span_id, name, component, level or "INFO", name, attributes, started_at)
                        )
                    elif kind == "log":
                        conn.execute(
                            "INSERT INTO runtime_logs (log_id, timestamp, level, logger, message, request_id, trace_id, span_id, attributes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (event_id, started_at, level or "INFO", component, name, request_id, trace_id, span_id, attributes)
                        )

                conn.commit()
        except Exception:
            # If batch persist fails, we log it (if we had a logger here) and continue
            pass

    def wait_for_traces(self):
        """Wait for all currently queued trace events to be persisted. Used primarily for testing."""
        self._trace_queue.join()

    def _get_connection(self):
        db_path = Path(self.db_path)
        if not db_path.parent.exists():
            db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _initialize_db(self):
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS workspaces (
                    workspace_id    TEXT        PRIMARY KEY,
                    name            TEXT        NOT NULL,
                    path            TEXT        NOT NULL,
                    user_id         TEXT        NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    template        TEXT        NOT NULL DEFAULT 'default',
                    default_provider TEXT,
                    created_at      INTEGER     NOT NULL,
                    updated_at      INTEGER     NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id         TEXT        PRIMARY KEY,
                    workspace_id       TEXT        NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
                    client_session_id  TEXT,
                    backend_id         TEXT        NOT NULL,
                    provider           TEXT        NOT NULL,
                    user_id            TEXT        NOT NULL REFERENCES users(user_id),
                    api_key_id         TEXT,
                    cwd_path           TEXT        NOT NULL,
                    status             TEXT        NOT NULL DEFAULT 'idle',
                    created_at         INTEGER     NOT NULL,
                    updated_at         INTEGER     NOT NULL,
                    expires_at         INTEGER     NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id          TEXT        PRIMARY KEY,
                    session_id       TEXT        NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                    workspace_id     TEXT        NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
                    user_id          TEXT        NOT NULL REFERENCES users(user_id),
                    prompt           TEXT        NOT NULL,
                    status           TEXT        NOT NULL DEFAULT 'pending',
                    result           TEXT,
                    created_at       INTEGER     NOT NULL,
                    updated_at       INTEGER     NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    audit_id         TEXT        PRIMARY KEY,
                    actor            TEXT        NOT NULL,
                    action           TEXT        NOT NULL,
                    target_type      TEXT        NOT NULL,
                    target_id        TEXT        NOT NULL,
                    before_state     TEXT,
                    after_state      TEXT,
                    request_id       TEXT,
                    timestamp        INTEGER     NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS turns (
                    turn_id          TEXT        PRIMARY KEY,
                    session_id       TEXT        NOT NULL REFERENCES sessions(session_id),
                    user_id          TEXT,
                    provider         TEXT        NOT NULL,
                    finish_reason    TEXT,
                    duration_ms      INTEGER     NOT NULL DEFAULT 0,
                    diff             TEXT,
                    actions          TEXT,
                    timestamp        INTEGER     NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS traces (
                    trace_id         TEXT        PRIMARY KEY,
                    request_id       TEXT        UNIQUE,
                    name             TEXT        NOT NULL,
                    component        TEXT,
                    status           TEXT        NOT NULL DEFAULT 'ok',
                    created_at       INTEGER     NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS spans (
                    span_id          TEXT        PRIMARY KEY,
                    trace_id         TEXT        NOT NULL REFERENCES traces(trace_id) ON DELETE CASCADE,
                    parent_span_id   TEXT,
                    name             TEXT        NOT NULL,
                    component        TEXT,
                    status           TEXT        NOT NULL DEFAULT 'ok',
                    start_time       INTEGER     NOT NULL,
                    end_time         INTEGER,
                    duration_ms      REAL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    event_id         TEXT        PRIMARY KEY,
                    trace_id         TEXT        NOT NULL REFERENCES traces(trace_id) ON DELETE CASCADE,
                    span_id          TEXT        REFERENCES spans(span_id) ON DELETE CASCADE,
                    name             TEXT        NOT NULL,
                    component        TEXT,
                    level            TEXT        NOT NULL DEFAULT 'INFO',
                    message          TEXT,
                    attributes       TEXT,
                    timestamp        INTEGER     NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS runtime_logs (
                    log_id           TEXT        PRIMARY KEY,
                    timestamp        INTEGER     NOT NULL,
                    level            TEXT        NOT NULL,
                    logger           TEXT,
                    message          TEXT,
                    request_id       TEXT,
                    trace_id         TEXT,
                    span_id          TEXT,
                    attributes       TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON runtime_logs(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_request ON runtime_logs(request_id)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trace_events (
                    event_id          TEXT        PRIMARY KEY,
                    trace_id          TEXT        NOT NULL,
                    span_id           TEXT,
                    parent_span_id    TEXT,
                    kind              TEXT        NOT NULL,
                    name              TEXT        NOT NULL,
                    component         TEXT,
                    level             TEXT,
                    status            TEXT,
                    request_id        TEXT,
                    started_at        INTEGER     NOT NULL,
                    ended_at          INTEGER,
                    duration_ms       REAL,
                    attributes        TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id         TEXT        PRIMARY KEY,
                    email           TEXT        NOT NULL UNIQUE,
                    display_name    TEXT        NOT NULL,
                    status          TEXT        NOT NULL DEFAULT 'active',
                    workspace_path  TEXT        NOT NULL UNIQUE,
                    created_at      INTEGER     NOT NULL,
                    created_by      TEXT        NOT NULL,
                    updated_at      INTEGER     NOT NULL,
                    max_api_keys    INTEGER     NOT NULL DEFAULT 1,
                    max_concurrency INTEGER     NOT NULL DEFAULT 3
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    key_id          TEXT        PRIMARY KEY,
                    user_id         TEXT        NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    key_hash        TEXT        NOT NULL UNIQUE,
                    key_prefix      TEXT        NOT NULL,
                    label           TEXT,
                    status          TEXT        NOT NULL DEFAULT 'active',
                    last_used_at    INTEGER,
                    expires_at      INTEGER,
                    created_at      INTEGER     NOT NULL,
                    revoked_at      INTEGER
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS workspace_resets (
                    reset_id        TEXT        PRIMARY KEY,
                    user_id         TEXT        NOT NULL REFERENCES users(user_id),
                    triggered_by    TEXT        NOT NULL,
                    actor_id        TEXT        NOT NULL,
                    sessions_wiped  INTEGER     NOT NULL,
                    reset_at        INTEGER     NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS channel_user_links (
                    channel          TEXT        NOT NULL,
                    bot_name         TEXT        NOT NULL DEFAULT '',
                    external_user_id TEXT        NOT NULL,
                    user_id          TEXT        NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    external_chat_id TEXT,
                    status           TEXT        NOT NULL DEFAULT 'active',
                    created_at       INTEGER     NOT NULL,
                    updated_at       INTEGER     NOT NULL,
                    PRIMARY KEY (channel, bot_name, external_user_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS channel_conversations (
                    channel          TEXT        NOT NULL,
                    bot_name         TEXT        NOT NULL DEFAULT '',
                    conversation_key TEXT        NOT NULL,
                    user_id          TEXT        NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    external_chat_id TEXT,
                    external_thread_id TEXT,
                    workspace_id     TEXT        NOT NULL DEFAULT 'default',
                    provider         TEXT        NOT NULL DEFAULT 'codex',
                    session_label    TEXT        NOT NULL,
                    created_at       INTEGER     NOT NULL,
                    updated_at       INTEGER     NOT NULL,
                    PRIMARY KEY (channel, bot_name, conversation_key)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS channel_link_tokens (
                    token_id         TEXT        PRIMARY KEY,
                    token_hash       TEXT        NOT NULL UNIQUE,
                    user_id          TEXT        NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    channel          TEXT        NOT NULL,
                    bot_name         TEXT        NOT NULL DEFAULT '',
                    created_by       TEXT        NOT NULL,
                    created_at       INTEGER     NOT NULL,
                    expires_at       INTEGER     NOT NULL,
                    consumed_at      INTEGER
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS channel_runtime_state (
                    channel          TEXT        NOT NULL,
                    bot_name         TEXT        NOT NULL DEFAULT '',
                    state_key        TEXT        NOT NULL,
                    state_value      TEXT,
                    updated_at       INTEGER     NOT NULL,
                    PRIMARY KEY (channel, bot_name, state_key)
                )
            """)
            self._migrate_legacy_usage_schema(conn)
            self._ensure_column(conn, "turns", "user_id", "TEXT")
            self._ensure_column(conn, "sessions", "user_id", "TEXT")
            self._ensure_column(conn, "sessions", "cwd_path", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "sessions", "api_key_id", "TEXT")
            self._ensure_column(conn, "users", "max_concurrency", "INTEGER NOT NULL DEFAULT 3")
            conn.execute("UPDATE users SET max_api_keys = 1 WHERE COALESCE(max_api_keys, 1) != 1")
            # Indices
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_workspace ON sessions(workspace_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user    ON sessions(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_turns_session   ON turns(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trace_events_trace ON trace_events(trace_id, started_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trace_events_request ON trace_events(request_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trace_events_component ON trace_events(component, started_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trace_events_started ON trace_events(started_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_email      ON users(email)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_user     ON api_keys(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_hash     ON api_keys(key_hash)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_resets_user       ON workspace_resets(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_channel_links_user ON channel_user_links(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_channel_conversations_user ON channel_conversations(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_channel_tokens_user ON channel_link_tokens(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_channel_tokens_channel ON channel_link_tokens(channel)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_channel_runtime_state_channel ON channel_runtime_state(channel, bot_name)")
            self._ensure_column(conn, "channel_user_links", "bot_name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "channel_conversations", "bot_name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "channel_link_tokens", "bot_name", "TEXT NOT NULL DEFAULT ''")
            conn.commit()

    def _ensure_column(self, conn, table: str, column: str, column_type: str):
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")

    def _migrate_legacy_usage_schema(self, conn) -> None:
        desired_sessions = [
            ("session_id", "TEXT", "PRIMARY KEY"),
            ("workspace_id", "TEXT", "NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE"),
            ("client_session_id", "TEXT", ""),
            ("backend_id", "TEXT", "NOT NULL"),
            ("provider", "TEXT", "NOT NULL"),
            ("user_id", "TEXT", "NOT NULL REFERENCES users(user_id)"),
            ("api_key_id", "TEXT", ""),
            ("cwd_path", "TEXT", "NOT NULL"),
            ("status", "TEXT", "NOT NULL DEFAULT 'idle'"),
            ("created_at", "INTEGER", "NOT NULL"),
            ("updated_at", "INTEGER", "NOT NULL"),
            ("expires_at", "INTEGER", "NOT NULL"),
        ]
        desired_turns = [
            ("turn_id", "TEXT", "PRIMARY KEY"),
            ("session_id", "TEXT", "NOT NULL REFERENCES sessions(session_id)"),
            ("user_id", "TEXT", ""),
            ("provider", "TEXT", "NOT NULL"),
            ("finish_reason", "TEXT", ""),
            ("duration_ms", "INTEGER", "NOT NULL DEFAULT 0"),
            ("diff", "TEXT", ""),
            ("actions", "TEXT", ""),
            ("timestamp", "INTEGER", "NOT NULL"),
        ]

        sessions_cols = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        turns_cols = {row["name"] for row in conn.execute("PRAGMA table_info(turns)").fetchall()}

        if {"account_id", "last_context_tokens"} & sessions_cols:
            self._rebuild_table(
                conn,
                table="sessions",
                desired_columns=desired_sessions,
                legacy_columns=sessions_cols,
            )
        if {"account_id", "input_tokens", "output_tokens", "cache_hit_tokens"} & turns_cols:
            self._rebuild_table(
                conn,
                table="turns",
                desired_columns=desired_turns,
                legacy_columns=turns_cols,
            )

    def _rebuild_table(
        self,
        conn,
        *,
        table: str,
        desired_columns: list[tuple[str, str, str]],
        legacy_columns: set[str],
    ) -> None:
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        conn.execute("PRAGMA foreign_keys = OFF")
        old_table = f"{table}__old"
        conn.execute(f"ALTER TABLE {table} RENAME TO {old_table}")

        column_defs: list[str] = []
        for name, col_type, suffix in desired_columns:
            suffix_sql = f" {suffix}" if suffix else ""
            column_defs.append(f"{name} {col_type}{suffix_sql}")
        conn.execute(f"CREATE TABLE {table} ({', '.join(column_defs)})")

        def select_expr(name: str) -> str:
            if name in legacy_columns:
                return name
            if name in {"backend_id", "cwd_path"}:
                return "''"
            if name == "status":
                return "'idle'"
            if name in {"created_at", "updated_at", "expires_at", "timestamp"}:
                return "0"
            return "NULL"

        desired_names = [name for name, _col_type, _suffix in desired_columns]
        select_sql = ", ".join(select_expr(name) for name in desired_names)
        insert_cols_sql = ", ".join(desired_names)
        conn.execute(
            f"INSERT INTO {table} ({insert_cols_sql}) SELECT {select_sql} FROM {old_table}"
        )
        conn.execute(f"DROP TABLE {old_table}")
        if foreign_keys:
            conn.execute("PRAGMA foreign_keys = ON")

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _now_ms(self) -> int:
        return int(self._now().timestamp() * 1000)

    def _row_to_datetime(self, value: Optional[int]) -> Optional[datetime]:
        if value is None:
            return None
        if value > 10_000_000_000:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        return datetime.fromtimestamp(value, tz=timezone.utc)

    def _generate_ulid_like(self, prefix: str) -> str:
        from uuid import uuid4

        return f"{prefix}_{uuid4().hex[:12]}"

    def _hash_api_key(self, raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode()).hexdigest()

    def _hash_channel_token(self, raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode()).hexdigest()

    def _json_default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError("Type %s not serializable" % type(obj))

    def record_turn(
        self,
        *,
        turn_id: str,
        session_id: str,
        provider: str,
        finish_reason: str,
        duration_ms: int,
        diff: Optional[str],
        actions: Optional[List[dict]],
        user_id: str,
    ):
        now = int(self._now().timestamp())
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO turns 
                (turn_id, session_id, user_id, provider, finish_reason, duration_ms, diff, actions, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                turn_id,
                session_id,
                user_id,
                provider,
                finish_reason,
                duration_ms,
                diff,
                json.dumps(actions) if actions else None,
                now
            ))
            conn.commit()

    def save_task(self, task: Task):
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO tasks
                (task_id, session_id, workspace_id, user_id, prompt, status, result, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                task.task_id,
                task.session_id,
                task.workspace_id,
                task.user_id,
                task.prompt,
                task.status,
                task.result.model_dump_json() if task.result else None,
                int(task.created_at.timestamp() * 1000),
                int(task.updated_at.timestamp() * 1000),
            ))
            conn.commit()

    def get_task(self, task_id: str) -> Optional[Task]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            return self._row_to_task(row) if row else None

    def _row_to_task(self, row) -> Task:
        return Task(
            task_id=row["task_id"],
            session_id=row["session_id"],
            workspace_id=row["workspace_id"],
            user_id=row["user_id"],
            prompt=row["prompt"],
            status=row["status"],
            result=TurnResult.model_validate_json(row["result"]) if row["result"] else None,
            created_at=self._row_to_datetime(row["created_at"]),
            updated_at=self._row_to_datetime(row["updated_at"]),
        )

    def list_session_tasks(self, session_id: str) -> List[dict]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    task_id,
                    session_id,
                    workspace_id,
                    user_id,
                    prompt,
                    status,
                    result,
                    created_at,
                    updated_at
                FROM tasks
                WHERE session_id = ?
                ORDER BY created_at ASC
                """,
                (session_id,),
            ).fetchall()

            items: list[dict] = []
            for row in rows:
                record = dict(row)
                result = record.get("result")
                if result:
                    try:
                        record["result"] = json.loads(result)
                    except json.JSONDecodeError:
                        record["result"] = None
                items.append(record)
            return items

    def get_session_turns(self, session_id: str) -> List[dict]:
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT * FROM turns WHERE session_id = ? ORDER BY timestamp ASC
            """, (session_id,)).fetchall()
            return [dict(row) for row in rows]

    def get_provider_stats(self) -> List[dict]:
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT provider, 
                       COUNT(DISTINCT session_id) as active_sessions,
                       COUNT(*) as total_turns
                FROM turns
                GROUP BY provider
            """).fetchall()
            return [dict(row) for row in rows]

    def record_audit(self, actor: str, action: str, target_type: str, target_id: str,
                     before: Optional[dict] = None, after: Optional[dict] = None,
                     request_id: Optional[str] = None):
        from uuid import uuid4

        audit_id = f"aud_{uuid4().hex[:12]}"
        now = int(self._now().timestamp())
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO audit_log 
                (audit_id, actor, action, target_type, target_id, before_state, after_state, request_id, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                audit_id, actor, action, target_type, target_id,
                json.dumps(before, default=self._json_default) if before else None,
                json.dumps(after, default=self._json_default) if after else None,
                request_id,
                now
            ))
            conn.commit()

    def record_trace_event(
        self,
        *,
        trace_id: str,
        span_id: Optional[str],
        parent_span_id: Optional[str],
        kind: str,
        name: str,
        component: Optional[str],
        level: Optional[str],
        status: Optional[str],
        request_id: Optional[str],
        started_at_ms: int,
        ended_at_ms: Optional[int],
        duration_ms: Optional[float],
        attributes: Optional[dict],
        sync: bool = False,
    ) -> str:
        event_id = self._generate_ulid_like("trc_evt")
        row = (
            event_id,
            trace_id,
            span_id,
            parent_span_id,
            kind,
            name,
            component,
            level,
            status,
            request_id,
            started_at_ms,
            ended_at_ms,
            duration_ms,
            json.dumps(attributes, default=self._json_default) if attributes else None,
        )

        if sync:
            self._persist_trace_batch([row])
            return event_id

        try:
            self._trace_queue.put_nowait(row)
        except queue.Full:
            # Drop the event if the queue is overloaded to avoid impacting main thread.
            self._trace_queue_dropped += 1
            dropped = self._trace_queue_dropped
            # Log on powers-of-two and then every 1000 drops to avoid log spam.
            if dropped & (dropped - 1) == 0 or dropped % 1000 == 0:
                self._logger.warning(
                    "Trace queue full; dropped %d trace events so far (maxsize=%d)",
                    dropped,
                    getattr(self._trace_queue, "maxsize", 0),
                )
        return event_id

    def list_traces(
        self,
        *,
        limit: int = 50,
        after: Optional[int] = None,
        component: Optional[str] = None,
        request_id: Optional[str] = None,
        status: Optional[str] = None,
        trace_id: Optional[str] = None,
        search: Optional[str] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
    ) -> List[dict]:
        if self._trace_store is not None:
            return self._trace_store.list_traces(
                limit=limit,
                after=after,
                component=component,
                request_id=request_id,
                status=status,
                trace_id=trace_id,
                search=search,
                since=since,
                until=until,
            )
        query = """
            SELECT trace_id, span_id, name, component, level, status, request_id, started_at, ended_at, duration_ms, attributes
            FROM trace_events
            WHERE kind = 'span' AND parent_span_id IS NULL
        """
        params: list[object] = []
        if after is not None:
            query += " AND started_at < ?"
            params.append(after)
        if since is not None:
            query += " AND started_at >= ?"
            params.append(since)
        if until is not None:
            query += " AND started_at <= ?"
            params.append(until)
        if component:
            query += " AND component = ?"
            params.append(component)
        if request_id:
            query += " AND request_id = ?"
            params.append(request_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        if trace_id:
            query += " AND trace_id = ?"
            params.append(trace_id)
        if search:
            pattern = f"%{search.lower()}%"
            query += """
             AND (
                lower(trace_id) LIKE ?
                OR lower(name) LIKE ?
                OR lower(component) LIKE ?
                OR lower(coalesce(request_id, '')) LIKE ?
                OR lower(coalesce(attributes, '')) LIKE ?
             )
            """
            params.extend([pattern, pattern, pattern, pattern, pattern])
        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._deserialize_trace_row(dict(row)) for row in rows]

    def prune_traces(self, retention_days: int) -> dict:
        if retention_days <= 0:
            return {"files_deleted": 0, "files_rewritten": 0, "records_deleted": 0}
        cutoff_ms = self._now_ms() - retention_days * 24 * 60 * 60 * 1000
        if self._trace_store is not None:
            return self._trace_store.prune_older_than(cutoff_ms)
        with self._get_connection() as conn:
            before = conn.execute("SELECT COUNT(*) AS count FROM trace_events").fetchone()["count"]
            conn.execute("DELETE FROM trace_events WHERE started_at < ?", (cutoff_ms,))
            conn.commit()
            after = conn.execute("SELECT COUNT(*) AS count FROM trace_events").fetchone()["count"]
        return {"files_deleted": 0, "files_rewritten": 0, "records_deleted": int(before) - int(after)}

    def get_trace_events(self, trace_id: str) -> List[dict]:
        if self._trace_store is not None:
            return self._trace_store.get_trace_events(trace_id)
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM trace_events
                WHERE trace_id = ?
                ORDER BY started_at ASC, event_id ASC
                """,
                (trace_id,),
            ).fetchall()
            return [self._deserialize_trace_row(dict(row)) for row in rows]

    def _deserialize_trace_row(self, row: dict) -> dict:
        attributes = row.get("attributes")
        if isinstance(attributes, str) and attributes:
            try:
                row["attributes"] = json.loads(attributes)
            except json.JSONDecodeError:
                pass
        return row

    def get_audit_logs(
        self,
        limit: int = 50,
        after: Optional[int] = None,
        actor: Optional[str] = None,
        action: Optional[str] = None,
        target_type: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[dict]:
        query = "SELECT * FROM audit_log WHERE 1 = 1"
        params: list[object] = []
        if after is not None:
            query += " AND timestamp < ?"
            params.append(after)
        if actor:
            query += " AND actor = ?"
            params.append(actor)
        if action:
            query += " AND action = ?"
            params.append(action)
        if target_type:
            query += " AND target_type = ?"
            params.append(target_type)
        if search:
            query += """
                AND (
                    actor LIKE ?
                    OR action LIKE ?
                    OR target_id LIKE ?
                    OR COALESCE(before_state, '') LIKE ?
                    OR COALESCE(after_state, '') LIKE ?
                )
            """
            needle = f"%{search}%"
            params.extend([needle, needle, needle, needle, needle])
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    def get_all_sessions(
        self,
        status: Optional[str] = None,
        provider: Optional[str] = None,
        workspace_id: Optional[str] = None,
        after: Optional[str] = None,
        limit: int = 50,
    ) -> List[Session]:
        query = "SELECT * FROM sessions WHERE 1 = 1"
        params: list[object] = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if provider:
            query += " AND provider = ?"
            params.append(provider)
        if workspace_id:
            query += " AND workspace_id = ?"
            params.append(workspace_id)
        if after:
            query += " AND session_id > ?"
            params.append(after)
        query += " ORDER BY session_id ASC LIMIT ?"
        params.append(limit)

        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_session(row) for row in rows]

    def delete_session(self, session_id: str):
        with self._get_connection() as conn:
            # First find the internal session_id if client_session_id was provided
            row = conn.execute("SELECT session_id FROM sessions WHERE session_id = ? OR client_session_id = ?", (session_id, session_id)).fetchone()
            if not row:
                return
            internal_id = row["session_id"]
            conn.execute("DELETE FROM turns WHERE session_id = ?", (internal_id,))
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (internal_id,))
            conn.commit()

    def save_workspace(self, workspace: Workspace):
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO workspaces
                (workspace_id, name, path, user_id, template, default_provider, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                workspace.workspace_id,
                workspace.name,
                workspace.path,
                workspace.user_id,
                workspace.template,
                workspace.default_provider.value if workspace.default_provider else None,
                int(workspace.created_at.timestamp() * 1000),
                int(workspace.updated_at.timestamp() * 1000),
            ))
            conn.commit()

    def get_workspace_v2(self, workspace_id: str) -> Optional[Workspace]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM workspaces WHERE workspace_id = ? OR name = ?",
                (workspace_id, workspace_id),
            ).fetchone()
            return self._row_to_workspace(row) if row else None

    def list_workspaces_v2(self, user_id: Optional[str] = None) -> List[Workspace]:
        query = "SELECT * FROM workspaces"
        params = []
        if user_id:
            query += " WHERE user_id = ?"
            params.append(user_id)
        query += " ORDER BY created_at DESC"
        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_workspace(row) for row in rows]

    def delete_workspace_v2(self, workspace_id: str):
        with self._get_connection() as conn:
            conn.execute("DELETE FROM workspaces WHERE workspace_id = ?", (workspace_id,))
            conn.commit()

    def _row_to_workspace(self, row) -> Workspace:
        return Workspace(
            workspace_id=row["workspace_id"],
            name=row["name"],
            path=row["path"],
            user_id=row["user_id"],
            template=row["template"],
            default_provider=ProviderType(row["default_provider"]) if row["default_provider"] else None,
            created_at=self._row_to_datetime(row["created_at"]),
            updated_at=self._row_to_datetime(row["updated_at"]),
        )

    def list_users(self, limit: int = 100, offset: int = 0) -> List[User]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [self._row_to_user(row) for row in rows]

    def get_user(self, user_id: str) -> Optional[User]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            return self._row_to_user(row) if row else None

    def get_user_by_email(self, email: str) -> Optional[User]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            return self._row_to_user(row) if row else None

    def _row_to_user(self, row) -> User:
        return User(
            user_id=row["user_id"],
            email=row["email"],
            display_name=row["display_name"],
            status=UserStatus(row["status"]),
            workspace_path=row["workspace_path"],
            created_at=self._row_to_datetime(row["created_at"]),
            created_by=row["created_by"],
            updated_at=self._row_to_datetime(row["updated_at"]),
            max_api_keys=1,
            max_concurrency=row["max_concurrency"] if "max_concurrency" in row.keys() else 3,
        )

    def save_user(self, user: User):
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO users
                (user_id, email, display_name, status, workspace_path, created_at, created_by, updated_at, max_api_keys, max_concurrency)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    email = excluded.email,
                    display_name = excluded.display_name,
                    status = excluded.status,
                    workspace_path = excluded.workspace_path,
                    created_at = excluded.created_at,
                    created_by = excluded.created_by,
                    updated_at = excluded.updated_at,
                    max_api_keys = excluded.max_api_keys,
                    max_concurrency = excluded.max_concurrency
            """, (
                user.user_id,
                user.email,
                user.display_name,
                user.status.value,
                user.workspace_path,
                int(user.created_at.timestamp() * 1000),
                user.created_by,
                int(user.updated_at.timestamp() * 1000),
                1,
                user.max_concurrency,
            ))
            conn.commit()

    def create_user(
        self,
        email: str,
        display_name: str,
        workspace_path: str,
        created_by: str,
        max_api_keys: int = 1,
        max_concurrency: int = 3,
        user_id: Optional[str] = None,
    ) -> User:
        now = self._now()
        user = User(
            user_id=user_id or self._generate_ulid_like("uag_usr"),
            email=email,
            display_name=display_name,
            workspace_path=workspace_path,
            created_at=now,
            created_by=created_by,
            updated_at=now,
            max_api_keys=1,
            max_concurrency=max(max_concurrency, 1),
        )
        self.save_user(user)
        return user

    def update_user_status(self, user_id: str, status: UserStatus):
        user = self.get_user(user_id)
        if not user:
            return None
        user.status = status
        user.updated_at = self._now()
        self.save_user(user)
        return user

    def list_api_keys(self, user_id: str) -> List[ApiKey]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM api_keys WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
            return [self._row_to_api_key(row) for row in rows]

    def list_active_api_keys(self, user_id: str) -> List[ApiKey]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM api_keys WHERE user_id = ? AND status = 'active' ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
            return [self._row_to_api_key(row) for row in rows]

    def _row_to_api_key(self, row) -> ApiKey:
        return ApiKey(
            key_id=row["key_id"],
            user_id=row["user_id"],
            key_hash=row["key_hash"],
            key_prefix=row["key_prefix"],
            label=row["label"],
            status=row["status"],
            last_used_at=self._row_to_datetime(row["last_used_at"]),
            expires_at=self._row_to_datetime(row["expires_at"]),
            created_at=self._row_to_datetime(row["created_at"]),
            revoked_at=self._row_to_datetime(row["revoked_at"]),
        )

    def get_api_key_by_hash(self, key_hash: str) -> Optional[ApiKey]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM api_keys WHERE key_hash = ?", (key_hash,)).fetchone()
            return self._row_to_api_key(row) if row else None

    def save_api_key(
        self,
        user_id: str,
        raw_key: str,
        label: Optional[str] = None,
        expires_at: Optional[datetime] = None,
    ) -> ApiKey:
        from uuid import uuid4

        created_at = self._now()
        key = ApiKey(
            key_id=self._generate_ulid_like("uag_key"),
            user_id=user_id,
            key_hash=self._hash_api_key(raw_key),
            key_prefix=raw_key[:8],
            label=label,
            created_at=created_at,
            expires_at=expires_at,
        )
        with self._get_connection() as conn:
            revoked_at = int(created_at.timestamp() * 1000)
            conn.execute(
                """
                UPDATE api_keys
                SET status = 'revoked', revoked_at = ?
                WHERE user_id = ? AND status = 'active'
                """,
                (revoked_at, user_id),
            )
            conn.execute("""
                INSERT INTO api_keys
                (key_id, user_id, key_hash, key_prefix, label, status, last_used_at, expires_at, created_at, revoked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                key.key_id,
                key.user_id,
                key.key_hash,
                key.key_prefix,
                key.label,
                key.status,
                int(key.last_used_at.timestamp() * 1000) if key.last_used_at else None,
                int(key.expires_at.timestamp() * 1000) if key.expires_at else None,
                int(key.created_at.timestamp() * 1000),
                int(key.revoked_at.timestamp() * 1000) if key.revoked_at else None,
            ))
            conn.commit()
        return key

    def revoke_api_key(self, key_id: str):
        now = self._now_ms()
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE api_keys SET status = 'revoked', revoked_at = ? WHERE key_id = ?",
                (now, key_id),
            )
            conn.commit()

    def touch_api_key(self, key_id: str):
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE api_keys SET last_used_at = ? WHERE key_id = ?",
                (self._now_ms(), key_id),
            )
            conn.commit()

    def count_user_sessions(self, user_id: str) -> int:
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM sessions
                WHERE user_id = ?
                   OR (user_id IS NULL AND client_session_id LIKE ?)
                """,
                (user_id, f"{user_id}::%"),
            ).fetchone()
            return int(row["count"] if row else 0)

    def count_active_user_sessions(self, user_id: str, exclude_session_id: Optional[str] = None) -> int:
        query = """
            SELECT COUNT(*) AS count
            FROM sessions
            WHERE status = ?
              AND (
                  user_id = ?
                  OR (user_id IS NULL AND client_session_id LIKE ?)
              )
        """
        params: list[object] = ["active", user_id, f"{user_id}::%"]
        if exclude_session_id:
            query += " AND client_session_id != ?"
            params.append(exclude_session_id)
        with self._get_connection() as conn:
            row = conn.execute(query, params).fetchone()
            return int(row["count"] if row else 0)

    def get_user_sessions(self, user_id: str, status: Optional[str] = None) -> List[Session]:
        query = """
            SELECT * FROM sessions
            WHERE (user_id = ?
               OR (user_id IS NULL AND client_session_id LIKE ?))
        """
        params: list[object] = [user_id, f"{user_id}::%"]
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY updated_at DESC"
        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_session(row) for row in rows]

    def get_workspace_sessions(self, workspace_path: str, status: Optional[str] = None) -> List[Session]:
        normalized = os.path.normpath(workspace_path)
        query = """
            SELECT * FROM sessions
            WHERE (cwd_path = ? OR cwd_path LIKE ?)
        """
        params: list[object] = [normalized, f"{normalized}{os.sep}%"]
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY updated_at DESC"
        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_session(row) for row in rows]

    def delete_workspace_sessions(self, workspace_id_or_path: str) -> int:
        with self._get_connection() as conn:
            # Handle both workspace_id and path
            rows = conn.execute(
                """
                SELECT session_id
                FROM sessions
                WHERE workspace_id = ? OR cwd_path = ?
                """,
                (workspace_id_or_path, workspace_id_or_path),
            ).fetchall()
            session_ids = [row["session_id"] for row in rows]
            if not session_ids:
                return 0
            placeholders = ", ".join("?" for _ in session_ids)
            conn.execute(
                f"DELETE FROM turns WHERE session_id IN ({placeholders})",
                session_ids,
            )
            conn.execute(
                f"DELETE FROM sessions WHERE session_id IN ({placeholders})",
                session_ids,
            )
            conn.commit()
            return len(session_ids)

    def get_recent_user_activity(self, user_id: str, limit: int = 20) -> List[dict]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    turns.turn_id,
                    turns.session_id,
                    turns.provider,
                    turns.finish_reason,
                    turns.duration_ms,
                    turns.timestamp,
                    sessions.client_session_id,
                    sessions.cwd_path,
                    sessions.status AS session_status,
                    sessions.workspace_id
                FROM turns
                LEFT JOIN sessions ON sessions.session_id = turns.session_id
                WHERE turns.user_id = ?
                ORDER BY turns.timestamp DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def record_workspace_reset(self, user_id: str, triggered_by: str, actor_id: str, sessions_wiped: int) -> WorkspaceReset:
        reset = WorkspaceReset(
            reset_id=self._generate_ulid_like("uag_rst"),
            user_id=user_id,
            triggered_by=triggered_by,
            actor_id=actor_id,
            sessions_wiped=sessions_wiped,
            reset_at=self._now(),
        )
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO workspace_resets
                (reset_id, user_id, triggered_by, actor_id, sessions_wiped, reset_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                reset.reset_id,
                reset.user_id,
                reset.triggered_by,
                reset.actor_id,
                reset.sessions_wiped,
                int(reset.reset_at.timestamp() * 1000),
            ))
            conn.commit()
        return reset

    def get_workspace_resets(self, user_id: str) -> List[dict]:
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT * FROM workspace_resets WHERE user_id = ? ORDER BY reset_at DESC
            """, (user_id,)).fetchall()
            return [dict(row) for row in rows]

    def get_channel_user_link(self, channel: str, bot_name: str, external_user_id: str) -> Optional[dict]:
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM channel_user_links
                WHERE channel = ? AND bot_name = ? AND external_user_id = ?
                """,
                (channel, bot_name, external_user_id),
            ).fetchone()
            return dict(row) if row else None

    def save_channel_user_link(
        self,
        *,
        channel: str,
        bot_name: str,
        external_user_id: str,
        user_id: str,
        external_chat_id: Optional[str] = None,
        status: str = "active",
    ) -> dict:
        now = self._now_ms()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO channel_user_links
                (channel, bot_name, external_user_id, user_id, external_chat_id, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel, bot_name, external_user_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    external_chat_id = excluded.external_chat_id,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (channel, bot_name, external_user_id, user_id, external_chat_id, status, now, now),
            )
            conn.commit()
        return self.get_channel_user_link(channel, bot_name, external_user_id) or {}

    def get_channel_conversation(self, channel: str, bot_name: str, conversation_key: str) -> Optional[dict]:
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM channel_conversations
                WHERE channel = ? AND bot_name = ? AND conversation_key = ?
                """,
                (channel, bot_name, conversation_key),
            ).fetchone()
            return dict(row) if row else None

    def save_channel_conversation(
        self,
        *,
        channel: str,
        bot_name: str,
        conversation_key: str,
        user_id: str,
        external_chat_id: Optional[str],
        external_thread_id: Optional[str],
        workspace_id: str,
        provider: str,
        session_label: str,
    ) -> dict:
        now = self._now_ms()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO channel_conversations
                (channel, bot_name, conversation_key, user_id, external_chat_id, external_thread_id, workspace_id, provider, session_label, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel, bot_name, conversation_key) DO UPDATE SET
                    user_id = excluded.user_id,
                    external_chat_id = excluded.external_chat_id,
                    external_thread_id = excluded.external_thread_id,
                    workspace_id = excluded.workspace_id,
                    provider = excluded.provider,
                    session_label = excluded.session_label,
                    updated_at = excluded.updated_at
                """,
                (
                    channel,
                    bot_name,
                    conversation_key,
                    user_id,
                    external_chat_id,
                    external_thread_id,
                    workspace_id,
                    provider,
                    session_label,
                    now,
                    now,
                ),
            )
            conn.commit()
        return self.get_channel_conversation(channel, bot_name, conversation_key) or {}

    def create_channel_link_token(
        self,
        *,
        user_id: str,
        channel: str,
        bot_name: str,
        created_by: str,
        expires_in_minutes: int = 30,
    ) -> dict:
        from secrets import token_urlsafe

        raw_token = token_urlsafe(18)
        token_id = self._generate_ulid_like("uag_clt")
        created_at = self._now_ms()
        expires_at = created_at + max(expires_in_minutes, 1) * 60 * 1000
        token_hash = self._hash_channel_token(raw_token)
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO channel_link_tokens
                (token_id, token_hash, user_id, channel, bot_name, created_by, created_at, expires_at, consumed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (token_id, token_hash, user_id, channel, bot_name, created_by, created_at, expires_at),
            )
            conn.commit()
        return {
            "token_id": token_id,
            "raw_token": raw_token,
            "user_id": user_id,
            "channel": channel,
            "bot_name": bot_name,
            "created_by": created_by,
            "created_at": self._row_to_datetime(created_at).isoformat(),
            "expires_at": self._row_to_datetime(expires_at).isoformat(),
        }

    def consume_channel_link_token(self, raw_token: str, channel: str, bot_name: str) -> Optional[dict]:
        token_hash = self._hash_channel_token(raw_token)
        now = self._now_ms()
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM channel_link_tokens
                WHERE token_hash = ? AND channel = ? AND bot_name = ?
                """,
                (token_hash, channel, bot_name),
            ).fetchone()
            if not row:
                return None
            if row["consumed_at"] is not None or row["expires_at"] < now:
                return None
            conn.execute(
                "UPDATE channel_link_tokens SET consumed_at = ? WHERE token_id = ?",
                (now, row["token_id"]),
            )
            conn.commit()
            return dict(row)

    def get_channel_runtime_state(self, channel: str, bot_name: str, state_key: str) -> Optional[dict]:
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM channel_runtime_state
                WHERE channel = ? AND bot_name = ? AND state_key = ?
                """,
                (channel, bot_name, state_key),
            ).fetchone()
            return dict(row) if row else None

    def save_channel_runtime_state(self, *, channel: str, bot_name: str, state_key: str, state_value: Optional[str]) -> dict:
        now = self._now_ms()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO channel_runtime_state
                (channel, bot_name, state_key, state_value, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(channel, bot_name, state_key) DO UPDATE SET
                    state_value = excluded.state_value,
                    updated_at = excluded.updated_at
                """,
                (channel, bot_name, state_key, state_value, now),
            )
            conn.commit()
        return self.get_channel_runtime_state(channel, bot_name, state_key) or {}

    def get_channel_polling_offset(self, channel: str, bot_name: str) -> int:
        row = self.get_channel_runtime_state(channel, bot_name, "polling_offset")
        if not row or row.get("state_value") in (None, ""):
            return 0
        try:
            return int(str(row["state_value"]))
        except (TypeError, ValueError):
            return 0

    def save_channel_polling_offset(self, channel: str, bot_name: str, offset: int) -> int:
        self.save_channel_runtime_state(
            channel=channel,
            bot_name=bot_name,
            state_key="polling_offset",
            state_value=str(int(offset)),
        )
        return int(offset)

    def count_sessions(
        self,
        status: Optional[str] = None,
        provider: Optional[str] = None,
        workspace_prefix: Optional[str] = None,
    ) -> int:
        query = "SELECT COUNT(*) AS count FROM sessions WHERE 1 = 1"
        params: list[object] = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if provider:
            query += " AND provider = ?"
            params.append(provider)
        if workspace_prefix:
            query += " AND cwd_path LIKE ?"
            params.append(f"{workspace_prefix}%")
        with self._get_connection() as conn:
            row = conn.execute(query, params).fetchone()
            return int(row["count"] if row else 0)

    def save_session(self, session: Session):
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO sessions 
                (session_id, workspace_id, client_session_id, backend_id, provider, user_id, api_key_id, cwd_path, status, created_at, updated_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session.session_id,
                session.workspace_id,
                session.client_session_id,
                session.backend_id,
                session.provider.value,
                session.user_id,
                session.api_key_id,
                session.cwd_path,
                session.status.value,
                int(session.created_at.timestamp() * 1000),
                int(session.updated_at.timestamp() * 1000),
                int(session.expires_at.timestamp() * 1000),
            ))
            conn.commit()

    def get_session(self, session_id: str) -> Optional[Session]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE session_id = ? OR client_session_id = ?", (session_id, session_id)).fetchone()
            if row:
                return self._row_to_session(row)
        return None

    def _row_to_session(self, row) -> Session:
        return Session(
            session_id=row["session_id"],
            workspace_id=row["workspace_id"],
            client_session_id=row["client_session_id"],
            backend_id=row["backend_id"],
            provider=ProviderType(row["provider"]),
            user_id=row["user_id"],
            api_key_id=row["api_key_id"],
            cwd_path=row["cwd_path"],
            status=SessionStatus(row["status"]),
            created_at=self._row_to_datetime(row["created_at"]),
            updated_at=self._row_to_datetime(row["updated_at"]),
            expires_at=self._row_to_datetime(row["expires_at"])
        )

    def delete_expired_sessions(self):
        now = int(datetime.now().timestamp())
        with self._get_connection() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            conn.commit()
    def list_runtime_logs(self, limit: int = 100, after: Optional[str] = None, level: Optional[str] = None, search: Optional[str] = None, since: Optional[int] = None, until: Optional[int] = None) -> List[dict]:
        return self._runtime_log_store.list_logs(limit=limit, after=after, level=level, search=search, since=since, until=until)


    def list_traces(self, limit: int = 50, after: Optional[str] = None, trace_id: Optional[str] = None, since: Optional[int] = None, until: Optional[int] = None, search: Optional[str] = None) -> List[dict]:
        if self._trace_store:
            return self._trace_store.list_traces(limit=limit, after=None, component=None, request_id=None, status=None, trace_id=trace_id, search=search, since=since, until=until)
        return []

    def get_trace_events(self, trace_id: str) -> List[dict]:
        if self._trace_store:
            return self._trace_store.get_trace_events(trace_id)
        return []

    def get_trace(self, trace_id: str) -> Optional[dict]:
        if self._trace_store:
            root = self._trace_store._find_trace_root(trace_id)
            if not root:
                return None
            events = self._trace_store.get_trace_events(trace_id)
            root["events"] = events
            # For backward compatibility with UI
            root["spans"] = [e for e in events if e.get("kind", "").startswith("span")]
            return root
        return None

    def save_trace(self, trace_id: str, request_id: Optional[str], name: str, component: Optional[str], status: str, created_at: int):
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO traces (trace_id, request_id, name, component, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (trace_id, request_id, name, component, status, created_at),
            )
            conn.commit()

    def save_span(self, span_id: str, trace_id: str, parent_span_id: Optional[str], name: str, component: Optional[str], status: str, start_time: int, end_time: Optional[int], duration_ms: Optional[float]):
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO spans (span_id, trace_id, parent_span_id, name, component, status, start_time, end_time, duration_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (span_id, trace_id, parent_span_id, name, component, status, start_time, end_time, duration_ms),
            )
            conn.commit()

    def save_event(self, event_id: str, trace_id: str, span_id: Optional[str], name: str, component: Optional[str], level: str, message: Optional[str], attributes: Optional[str], timestamp: int):
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO events (event_id, trace_id, span_id, name, component, level, message, attributes, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (event_id, trace_id, span_id, name, component, level, message, attributes, timestamp),
            )
            conn.commit()

    def save_runtime_log(self, log_id: str, timestamp: int, level: str, logger: str, message: str, request_id: Optional[str], trace_id: Optional[str], span_id: Optional[str], attributes: Optional[str]):
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO runtime_logs (log_id, timestamp, level, logger, message, request_id, trace_id, span_id, attributes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (log_id, timestamp, level, logger, message, request_id, trace_id, span_id, attributes),
            )
            conn.commit()
