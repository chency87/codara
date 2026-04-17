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
from codara.config import get_settings
from codara.trace_store import FileTraceStore

from codara.core.models import (
    Session,
    Account,
    ProviderType,
    SessionStatus,
    AuthType,
    is_account_enabled_status,
    User,
    ApiKey,
    UserStatus,
    UserUsage,
    WorkspaceReset,
)

class DatabaseManager:
    def __init__(self, db_path: str = "codara.db"):
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
        self._trace_queue: queue.Queue = queue.Queue(maxsize=10000)
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
                # Avoid crashing the worker thread; in a real app we would log this
                time.sleep(1)

    def _persist_trace_batch(self, batch: List[tuple]):
        try:
            if self._trace_store is not None:
                rows = [
                    {
                        "event_id": event_id,
                        "trace_id": trace_id,
                        "span_id": span_id,
                        "parent_span_id": parent_span_id,
                        "kind": kind,
                        "name": name,
                        "component": component,
                        "level": level,
                        "status": status,
                        "request_id": request_id,
                        "started_at": started_at,
                        "ended_at": ended_at,
                        "duration_ms": duration_ms,
                        "attributes": json.loads(attributes) if attributes else None,
                    }
                    for (
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
                        started_at,
                        ended_at,
                        duration_ms,
                        attributes,
                    ) in batch
                ]
                self._trace_store.append_batch(rows)
                return
            with self._get_connection() as conn:
                conn.executemany(
                    """
                    INSERT INTO trace_events
                    (event_id, trace_id, span_id, parent_span_id, kind, name, component, level, status, request_id, started_at, ended_at, duration_ms, attributes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    batch
                )
                conn.commit()
        except Exception:
            # If batch persist fails, we log it (if we had a logger here) and continue
            pass

    def wait_for_traces(self):
        """Wait for all currently queued trace events to be persisted. Used primarily for testing."""
        self._trace_queue.join()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _initialize_db(self):
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    account_id           TEXT        PRIMARY KEY,
                    credential_id        TEXT,
                    inventory_source     TEXT        NOT NULL DEFAULT 'vault',
                    provider             TEXT        NOT NULL,
                    auth_type            TEXT        NOT NULL,
                    label                TEXT        NOT NULL,
                    encrypted_credential TEXT,
                    status               TEXT        NOT NULL DEFAULT 'active',
                    auth_index           TEXT,
                    cooldown_until       INTEGER,
                    last_seen_at         INTEGER,
                    last_used_at         INTEGER,
                    cli_primary          INTEGER     NOT NULL DEFAULT 0,
                    usage_tpm            INTEGER     NOT NULL DEFAULT 0,
                    usage_rpd            INTEGER     NOT NULL DEFAULT 0,
                    usage_hourly         INTEGER     NOT NULL DEFAULT 0,
                    usage_weekly         INTEGER     NOT NULL DEFAULT 0,
                    tpm_limit            INTEGER     NOT NULL DEFAULT 100000,
                    rpd_limit            INTEGER     NOT NULL DEFAULT 5000,
                    hourly_limit         INTEGER     NOT NULL DEFAULT 50000,
                    weekly_limit         INTEGER     NOT NULL DEFAULT 1000000,
                    remaining_compute_hours FLOAT    NOT NULL DEFAULT 0.0,
                    hourly_used_pct      REAL,
                    weekly_used_pct      REAL,
                    hourly_reset_after_seconds INTEGER,
                    weekly_reset_after_seconds INTEGER,
                    hourly_reset_at      INTEGER,
                    weekly_reset_at      INTEGER,
                    access_token_expires_at INTEGER,
                    usage_source         TEXT,
                    plan_type            TEXT,
                    rate_limit_allowed   INTEGER,
                    rate_limit_reached   INTEGER,
                    credits_has_credits  INTEGER,
                    credits_unlimited    INTEGER,
                    credits_overage_limit_reached INTEGER,
                    approx_local_messages_min INTEGER,
                    approx_local_messages_max INTEGER,
                    approx_cloud_messages_min INTEGER,
                    approx_cloud_messages_max INTEGER
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    client_session_id  TEXT        PRIMARY KEY,
                    backend_id         TEXT        NOT NULL,
                    provider           TEXT        NOT NULL,
                    account_id         TEXT        NOT NULL REFERENCES accounts(account_id),
                    user_id            TEXT        REFERENCES users(user_id),
                    api_key_id         TEXT        REFERENCES api_keys(key_id),
                    cwd_path           TEXT        NOT NULL,
                    prefix_hash        TEXT        NOT NULL,
                    status             TEXT        NOT NULL DEFAULT 'idle',
                    fence_token        INTEGER     NOT NULL DEFAULT 0,
                    last_context_tokens INTEGER    NOT NULL DEFAULT 0,
                    created_at         INTEGER     NOT NULL,
                    updated_at         INTEGER     NOT NULL,
                    expires_at         INTEGER     NOT NULL
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
                    client_session_id TEXT        NOT NULL REFERENCES sessions(client_session_id),
                    user_id          TEXT,
                    provider         TEXT        NOT NULL,
                    account_id       TEXT        NOT NULL,
                    input_tokens     INTEGER     NOT NULL DEFAULT 0,
                    output_tokens    INTEGER     NOT NULL DEFAULT 0,
                    finish_reason    TEXT,
                    duration_ms      INTEGER     NOT NULL DEFAULT 0,
                    diff             TEXT,
                    actions          TEXT,
                    timestamp        INTEGER     NOT NULL
                )
            """)
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
                CREATE TABLE IF NOT EXISTS user_usage (
                    user_id         TEXT        NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    period          TEXT        NOT NULL,
                    provider        TEXT        NOT NULL,
                    input_tokens    INTEGER     NOT NULL DEFAULT 0,
                    output_tokens   INTEGER     NOT NULL DEFAULT 0,
                    cache_hit_tokens INTEGER    NOT NULL DEFAULT 0,
                    request_count   INTEGER     NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, period, provider)
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
            self._ensure_column(conn, "turns", "user_id", "TEXT")
            self._ensure_column(conn, "sessions", "user_id", "TEXT")
            self._ensure_column(conn, "sessions", "api_key_id", "TEXT")
            self._ensure_column(conn, "users", "max_concurrency", "INTEGER NOT NULL DEFAULT 3")
            conn.execute("UPDATE users SET max_api_keys = 1 WHERE COALESCE(max_api_keys, 1) != 1")
            # Indices
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_account ON sessions(account_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_cwd     ON sessions(cwd_path)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user    ON sessions(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_api_key ON sessions(api_key_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_turns_session   ON turns(client_session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trace_events_trace ON trace_events(trace_id, started_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trace_events_request ON trace_events(request_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trace_events_component ON trace_events(component, started_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trace_events_started ON trace_events(started_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_email      ON users(email)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_user     ON api_keys(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_hash     ON api_keys(key_hash)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_user_usage_user   ON user_usage(user_id, period)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_resets_user       ON workspace_resets(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_channel_links_user ON channel_user_links(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_channel_conversations_user ON channel_conversations(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_channel_tokens_user ON channel_link_tokens(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_channel_tokens_channel ON channel_link_tokens(channel)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_channel_runtime_state_channel ON channel_runtime_state(channel, bot_name)")
            self._ensure_column(conn, "channel_user_links", "bot_name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "channel_conversations", "bot_name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "channel_link_tokens", "bot_name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "accounts", "cli_primary", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "accounts", "credential_id", "TEXT")
            self._ensure_column(conn, "accounts", "inventory_source", "TEXT NOT NULL DEFAULT 'vault'")
            self._ensure_column(conn, "accounts", "auth_index", "TEXT")
            self._ensure_column(conn, "accounts", "last_seen_at", "INTEGER")
            self._ensure_column(conn, "accounts", "last_used_at", "INTEGER")
            self._ensure_column(conn, "accounts", "hourly_used_pct", "REAL")
            self._ensure_column(conn, "accounts", "weekly_used_pct", "REAL")
            self._ensure_column(conn, "accounts", "hourly_reset_after_seconds", "INTEGER")
            self._ensure_column(conn, "accounts", "weekly_reset_after_seconds", "INTEGER")
            self._ensure_column(conn, "accounts", "hourly_reset_at", "INTEGER")
            self._ensure_column(conn, "accounts", "weekly_reset_at", "INTEGER")
            self._ensure_column(conn, "accounts", "access_token_expires_at", "INTEGER")
            self._ensure_column(conn, "accounts", "usage_source", "TEXT")
            self._ensure_column(conn, "accounts", "plan_type", "TEXT")
            self._ensure_column(conn, "accounts", "rate_limit_allowed", "INTEGER")
            self._ensure_column(conn, "accounts", "rate_limit_reached", "INTEGER")
            self._ensure_column(conn, "accounts", "credits_has_credits", "INTEGER")
            self._ensure_column(conn, "accounts", "credits_unlimited", "INTEGER")
            self._ensure_column(conn, "accounts", "credits_overage_limit_reached", "INTEGER")
            self._ensure_column(conn, "accounts", "approx_local_messages_min", "INTEGER")
            self._ensure_column(conn, "accounts", "approx_local_messages_max", "INTEGER")
            self._ensure_column(conn, "accounts", "approx_cloud_messages_min", "INTEGER")
            self._ensure_column(conn, "accounts", "approx_cloud_messages_max", "INTEGER")
            self._mark_legacy_system_accounts(conn)
            self._backfill_session_user_bindings(conn)
            self._seed_system_accounts(conn)
            conn.commit()

    def _seed_system_accounts(self, conn):
        system_accounts = [
            ("gemini-system", "gemini", "SYSTEM", "Gemini (System)", "system"),
            ("opencode-system", "opencode", "SYSTEM", "OpenCode (System)", "system"),
        ]
        conn.executemany(
            """
            INSERT INTO accounts (account_id, provider, auth_type, label, inventory_source, status)
            VALUES (?, ?, ?, ?, ?, 'active')
            ON CONFLICT(account_id) DO NOTHING
            """,
            system_accounts
        )

    def _ensure_column(self, conn, table: str, column: str, column_type: str):
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")

    def _mark_legacy_system_accounts(self, conn):
        conn.execute(
            """
            UPDATE accounts
            SET inventory_source = 'system'
            WHERE account_id IN ('codex-oauth', 'gemini-oauth', 'opencode-oauth')
              AND label IN ('Codex OAuth Account', 'Gemini OAuth Account', 'Opencode OAuth Account')
            """
        )

    def _backfill_session_user_bindings(self, conn):
        user_ids = {
            row["user_id"]
            for row in conn.execute("SELECT user_id FROM users").fetchall()
        }
        if not user_ids:
            return
        rows = conn.execute(
            """
            SELECT client_session_id
            FROM sessions
            WHERE user_id IS NULL
              AND client_session_id LIKE '%::%'
            """
        ).fetchall()
        for row in rows:
            session_id = row["client_session_id"]
            user_id = session_id.split("::", 1)[0]
            if user_id in user_ids:
                conn.execute(
                    "UPDATE sessions SET user_id = ? WHERE client_session_id = ?",
                    (user_id, session_id),
                )

    def _account_inventory_filter(self, include_system: bool = False) -> str:
        if include_system:
            return ""
        return " AND COALESCE(inventory_source, 'vault') != 'system'"

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

    def record_turn(self, turn_id: str, session_id: str, provider: str, account_id: str,
                    input_tokens: int, output_tokens: int, finish_reason: str,
                    duration_ms: int, diff: Optional[str], actions: Optional[List[dict]],
                    user_id: Optional[str] = None):
        now = int(self._now().timestamp())
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO turns 
                (turn_id, client_session_id, user_id, provider, account_id, input_tokens, output_tokens, finish_reason, duration_ms, diff, actions, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                turn_id, session_id, user_id, provider, account_id, input_tokens, output_tokens,
                finish_reason, duration_ms, diff, 
                json.dumps(actions) if actions else None,
                now
            ))
            conn.commit()

    def get_session_turns(self, session_id: str) -> List[dict]:
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT * FROM turns WHERE client_session_id = ? ORDER BY timestamp ASC
            """, (session_id,)).fetchall()
            return [dict(row) for row in rows]

    def get_provider_stats(self) -> List[dict]:
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT provider, 
                       COUNT(DISTINCT client_session_id) as active_sessions,
                       SUM(input_tokens + output_tokens) as total_tokens
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
    ) -> str:
        event_id = self._generate_ulid_like("trc_evt")
        try:
            self._trace_queue.put_nowait((
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
            ))
        except queue.Full:
            # Drop the event if the queue is overloaded to avoid impacting main thread.
            pass
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

    def get_all_accounts(
        self,
        provider: Optional[str] = None,
        status: Optional[str] = None,
        after: Optional[str] = None,
        limit: int = 50,
        include_system: bool = False,
    ) -> List[Account]:
        query = """
            SELECT * FROM accounts
            WHERE encrypted_credential IS NOT NULL
              AND provider = 'codex'
        """
        query += self._account_inventory_filter(include_system)
        params: list[object] = []
        if provider and provider.lower() != 'codex':
            return [] # Return empty if a non-codex provider is specifically requested
        if provider:
            query += " AND provider = ?"
            params.append(provider)
        if status:
            query += " AND status = ?"
            params.append(status)
        if after:
            query += " AND account_id > ?"
            params.append(after)
        query += " ORDER BY account_id ASC LIMIT ?"
        params.append(limit)

        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_account(row) for row in rows]

    def get_all_sessions(
        self,
        status: Optional[str] = None,
        provider: Optional[str] = None,
        workspace_prefix: Optional[str] = None,
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
        if workspace_prefix:
            query += " AND cwd_path LIKE ?"
            params.append(f"{workspace_prefix}%")
        if after:
            query += " AND client_session_id > ?"
            params.append(after)
        query += " ORDER BY client_session_id ASC LIMIT ?"
        params.append(limit)

        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_session(row) for row in rows]

    def delete_account(self, account_id: str):
        with self._get_connection() as conn:
            conn.execute("DELETE FROM turns WHERE account_id = ?", (account_id,))
            conn.execute("DELETE FROM sessions WHERE account_id = ?", (account_id,))
            conn.execute("DELETE FROM accounts WHERE account_id = ?", (account_id,))
            conn.commit()

    def delete_session(self, session_id: str):
        with self._get_connection() as conn:
            conn.execute("DELETE FROM turns WHERE client_session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE client_session_id = ?", (session_id,))
            conn.commit()

    def save_account(self, account: Account):
        credential_id = account.credential_id or account.account_id
        columns = [
            "account_id",
            "credential_id",
            "inventory_source",
            "provider",
            "auth_type",
            "label",
            "encrypted_credential",
            "status",
            "auth_index",
            "cooldown_until",
            "last_seen_at",
            "last_used_at",
            "cli_primary",
            "usage_tpm",
            "usage_rpd",
            "usage_hourly",
            "usage_weekly",
            "tpm_limit",
            "rpd_limit",
            "hourly_limit",
            "weekly_limit",
            "remaining_compute_hours",
            "hourly_used_pct",
            "weekly_used_pct",
            "hourly_reset_after_seconds",
            "weekly_reset_after_seconds",
            "hourly_reset_at",
            "weekly_reset_at",
            "access_token_expires_at",
            "usage_source",
            "plan_type",
            "rate_limit_allowed",
            "rate_limit_reached",
            "credits_has_credits",
            "credits_unlimited",
            "credits_overage_limit_reached",
            "approx_local_messages_min",
            "approx_local_messages_max",
            "approx_cloud_messages_min",
            "approx_cloud_messages_max",
        ]
        values = (
            account.account_id,
            credential_id,
            account.inventory_source,
            account.provider.value,
            account.auth_type.value,
            account.label,
            account.encrypted_credential,
            account.status,
            account.auth_index,
            int(account.cooldown_until.timestamp()) if account.cooldown_until else None,
            int(account.last_seen_at.timestamp() * 1000) if account.last_seen_at else None,
            int(account.last_used_at.timestamp() * 1000) if account.last_used_at else None,
            1 if account.cli_primary else 0,
            account.usage_tpm,
            account.usage_rpd,
            account.usage_hourly,
            account.usage_weekly,
            account.tpm_limit,
            account.rpd_limit,
            account.hourly_limit,
            account.weekly_limit,
            account.remaining_compute_hours,
            account.hourly_used_pct,
            account.weekly_used_pct,
            account.hourly_reset_after_seconds,
            account.weekly_reset_after_seconds,
            int(account.hourly_reset_at.timestamp()) if account.hourly_reset_at else None,
            int(account.weekly_reset_at.timestamp()) if account.weekly_reset_at else None,
            int(account.access_token_expires_at.timestamp()) if account.access_token_expires_at else None,
            account.usage_source,
            account.plan_type,
            int(account.rate_limit_allowed) if account.rate_limit_allowed is not None else None,
            int(account.rate_limit_reached) if account.rate_limit_reached is not None else None,
            int(account.credits_has_credits) if account.credits_has_credits is not None else None,
            int(account.credits_unlimited) if account.credits_unlimited is not None else None,
            int(account.credits_overage_limit_reached) if account.credits_overage_limit_reached is not None else None,
            account.approx_local_messages_min,
            account.approx_local_messages_max,
            account.approx_cloud_messages_min,
            account.approx_cloud_messages_max,
        )
        with self._get_connection() as conn:
            placeholders = ", ".join("?" for _ in columns)
            conn.execute(
                f"""
                INSERT OR REPLACE INTO accounts
                ({", ".join(columns)})
                VALUES ({placeholders})
                """,
                values,
            )
            conn.commit()

    def get_account(self, account_id: str) -> Optional[Account]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE account_id = ?", (account_id,)).fetchone()
            if row:
                return self._row_to_account(row)
        return None

    def get_account_by_credential_id(self, credential_id: str) -> Optional[Account]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE credential_id = ?", (credential_id,)).fetchone()
            if row:
                return self._row_to_account(row)
        return None

    def get_account_by_auth_index(self, auth_index: str) -> Optional[Account]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE auth_index = ?", (auth_index,)).fetchone()
            if row:
                return self._row_to_account(row)
        return None

    def _row_to_account(self, row) -> Account:
        return Account(
            account_id=row["account_id"],
            credential_id=row["credential_id"] or row["account_id"],
            inventory_source=row["inventory_source"] or "vault",
            provider=ProviderType(row["provider"]),
            auth_type=AuthType(row["auth_type"]),
            label=row["label"],
            encrypted_credential=row["encrypted_credential"],
            status=row["status"],
            auth_index=row["auth_index"],
            cooldown_until=self._row_to_datetime(row["cooldown_until"]),
            last_seen_at=self._row_to_datetime(row["last_seen_at"]),
            last_used_at=self._row_to_datetime(row["last_used_at"]),
            cli_primary=bool(row["cli_primary"]),
            usage_tpm=row["usage_tpm"],
            usage_rpd=row["usage_rpd"],
            usage_hourly=row["usage_hourly"],
            usage_weekly=row["usage_weekly"],
            tpm_limit=row["tpm_limit"],
            rpd_limit=row["rpd_limit"],
            hourly_limit=row["hourly_limit"],
            weekly_limit=row["weekly_limit"],
            remaining_compute_hours=row["remaining_compute_hours"],
            hourly_used_pct=row["hourly_used_pct"],
            weekly_used_pct=row["weekly_used_pct"],
            hourly_reset_after_seconds=row["hourly_reset_after_seconds"],
            weekly_reset_after_seconds=row["weekly_reset_after_seconds"],
            hourly_reset_at=self._row_to_datetime(row["hourly_reset_at"]),
            weekly_reset_at=self._row_to_datetime(row["weekly_reset_at"]),
            access_token_expires_at=self._row_to_datetime(row["access_token_expires_at"]),
            usage_source=row["usage_source"],
            plan_type=row["plan_type"],
            rate_limit_allowed=bool(row["rate_limit_allowed"]) if row["rate_limit_allowed"] is not None else None,
            rate_limit_reached=bool(row["rate_limit_reached"]) if row["rate_limit_reached"] is not None else None,
            credits_has_credits=bool(row["credits_has_credits"]) if row["credits_has_credits"] is not None else None,
            credits_unlimited=bool(row["credits_unlimited"]) if row["credits_unlimited"] is not None else None,
            credits_overage_limit_reached=bool(row["credits_overage_limit_reached"]) if row["credits_overage_limit_reached"] is not None else None,
            approx_local_messages_min=row["approx_local_messages_min"],
            approx_local_messages_max=row["approx_local_messages_max"],
            approx_cloud_messages_min=row["approx_cloud_messages_min"],
            approx_cloud_messages_max=row["approx_cloud_messages_max"],
        )

    def set_account_status(self, account_id: str, status: str, cooldown_until: Optional[datetime] = None):
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE accounts SET status = ?, cooldown_until = ? WHERE account_id = ?",
                (status, int(cooldown_until.timestamp()) if cooldown_until else None, account_id),
            )
            conn.commit()

    def touch_account_seen(self, account_id: str):
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE accounts SET last_seen_at = ? WHERE account_id = ?",
                (self._now_ms(), account_id),
            )
            conn.commit()

    def touch_account_used(self, account_id: str):
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE accounts SET last_used_at = ? WHERE account_id = ?",
                (self._now_ms(), account_id),
            )
            conn.commit()

    def set_cli_primary_account(self, account_id: str) -> Optional[Account]:
        account = self.get_account(account_id)
        if not account:
            return None
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE accounts SET cli_primary = CASE WHEN account_id = ? THEN 1 ELSE 0 END WHERE provider = ?",
                (account_id, account.provider.value),
            )
            conn.commit()
        return self.get_account(account_id)

    def get_cli_primary_account(self, provider: ProviderType) -> Optional[Account]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM accounts WHERE provider = ? AND cli_primary = 1 AND COALESCE(inventory_source, 'vault') != 'system' LIMIT 1",
                (provider.value,),
            ).fetchone()
            return self._row_to_account(row) if row else None

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

    def delete_workspace_sessions(self, workspace_path: str) -> int:
        normalized = os.path.normpath(workspace_path)
        params = (normalized, f"{normalized}{os.sep}%")
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT client_session_id
                FROM sessions
                WHERE cwd_path = ? OR cwd_path LIKE ?
                """,
                params,
            ).fetchall()
            session_ids = [row["client_session_id"] for row in rows]
            if not session_ids:
                return 0
            placeholders = ", ".join("?" for _ in session_ids)
            conn.execute(
                f"DELETE FROM turns WHERE client_session_id IN ({placeholders})",
                session_ids,
            )
            conn.execute(
                f"DELETE FROM sessions WHERE client_session_id IN ({placeholders})",
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
                    turns.client_session_id,
                    turns.provider,
                    turns.account_id,
                    turns.input_tokens,
                    turns.output_tokens,
                    turns.finish_reason,
                    turns.duration_ms,
                    turns.timestamp,
                    sessions.cwd_path,
                    sessions.status AS session_status,
                    sessions.api_key_id,
                    api_keys.label AS api_key_label,
                    api_keys.key_prefix
                FROM turns
                LEFT JOIN sessions ON sessions.client_session_id = turns.client_session_id
                LEFT JOIN api_keys ON api_keys.key_id = sessions.api_key_id
                WHERE turns.user_id = ?
                ORDER BY turns.timestamp DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def record_user_usage(
        self,
        user_id: str,
        provider: ProviderType,
        input_tokens: int,
        output_tokens: int,
        cache_hit_tokens: int = 0,
        request_count: int = 1,
        period: Optional[str] = None,
    ):
        period = period or self._now().date().isoformat()
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO user_usage
                (user_id, period, provider, input_tokens, output_tokens, cache_hit_tokens, request_count)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, period, provider) DO UPDATE SET
                    input_tokens = input_tokens + excluded.input_tokens,
                    output_tokens = output_tokens + excluded.output_tokens,
                    cache_hit_tokens = cache_hit_tokens + excluded.cache_hit_tokens,
                    request_count = request_count + excluded.request_count
            """, (
                user_id,
                period,
                provider.value,
                input_tokens,
                output_tokens,
                cache_hit_tokens,
                request_count,
            ))
            conn.commit()

    def get_user_usage(
        self,
        user_id: str,
        start_period: Optional[str] = None,
        end_period: Optional[str] = None,
    ) -> List[dict]:
        query = "SELECT * FROM user_usage WHERE user_id = ?"
        params: list[object] = [user_id]
        if start_period:
            query += " AND period >= ?"
            params.append(start_period)
        if end_period:
            query += " AND period <= ?"
            params.append(end_period)
        query += " ORDER BY period ASC, provider ASC"
        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    def get_usage_timeseries(self, days: int = 30) -> List[dict]:
        cutoff = (self._now().date() - timedelta(days=max(days - 1, 0))).isoformat()
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    period,
                    COALESCE(SUM(input_tokens), 0) AS input_tokens,
                    COALESCE(SUM(output_tokens), 0) AS output_tokens,
                    COALESCE(SUM(cache_hit_tokens), 0) AS cache_hit_tokens,
                    COALESCE(SUM(request_count), 0) AS request_count
                FROM user_usage
                WHERE period >= ?
                GROUP BY period
                ORDER BY period ASC
                """,
                (cutoff,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_usage_summary(self) -> dict:
        with self._get_connection() as conn:
            account_rows = conn.execute(
                """
                SELECT * FROM accounts
                WHERE encrypted_credential IS NOT NULL
                  AND provider = 'codex'
                  AND COALESCE(inventory_source, 'vault') != 'system'
                ORDER BY account_id ASC
                """
            ).fetchall()
            provider_summary = []
            provider_totals: dict[str, dict[str, int]] = {}
            for row in account_rows:
                usage_observed = any(
                    row[key] is not None
                    for key in (
                        "last_seen_at",
                        "usage_source",
                        "hourly_used_pct",
                        "weekly_used_pct",
                        "hourly_reset_at",
                        "weekly_reset_at",
                        "rate_limit_allowed",
                        "rate_limit_reached",
                        "credits_has_credits",
                        "credits_unlimited",
                        "credits_overage_limit_reached",
                    )
                )
                usage_hourly = row["usage_hourly"] or 0
                usage_weekly = row["usage_weekly"] or 0
                hourly_limit = row["hourly_limit"] if usage_observed else None
                weekly_limit = row["weekly_limit"] if usage_observed else None
                hourly_left = max(hourly_limit - usage_hourly, 0) if hourly_limit is not None else None
                weekly_left = max(weekly_limit - usage_weekly, 0) if weekly_limit is not None else None
                provider_summary.append({
                    "provider": row["provider"],
                    "account_id": row["account_id"],
                    "credential_id": row["credential_id"] or row["account_id"],
                    "label": row["label"],
                    "cli_name": row["provider"],
                    "auth_type": row["auth_type"],
                    "allocation": "cli-primary" if row["cli_primary"] else "pool",
                    "cli_primary": bool(row["cli_primary"]),
                    "usage_tpm": row["usage_tpm"],
                    "usage_rpd": row["usage_rpd"],
                    "usage_hourly": usage_hourly,
                    "usage_weekly": usage_weekly,
                    "hourly_limit": hourly_limit,
                    "weekly_limit": weekly_limit,
                    "hourly_left": hourly_left,
                    "weekly_left": weekly_left,
                    "hourly_left_pct": round((hourly_left / hourly_limit) * 100, 2) if hourly_left is not None and hourly_limit else None,
                    "weekly_left_pct": round((weekly_left / weekly_limit) * 100, 2) if weekly_left is not None and weekly_limit else None,
                    "hourly_used_pct": row["hourly_used_pct"],
                    "weekly_used_pct": row["weekly_used_pct"],
                    "hourly_reset_after_seconds": row["hourly_reset_after_seconds"],
                    "weekly_reset_after_seconds": row["weekly_reset_after_seconds"],
                    "hourly_reset_at": self._row_to_datetime(row["hourly_reset_at"]).isoformat() if row["hourly_reset_at"] else None,
                    "weekly_reset_at": self._row_to_datetime(row["weekly_reset_at"]).isoformat() if row["weekly_reset_at"] else None,
                    "access_token_expires_at": self._row_to_datetime(row["access_token_expires_at"]).isoformat() if row["access_token_expires_at"] else None,
                    "usage_source": row["usage_source"],
                    "plan_type": row["plan_type"],
                    "rate_limit_allowed": bool(row["rate_limit_allowed"]) if row["rate_limit_allowed"] is not None else None,
                    "rate_limit_reached": bool(row["rate_limit_reached"]) if row["rate_limit_reached"] is not None else None,
                    "credits_has_credits": bool(row["credits_has_credits"]) if row["credits_has_credits"] is not None else None,
                    "credits_unlimited": bool(row["credits_unlimited"]) if row["credits_unlimited"] is not None else None,
                    "credits_overage_limit_reached": bool(row["credits_overage_limit_reached"]) if row["credits_overage_limit_reached"] is not None else None,
                    "approx_local_messages_min": row["approx_local_messages_min"],
                    "approx_local_messages_max": row["approx_local_messages_max"],
                    "approx_cloud_messages_min": row["approx_cloud_messages_min"],
                    "approx_cloud_messages_max": row["approx_cloud_messages_max"],
                    "compute_hours_left": row["remaining_compute_hours"],
                    "remaining_compute_hours": row["remaining_compute_hours"],
                    "compute_hours_pct": round((row["remaining_compute_hours"] / 5.0) * 100, 2) if row["remaining_compute_hours"] is not None else None,
                    "remaining_compute_hours_pct": round((row["remaining_compute_hours"] / 5.0) * 100, 2) if row["remaining_compute_hours"] is not None else None,
                    "status": row["status"],
                    "auth_index": row["auth_index"],
                    "cooldown_until": self._row_to_datetime(row["cooldown_until"]).isoformat() if row["cooldown_until"] else None,
                    "last_seen_at": self._row_to_datetime(row["last_seen_at"]).isoformat() if row["last_seen_at"] else None,
                    "last_used_at": self._row_to_datetime(row["last_used_at"]).isoformat() if row["last_used_at"] else None,
                    "is_enabled": is_account_enabled_status(row["status"]),
                    "usage_observed": usage_observed,
                })
                provider_bucket = provider_totals.setdefault(
                    row["provider"],
                    {"active_sessions": 0, "total_tokens": 0, "accounts": 0},
                )
                provider_bucket["accounts"] += 1
                provider_bucket["active_sessions"] += conn.execute(
                    "SELECT COUNT(*) AS count FROM sessions WHERE account_id = ?",
                    (row["account_id"],),
                ).fetchone()["count"]
                provider_bucket["total_tokens"] += usage_weekly or usage_hourly

            user_rows = conn.execute("""
                SELECT
                    user_usage.user_id,
                    users.display_name,
                    users.email,
                    SUM(user_usage.input_tokens) AS input_tokens,
                    SUM(user_usage.output_tokens) AS output_tokens,
                    SUM(user_usage.cache_hit_tokens) AS cache_hit_tokens,
                    SUM(user_usage.request_count) AS request_count
                FROM user_usage
                LEFT JOIN users ON users.user_id = user_usage.user_id
                GROUP BY user_usage.user_id, users.display_name, users.email
            """).fetchall()
            users = [dict(row) for row in user_rows]
        return {
            "providers": provider_summary,
            "provider_totals": [
                {"provider": provider, **data} for provider, data in provider_totals.items()
            ],
            "users": users,
        }

    def _next_hour_reset(self) -> datetime:
        now = self._now()
        return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

    def _next_week_reset(self) -> datetime:
        now = self._now()
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        days_until_monday = (7 - start_of_day.weekday()) % 7
        days_until_monday = days_until_monday or 7
        return start_of_day + timedelta(days=days_until_monday)

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

    def get_available_account(self, provider: ProviderType) -> Optional[Account]:
        now = int(datetime.now().timestamp())
        with self._get_connection() as conn:
            row = conn.execute("""
                SELECT * FROM accounts 
                WHERE provider = ? AND status IN ('active', 'ready')
                AND encrypted_credential IS NOT NULL
                AND COALESCE(inventory_source, 'vault') != 'system'
                AND (cooldown_until IS NULL OR cooldown_until < ?)
                ORDER BY usage_tpm ASC LIMIT 1
            """, (provider.value, now)).fetchone()
            if row:
                return self._row_to_account(row)
        return None

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

    def count_accounts(self, provider: Optional[str] = None, status: Optional[str] = None, include_system: bool = False) -> int:
        query = "SELECT COUNT(*) AS count FROM accounts WHERE encrypted_credential IS NOT NULL AND provider = 'codex'"
        query += self._account_inventory_filter(include_system)
        params: list[object] = []
        if provider and provider.lower() != 'codex':
            return 0
        if provider:
            query += " AND provider = ?"
            params.append(provider)
        if status:
            query += " AND status = ?"
            params.append(status)
        with self._get_connection() as conn:
            row = conn.execute(query, params).fetchone()
            return int(row["count"] if row else 0)

    def save_session(self, session: Session):
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO sessions 
                (client_session_id, backend_id, provider, account_id, user_id, api_key_id, cwd_path, prefix_hash, status, fence_token, last_context_tokens, created_at, updated_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session.client_session_id,
                session.backend_id,
                session.provider.value,
                session.account_id,
                session.user_id,
                session.api_key_id,
                session.cwd_path,
                session.prefix_hash,
                session.status.value,
                session.fence_token,
                session.last_context_tokens,
                int(session.created_at.timestamp()),
                int(session.updated_at.timestamp()),
                int(session.expires_at.timestamp())
            ))
            conn.commit()

    def get_session(self, client_session_id: str) -> Optional[Session]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE client_session_id = ?", (client_session_id,)).fetchone()
            if row:
                return self._row_to_session(row)
        return None

    def _row_to_session(self, row) -> Session:
        return Session(
            client_session_id=row["client_session_id"],
            backend_id=row["backend_id"],
            provider=ProviderType(row["provider"]),
            account_id=row["account_id"],
            user_id=row["user_id"],
            api_key_id=row["api_key_id"],
            cwd_path=row["cwd_path"],
            prefix_hash=row["prefix_hash"],
            status=SessionStatus(row["status"]),
            fence_token=row["fence_token"],
            last_context_tokens=row["last_context_tokens"],
            created_at=datetime.fromtimestamp(row["created_at"]),
            updated_at=datetime.fromtimestamp(row["updated_at"]),
            expires_at=datetime.fromtimestamp(row["expires_at"])
        )

    def delete_expired_sessions(self):
        now = int(datetime.now().timestamp())
        with self._get_connection() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            conn.commit()
