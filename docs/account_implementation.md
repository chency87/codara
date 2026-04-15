# Implementation Guide: AccountPool & UsageMonitor
## UAG Python Module — Account Management & Usage Tracking

**Version:** 1.0  
**Depends On:** UAG CLIProxyAPI Account Management Design v1.0

---

## 0. Current implementation notes

- The encrypted database and local vault are the authoritative account registry.
- Provider CLI auth paths are only materialization targets for the currently selected CLI-primary credential.
- Automatic selection should keep one CLI-primary account active at a time and promote the next healthiest ready account when the active account reaches 5% remaining headroom or less.
- For Codex OAuth usage, the current runtime should prefer the WHAM-style quota endpoint before older dashboard-style URLs.

---

## 3. Data Layer

### 3.1 `models.py` — Types First

Every data structure that crosses a module boundary is typed here. Read this file before implementing any other module.

```python
# uag/accounts/models.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Optional


# ── Enums ──────────────────────────────────────────────────────────────────

class Provider(StrEnum):
    CLAUDE      = "claude"
    CODEX       = "codex"
    GEMINI_CLI  = "gemini-cli"
    ANTIGRAVITY = "antigravity"
    QWEN        = "qwen"
    IFLOW       = "iflow"


class AuthType(StrEnum):
    OAUTH   = "oauth"    # JSON token file managed by CLIProxyAPI
    API_KEY = "api_key"  # sk-... / AIzaSy... static key


class AccountStatus(StrEnum):
    READY     = "ready"     # healthy, accepting requests
    COOLDOWN  = "cooldown"  # rate-limited; skip until cooldown_until
    EXPIRED   = "expired"   # OAuth token expired; needs re-auth
    DISABLED  = "disabled"  # manually disabled or deleted
    ERROR     = "error"     # unknown error reported by CLIProxyAPI


# ── Core account model ──────────────────────────────────────────────────────

@dataclass
class Account:
    """
    Canonical representation of one credential in the AccountPool.

    For OAuth accounts, `credential_id` is the email address (e.g. alice@example.com).
    For API key accounts, `credential_id` is the masked key prefix (e.g. sk-ant-...q2).
    """
    account_id:     str                      # uag_acc_<ulid> — UAG-internal ID
    credential_id:  str                      # CLIProxyAPI's identifier for this credential
    provider:       Provider
    auth_type:      AuthType
    status:         AccountStatus
    label:          Optional[str]            # human-readable name
    auth_index:     Optional[str]            # 16-char hex; set after first usage observation
    cooldown_until: Optional[datetime]       # None if not in cooldown
    last_seen_at:   Optional[datetime]       # last time CLIProxyAPI reported this account
    last_used_at:   Optional[datetime]       # last time a request was attributed to it
    created_at:     datetime
    updated_at:     datetime


# ── Rate limit window ───────────────────────────────────────────────────────

@dataclass
class RateLimitWindow:
    account_id:    str
    provider:      Provider
    window_start:  datetime        # floor to 1-minute buckets
    requests_made: int = 0
    tokens_used:   int = 0
    failures:      int = 0
    cooldown_until: Optional[datetime] = None


# ── Usage snapshot (from CLIProxyAPI GET /usage) ────────────────────────────

@dataclass
class TokenRecord:
    """One entry from usage.apis[path].models[model].details"""
    timestamp:        datetime
    source:           str           # provider string from CLIProxyAPI
    auth_index:       str           # 16-char hex credential hash
    input_tokens:     int
    output_tokens:    int
    reasoning_tokens: int
    cached_tokens:    int
    total_tokens:     int
    failed:           bool


@dataclass
class UsageSnapshot:
    """
    Parsed result of GET /v0/management/usage.
    Only the fields UAG needs — the full response has more.
    """
    total_requests:   int
    success_count:    int
    failure_count:    int
    total_tokens:     int
    records:          list[TokenRecord] = field(default_factory=list)
    exported_at:      Optional[datetime] = None  # set when from /usage/export


# ── OAuth flow state ────────────────────────────────────────────────────────

@dataclass
class OAuthFlowState:
    state:      str       # opaque state token from CLIProxyAPI
    provider:   Provider
    url:        str       # browser URL to open
    started_at: datetime
    completed:  bool = False
    error:      Optional[str] = None
```

### 3.2 `db.py` — Schema and Repository

One file handles schema creation and all DB reads/writes. No ORM — raw SQL with `aiosqlite` so you see exactly what's happening.

```python
# uag/accounts/db.py
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from .models import Account, AccountStatus, AuthType, Provider, RateLimitWindow

# ── Schema ──────────────────────────────────────────────────────────────────

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS accounts (
    account_id      TEXT PRIMARY KEY,
    credential_id   TEXT NOT NULL UNIQUE,
    provider        TEXT NOT NULL,
    auth_type       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'ready',
    label           TEXT,
    auth_index      TEXT,
    cooldown_until  INTEGER,          -- Unix ms; NULL = not in cooldown
    last_seen_at    INTEGER,
    last_used_at    INTEGER,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_accounts_provider ON accounts(provider);
CREATE INDEX IF NOT EXISTS idx_accounts_status   ON accounts(status);
CREATE INDEX IF NOT EXISTS idx_accounts_auth_idx ON accounts(auth_index);

CREATE TABLE IF NOT EXISTS rate_limit_windows (
    account_id      TEXT    NOT NULL,
    provider        TEXT    NOT NULL,
    window_start    INTEGER NOT NULL,   -- Unix ms, floored to 1-minute
    requests_made   INTEGER NOT NULL DEFAULT 0,
    tokens_used     INTEGER NOT NULL DEFAULT 0,
    failures        INTEGER NOT NULL DEFAULT 0,
    cooldown_until  INTEGER,
    PRIMARY KEY (account_id, provider, window_start)
);

CREATE INDEX IF NOT EXISTS idx_rlw_account ON rate_limit_windows(account_id, window_start);

CREATE TABLE IF NOT EXISTS usage_export_log (
    export_id       TEXT    PRIMARY KEY,
    exported_at     INTEGER NOT NULL,
    total_requests  INTEGER NOT NULL,
    total_tokens    INTEGER NOT NULL,
    raw_json        TEXT    NOT NULL    -- full /usage/export payload for reimport
);
"""

# ── Helpers ─────────────────────────────────────────────────────────────────

def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)

def _dt_to_ms(dt: Optional[datetime]) -> Optional[int]:
    return int(dt.timestamp() * 1000) if dt else None

def _ms_to_dt(ms: Optional[int]) -> Optional[datetime]:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc) if ms else None

def _row_to_account(row: aiosqlite.Row) -> Account:
    return Account(
        account_id     = row["account_id"],
        credential_id  = row["credential_id"],
        provider       = Provider(row["provider"]),
        auth_type      = AuthType(row["auth_type"]),
        status         = AccountStatus(row["status"]),
        label          = row["label"],
        auth_index     = row["auth_index"],
        cooldown_until = _ms_to_dt(row["cooldown_until"]),
        last_seen_at   = _ms_to_dt(row["last_seen_at"]),
        last_used_at   = _ms_to_dt(row["last_used_at"]),
        created_at     = _ms_to_dt(row["created_at"]),
        updated_at     = _ms_to_dt(row["updated_at"]),
    )

# ── Repository ───────────────────────────────────────────────────────────────

class AccountRepository:
    """
    All DB operations for the accounts module.
    Caller is responsible for opening/closing the connection.
    Pass a connected aiosqlite.Connection instance.
    """

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = aiosqlite.Row

    async def init_schema(self) -> None:
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    # ── Account CRUD ──────────────────────────────────────────────────────

    async def upsert_account(self, acc: Account) -> None:
        now = _now_ms()
        await self._conn.execute("""
            INSERT INTO accounts (
                account_id, credential_id, provider, auth_type, status,
                label, auth_index, cooldown_until, last_seen_at, last_used_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                status         = excluded.status,
                label          = excluded.label,
                auth_index     = COALESCE(excluded.auth_index, auth_index),
                cooldown_until = excluded.cooldown_until,
                last_seen_at   = excluded.last_seen_at,
                last_used_at   = COALESCE(excluded.last_used_at, last_used_at),
                updated_at     = ?
        """, (
            acc.account_id, acc.credential_id, acc.provider, acc.auth_type,
            acc.status, acc.label, acc.auth_index,
            _dt_to_ms(acc.cooldown_until), _dt_to_ms(acc.last_seen_at),
            _dt_to_ms(acc.last_used_at), _dt_to_ms(acc.created_at), now, now,
        ))
        await self._conn.commit()

    async def get_by_id(self, account_id: str) -> Optional[Account]:
        cur = await self._conn.execute(
            "SELECT * FROM accounts WHERE account_id = ?", (account_id,)
        )
        row = await cur.fetchone()
        return _row_to_account(row) if row else None

    async def get_by_auth_index(self, auth_index: str) -> Optional[Account]:
        cur = await self._conn.execute(
            "SELECT * FROM accounts WHERE auth_index = ?", (auth_index,)
        )
        row = await cur.fetchone()
        return _row_to_account(row) if row else None

    async def get_by_credential_id(self, credential_id: str) -> Optional[Account]:
        cur = await self._conn.execute(
            "SELECT * FROM accounts WHERE credential_id = ?", (credential_id,)
        )
        row = await cur.fetchone()
        return _row_to_account(row) if row else None

    async def list_by_provider(
        self,
        provider: Provider,
        status: Optional[AccountStatus] = None,
    ) -> list[Account]:
        if status:
            cur = await self._conn.execute(
                "SELECT * FROM accounts WHERE provider = ? AND status = ?",
                (provider, status),
            )
        else:
            cur = await self._conn.execute(
                "SELECT * FROM accounts WHERE provider = ?", (provider,)
            )
        rows = await cur.fetchall()
        return [_row_to_account(r) for r in rows]

    async def list_all(self) -> list[Account]:
        cur = await self._conn.execute("SELECT * FROM accounts")
        rows = await cur.fetchall()
        return [_row_to_account(r) for r in rows]

    async def set_status(
        self,
        account_id: str,
        status: AccountStatus,
        cooldown_until: Optional[datetime] = None,
    ) -> None:
        await self._conn.execute(
            """UPDATE accounts
               SET status = ?, cooldown_until = ?, updated_at = ?
               WHERE account_id = ?""",
            (status, _dt_to_ms(cooldown_until), _now_ms(), account_id),
        )
        await self._conn.commit()

    async def set_auth_index(self, account_id: str, auth_index: str) -> None:
        await self._conn.execute(
            "UPDATE accounts SET auth_index = ?, updated_at = ? WHERE account_id = ?",
            (auth_index, _now_ms(), account_id),
        )
        await self._conn.commit()

    async def delete(self, account_id: str) -> None:
        await self._conn.execute(
            "DELETE FROM accounts WHERE account_id = ?", (account_id,)
        )
        await self._conn.commit()

    # ── Rate limit windows ────────────────────────────────────────────────

    async def upsert_rate_window(self, w: RateLimitWindow) -> None:
        await self._conn.execute("""
            INSERT INTO rate_limit_windows
                (account_id, provider, window_start, requests_made, tokens_used, failures, cooldown_until)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, provider, window_start) DO UPDATE SET
                requests_made  = requests_made  + excluded.requests_made,
                tokens_used    = tokens_used    + excluded.tokens_used,
                failures       = failures       + excluded.failures,
                cooldown_until = COALESCE(excluded.cooldown_until, cooldown_until)
        """, (
            w.account_id, w.provider,
            int(w.window_start.timestamp() * 1000),
            w.requests_made, w.tokens_used, w.failures,
            _dt_to_ms(w.cooldown_until),
        ))
        await self._conn.commit()

    async def get_recent_windows(
        self, account_id: str, since_ms: int
    ) -> list[RateLimitWindow]:
        cur = await self._conn.execute(
            """SELECT * FROM rate_limit_windows
               WHERE account_id = ? AND window_start >= ?
               ORDER BY window_start DESC""",
            (account_id, since_ms),
        )
        rows = await cur.fetchall()
        return [
            RateLimitWindow(
                account_id    = r["account_id"],
                provider      = Provider(r["provider"]),
                window_start  = _ms_to_dt(r["window_start"]),
                requests_made = r["requests_made"],
                tokens_used   = r["tokens_used"],
                failures      = r["failures"],
                cooldown_until= _ms_to_dt(r["cooldown_until"]),
            )
            for r in rows
        ]

    # ── Usage export log ──────────────────────────────────────────────────

    async def save_export(
        self, export_id: str, exported_at: datetime,
        total_requests: int, total_tokens: int, raw_json: str,
    ) -> None:
        await self._conn.execute("""
            INSERT OR REPLACE INTO usage_export_log
                (export_id, exported_at, total_requests, total_tokens, raw_json)
            VALUES (?, ?, ?, ?, ?)
        """, (export_id, _dt_to_ms(exported_at), total_requests, total_tokens, raw_json))
        await self._conn.commit()

    async def get_latest_export(self) -> Optional[dict]:
        cur = await self._conn.execute(
            "SELECT raw_json FROM usage_export_log ORDER BY exported_at DESC LIMIT 1"
        )
        row = await cur.fetchone()
        return json.loads(row["raw_json"]) if row else None
```

---

## 4. CLIProxyAPI Client

All HTTP calls to CLIProxyAPI are in one place. If the Management API changes, only this file needs updating.

```python
# uag/accounts/cliproxy_client.py
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
import structlog

from .models import (
    Account, AccountStatus, AuthType, OAuthFlowState,
    Provider, TokenRecord, UsageSnapshot,
)

log = structlog.get_logger(__name__)


class CLIProxyError(Exception):
    """Raised when CLIProxyAPI returns a non-2xx response or unexpected shape."""
    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"CLIProxy {status_code}: {body}")


class CLIProxyClient:
    """
    Async HTTP client for the CLIProxyAPI Management API.
    Base path: http://<host>:<port>/v0/management

    Usage:
        async with CLIProxyClient("http://localhost:8317", "my-mgmt-key") as client:
            accounts = await client.list_auth_files()
    """

    def __init__(self, base_url: str, management_key: str) -> None:
        # base_url: e.g. "http://localhost:8317"
        self._base = base_url.rstrip("/") + "/v0/management"
        self._key  = management_key
        self._http: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "CLIProxyClient":
        self._http = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self._key}"},
            timeout=httpx.Timeout(10.0, connect=3.0),
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._http:
            await self._http.aclose()

    # ── Internal helpers ──────────────────────────────────────────────────

    async def _get(self, path: str, **params: Any) -> Any:
        assert self._http, "Use as async context manager"
        r = await self._http.get(f"{self._base}{path}", params=params or None)
        if r.status_code >= 400:
            raise CLIProxyError(r.status_code, r.text)
        return r.json()

    async def _put(self, path: str, body: Any) -> Any:
        assert self._http
        r = await self._http.put(f"{self._base}{path}", json=body)
        if r.status_code >= 400:
            raise CLIProxyError(r.status_code, r.text)
        return r.json()

    async def _patch(self, path: str, body: Any) -> Any:
        assert self._http
        r = await self._http.patch(f"{self._base}{path}", json=body)
        if r.status_code >= 400:
            raise CLIProxyError(r.status_code, r.text)
        return r.json()

    async def _post(self, path: str, body: Any = None) -> Any:
        assert self._http
        r = await self._http.post(f"{self._base}{path}", json=body)
        if r.status_code >= 400:
            raise CLIProxyError(r.status_code, r.text)
        return r.json()

    async def _delete(self, path: str, **params: Any) -> Any:
        assert self._http
        r = await self._http.delete(f"{self._base}{path}", params=params or None)
        if r.status_code >= 400:
            raise CLIProxyError(r.status_code, r.text)
        return r.json()

    # ── Usage statistics ──────────────────────────────────────────────────

    async def ensure_usage_enabled(self) -> None:
        """Enable usage-statistics if currently off. Call at startup."""
        data = await self._get("/usage-statistics-enabled")
        if not data.get("usage-statistics-enabled"):
            await self._put("/usage-statistics-enabled", {"value": True})
            log.info("cliproxy.usage_statistics.enabled")

    async def get_usage(self) -> UsageSnapshot:
        data = await self._get("/usage")
        return self._parse_usage(data.get("usage", {}))

    async def export_usage(self) -> tuple[UsageSnapshot, str]:
        """
        Returns (parsed_snapshot, raw_json_string).
        Store raw_json to usage_export_log for reimport after restart.
        """
        data = await self._get("/usage/export")
        raw  = json.dumps(data)
        snap = self._parse_usage(data.get("usage", {}))
        if data.get("exported_at"):
            snap.exported_at = datetime.fromisoformat(data["exported_at"])
        return snap, raw

    async def import_usage(self, raw_json: str) -> dict:
        """Re-import a previously exported snapshot. Returns {added, skipped, ...}."""
        body = json.loads(raw_json)
        return await self._post("/usage/import", body)

    def _parse_usage(self, usage: dict) -> UsageSnapshot:
        records: list[TokenRecord] = []
        for _path, path_data in usage.get("apis", {}).items():
            for _model, model_data in path_data.get("models", {}).items():
                for detail in model_data.get("details", []):
                    tok = detail.get("tokens", {})
                    records.append(TokenRecord(
                        timestamp        = datetime.fromisoformat(detail["timestamp"]),
                        source           = detail.get("source", ""),
                        auth_index       = detail.get("auth_index", ""),
                        input_tokens     = tok.get("input_tokens", 0),
                        output_tokens    = tok.get("output_tokens", 0),
                        reasoning_tokens = tok.get("reasoning_tokens", 0),
                        cached_tokens    = tok.get("cached_tokens", 0),
                        total_tokens     = tok.get("total_tokens", 0),
                        failed           = detail.get("failed", False),
                    ))
        return UsageSnapshot(
            total_requests = usage.get("total_requests", 0),
            success_count  = usage.get("success_count", 0),
            failure_count  = usage.get("failure_count", 0),
            total_tokens   = usage.get("total_tokens", 0),
            records        = records,
        )

    # ── Auth file (OAuth credential) management ───────────────────────────

    async def list_auth_files(self) -> list[dict]:
        """Returns raw file list from CLIProxyAPI. AccountPool maps these to Account objects."""
        data = await self._get("/auth-files")
        return data.get("files", [])

    async def delete_auth_file(self, name: str) -> None:
        await self._delete("/auth-files", name=name)
        log.info("cliproxy.auth_file.deleted", name=name)

    async def delete_all_auth_files(self) -> int:
        data = await self._delete("/auth-files", all="true")
        return data.get("deleted", 0)

    # ── OAuth login flows ─────────────────────────────────────────────────

    _AUTH_URL_PATHS: dict[Provider, str] = {
        Provider.CLAUDE:      "/anthropic-auth-url",
        Provider.CODEX:       "/codex-auth-url",
        Provider.GEMINI_CLI:  "/gemini-cli-auth-url",
        Provider.ANTIGRAVITY: "/antigravity-auth-url",
        Provider.QWEN:        "/qwen-auth-url",
        Provider.IFLOW:       "/iflow-auth-url",
    }

    async def start_oauth_flow(
        self, provider: Provider, gcp_project_id: Optional[str] = None
    ) -> OAuthFlowState:
        path   = self._AUTH_URL_PATHS[provider]
        params = {}
        if provider == Provider.GEMINI_CLI and gcp_project_id:
            params["project_id"] = gcp_project_id
        data = await self._get(path, **params)
        return OAuthFlowState(
            state      = data["state"],
            provider   = provider,
            url        = data["url"],
            started_at = datetime.now(timezone.utc),
        )

    async def poll_oauth_status(self, state: str) -> str:
        """
        Returns: "wait" | "ok" | "error"
        Call on a 3-second interval. Stop when result is not "wait".
        """
        data = await self._get("/get-auth-status", state=state)
        return data.get("status", "error")

    # ── API key management ────────────────────────────────────────────────

    _KEY_PATHS: dict[Provider, str] = {
        Provider.CLAUDE:     "/claude-api-key",
        Provider.CODEX:      "/codex-api-key",
        Provider.GEMINI_CLI: "/gemini-api-key",
    }

    async def list_api_keys(self, provider: Provider) -> list[dict]:
        path = self._KEY_PATHS[provider]
        data = await self._get(path)
        # Response key name varies per provider: "claude-api-key", "codex-api-key", etc.
        key_name = path.lstrip("/")
        return data.get(key_name, [])

    async def add_api_key(self, provider: Provider, entry: dict) -> None:
        """
        entry: {"api-key": "sk-...", "base-url": "...", "excluded-models": [...]}
        Fetches current list, appends entry, PUTs full list back.
        """
        current = await self.list_api_keys(provider)
        current.append(entry)
        await self._put(self._KEY_PATHS[provider], current)
        log.info("cliproxy.api_key.added", provider=provider)

    async def remove_api_key(self, provider: Provider, api_key_value: str) -> None:
        path = self._KEY_PATHS[provider]
        await self._delete(path, **{"api-key": api_key_value})
        log.info("cliproxy.api_key.removed", provider=provider)

    # ── Quota & retry config ──────────────────────────────────────────────

    async def configure_for_production(self) -> None:
        """
        Apply recommended production settings. Call once at startup after
        ensure_usage_enabled(). Safe to call repeatedly — all ops are idempotent.
        """
        await self._put("/quota-exceeded/switch-project",       {"value": True})
        await self._put("/quota-exceeded/switch-preview-model", {"value": True})
        await self._patch("/request-retry",       {"value": 3})
        await self._patch("/max-retry-interval",  {"value": 2})
        log.info("cliproxy.production_config.applied")
```

---

## 5. AccountPool

The AccountPool is the source of truth for credential state inside UAG. It syncs with CLIProxyAPI periodically and exposes a simple interface for the Orchestrator to use.

```python
# uag/accounts/pool.py
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional
from ulid import ULID  # pip install python-ulid

import structlog

from .cliproxy_client import CLIProxyClient, CLIProxyError
from .db import AccountRepository
from .models import Account, AccountStatus, AuthType, OAuthFlowState, Provider

log = structlog.get_logger(__name__)

# How long to wait before allowing a cooled-down account back into rotation
COOLDOWN_DURATION = timedelta(seconds=60)

# How many consecutive failures on one auth_index triggers COOLDOWN
FAILURE_THRESHOLD = 3


class AccountPool:
    """
    Manages the lifecycle of managed provider credentials, primarily the Codex account pool.

    Responsibilities:
    - Sync credential state from CLIProxyAPI on startup and every `sync_interval` seconds
    - Expose a `get_available()` method for the Orchestrator to pick a ready account
    - Move accounts into COOLDOWN on failure, release them when the timer expires
    - Provide add/remove operations that write through to CLIProxyAPI

    Usage:
        pool = AccountPool(client, repo, sync_interval=60)
        await pool.start()                 # begins background sync loop
        acc = await pool.get_available(Provider.CLAUDE)
        ...
        await pool.stop()
    """

    def __init__(
        self,
        client: CLIProxyClient,
        repo: AccountRepository,
        sync_interval: int = 60,
    ) -> None:
        self._client   = client
        self._repo     = repo
        self._interval = sync_interval
        self._task: Optional[asyncio.Task] = None
        # In-memory failure counter: auth_index → consecutive failure count
        self._failure_counts: dict[str, int] = {}

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Sync once immediately, then begin background sync loop."""
        await self._sync()
        self._task = asyncio.create_task(self._sync_loop(), name="account_pool_sync")
        log.info("account_pool.started", sync_interval=self._interval)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("account_pool.stopped")

    # ── Public interface for Orchestrator ─────────────────────────────────

    async def get_available(self, provider: Provider) -> Optional[Account]:
        """
        Return the next available (non-cooldown, non-expired) account
        for the given provider. Returns None if none are available.

        Selection strategy: first-ready from DB (simplest; extend to
        round-robin by tracking last-used per provider if needed).
        """
        # First, release any accounts whose cooldown has expired
        await self._release_expired_cooldowns(provider)

        accounts = await self._repo.list_by_provider(provider, AccountStatus.READY)
        if not accounts:
            log.warning("account_pool.no_accounts_available", provider=provider)
            return None

        return accounts[0]

    async def record_failure(self, account: Account) -> None:
        """
        Call when a request using `account` fails with a rate-limit signal.
        After FAILURE_THRESHOLD consecutive failures, moves account to COOLDOWN.
        """
        key = account.auth_index or account.account_id
        self._failure_counts[key] = self._failure_counts.get(key, 0) + 1

        if self._failure_counts[key] >= FAILURE_THRESHOLD:
            cooldown_until = datetime.now(timezone.utc) + COOLDOWN_DURATION
            await self._repo.set_status(
                account.account_id, AccountStatus.COOLDOWN, cooldown_until
            )
            self._failure_counts[key] = 0
            log.warning(
                "account_pool.cooldown_entered",
                account_id=account.account_id,
                provider=account.provider,
                until=cooldown_until.isoformat(),
            )

    async def record_success(self, account: Account) -> None:
        """Reset failure counter on success."""
        key = account.auth_index or account.account_id
        self._failure_counts.pop(key, None)

    async def set_auth_index(self, account_id: str, auth_index: str) -> None:
        """
        Called by UsageMonitor when it first observes an auth_index
        in a usage detail record and matches it to an account.
        """
        await self._repo.set_auth_index(account_id, auth_index)

    # ── Account add/remove (write-through to CLIProxyAPI) ─────────────────

    async def add_oauth_account(
        self, provider: Provider, gcp_project_id: Optional[str] = None
    ) -> OAuthFlowState:
        """
        Initiates a browser-based OAuth flow.
        Returns the flow state including the URL to open.
        Caller must poll `poll_oauth_flow(state)` until complete.
        """
        flow = await self._client.start_oauth_flow(provider, gcp_project_id)
        log.info("account_pool.oauth_flow.started", provider=provider, state=flow.state)
        return flow

    async def poll_oauth_flow(self, flow: OAuthFlowState) -> OAuthFlowState:
        """
        Poll once. Call every 3 seconds until flow.completed is True.
        On completion, triggers a sync to pick up the new credential.
        """
        status = await self._client.poll_oauth_status(flow.state)
        if status == "ok":
            flow.completed = True
            await self._sync()   # pick up the new auth file immediately
            log.info("account_pool.oauth_flow.completed", provider=flow.provider)
        elif status == "error":
            flow.completed = True
            flow.error = "OAuth flow failed"
            log.error("account_pool.oauth_flow.failed", provider=flow.provider)
        return flow

    async def add_api_key(
        self, provider: Provider, api_key: str, base_url: str,
        label: Optional[str] = None, excluded_models: Optional[list[str]] = None,
    ) -> Account:
        """Register a new API key with CLIProxyAPI and add to local pool."""
        entry: dict = {"api-key": api_key, "base-url": base_url}
        if excluded_models:
            entry["excluded-models"] = excluded_models

        await self._client.add_api_key(provider, entry)

        account = Account(
            account_id    = f"uag_acc_{ULID()}",
            credential_id = self._mask_key(api_key),
            provider      = provider,
            auth_type     = AuthType.API_KEY,
            status        = AccountStatus.READY,
            label         = label,
            auth_index    = None,
            cooldown_until= None,
            last_seen_at  = datetime.now(timezone.utc),
            last_used_at  = None,
            created_at    = datetime.now(timezone.utc),
            updated_at    = datetime.now(timezone.utc),
        )
        await self._repo.upsert_account(account)
        log.info("account_pool.api_key.added", provider=provider, account_id=account.account_id)
        return account

    async def remove_account(self, account_id: str) -> None:
        """Remove an account from the pool and from CLIProxyAPI."""
        account = await self._repo.get_by_id(account_id)
        if not account:
            raise ValueError(f"Account {account_id!r} not found")

        if account.auth_type == AuthType.OAUTH:
            await self._client.delete_auth_file(f"{account.credential_id}.json")
        else:
            # credential_id is the masked key — we need the full key for deletion
            # Operators should pass the actual key value; this is a limitation to document
            raise NotImplementedError(
                "API key removal requires the raw key value. "
                "Call client.remove_api_key() directly with the full key."
            )

        await self._repo.delete(account_id)
        log.info("account_pool.account.removed", account_id=account_id)

    # ── Internal sync ─────────────────────────────────────────────────────

    async def _sync_loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self._sync()
            except CLIProxyError as e:
                log.error("account_pool.sync.failed", error=str(e))
            except Exception as e:
                log.exception("account_pool.sync.unexpected_error", error=str(e))

    async def _sync(self) -> None:
        """
        Pull current state from CLIProxyAPI and reconcile with local DB.

        For each auth file:
          - If not in DB: create new Account record with READY/EXPIRED/ERROR status
          - If in DB: update status from CLIProxyAPI's reported status
          - Accounts in local DB but NOT in CLIProxyAPI response: set to DISABLED

        API keys are not synced this way — they are managed entirely by UAG.
        """
        now = datetime.now(timezone.utc)
        try:
            files = await self._client.list_auth_files()
        except CLIProxyError as e:
            log.error("account_pool.sync.list_failed", error=str(e))
            return

        seen_credential_ids: set[str] = set()

        for f in files:
            credential_id = f.get("id") or f.get("name", "").replace(".json", "")
            if not credential_id:
                continue
            seen_credential_ids.add(credential_id)

            # Map CLIProxyAPI status → AccountStatus
            raw_status   = f.get("status", "ready")
            is_disabled  = f.get("disabled", False)
            is_unavailable = f.get("unavailable", False)

            if is_disabled:
                status = AccountStatus.DISABLED
            elif raw_status == "expired":
                status = AccountStatus.EXPIRED
            elif raw_status == "error" or is_unavailable:
                status = AccountStatus.ERROR
            else:
                status = AccountStatus.READY

            provider_str = f.get("provider", "")
            try:
                provider = Provider(provider_str)
            except ValueError:
                log.warning("account_pool.sync.unknown_provider", provider=provider_str)
                continue

            existing = await self._repo.get_by_credential_id(credential_id)

            if existing:
                # Don't overwrite COOLDOWN status — that's UAG-internal
                if existing.status != AccountStatus.COOLDOWN:
                    await self._repo.set_status(existing.account_id, status)
                    log.debug(
                        "account_pool.sync.updated",
                        credential_id=credential_id,
                        status=status,
                    )
            else:
                new_account = Account(
                    account_id    = f"uag_acc_{ULID()}",
                    credential_id = credential_id,
                    provider      = provider,
                    auth_type     = AuthType.OAUTH,
                    status        = status,
                    label         = f.get("label"),
                    auth_index    = None,   # discovered via UsageMonitor
                    cooldown_until= None,
                    last_seen_at  = now,
                    last_used_at  = None,
                    created_at    = now,
                    updated_at    = now,
                )
                await self._repo.upsert_account(new_account)
                log.info(
                    "account_pool.sync.new_account",
                    credential_id=credential_id,
                    provider=provider,
                )

        # Mark OAuth accounts no longer reported by CLIProxyAPI as DISABLED
        all_accounts = await self._repo.list_all()
        for acc in all_accounts:
            if acc.auth_type == AuthType.OAUTH and acc.credential_id not in seen_credential_ids:
                if acc.status not in (AccountStatus.DISABLED, AccountStatus.COOLDOWN):
                    await self._repo.set_status(acc.account_id, AccountStatus.DISABLED)
                    log.info(
                        "account_pool.sync.disabled_missing",
                        account_id=acc.account_id,
                    )

        log.debug("account_pool.sync.done", accounts_seen=len(seen_credential_ids))

    async def _release_expired_cooldowns(self, provider: Provider) -> None:
        now = datetime.now(timezone.utc)
        accounts = await self._repo.list_by_provider(provider, AccountStatus.COOLDOWN)
        for acc in accounts:
            if acc.cooldown_until and now >= acc.cooldown_until:
                await self._repo.set_status(acc.account_id, AccountStatus.READY)
                log.info(
                    "account_pool.cooldown.released",
                    account_id=acc.account_id,
                    provider=provider,
                )

    @staticmethod
    def _mask_key(key: str) -> str:
        """Returns last 4 chars masked: sk-ant-...q2r9"""
        if len(key) <= 8:
            return key
        return key[:8] + "..." + key[-4:]
```

---

## 6. UsageMonitor

The UsageMonitor polls CLIProxyAPI for usage data, attributes tokens to accounts via `auth_index`, updates the rate limit windows, and exports snapshots before process shutdown.

```python
# uag/accounts/usage_monitor.py
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional
from ulid import ULID

import structlog

from .cliproxy_client import CLIProxyClient, CLIProxyError
from .db import AccountRepository
from .models import AccountStatus, RateLimitWindow, TokenRecord, UsageSnapshot
from .pool import AccountPool, FAILURE_THRESHOLD

log = structlog.get_logger(__name__)

# Polling intervals
USAGE_POLL_INTERVAL   = 10    # seconds between GET /usage calls
EXPORT_INTERVAL       = 300   # seconds between export snapshots (5 minutes)

# Rate limit detection: if failure_rate > threshold in last window, trigger cooldown
FAILURE_RATE_THRESHOLD = 0.10   # 10% failure rate


class UsageMonitor:
    """
    Background poller for CLIProxyAPI usage data.

    Responsibilities:
    - Poll GET /usage every USAGE_POLL_INTERVAL seconds
    - Diff successive snapshots to compute per-window deltas
    - Attribute deltas to accounts via auth_index → account_id mapping
    - Update rate_limit_windows table
    - Detect high failure rates and signal AccountPool to enter COOLDOWN
    - Export usage snapshots to DB every EXPORT_INTERVAL seconds

    Usage:
        monitor = UsageMonitor(client, repo, pool)
        await monitor.start()
        ...
        await monitor.stop()   # also triggers a final export
    """

    def __init__(
        self,
        client: CLIProxyClient,
        repo: AccountRepository,
        pool: AccountPool,
    ) -> None:
        self._client      = client
        self._repo        = repo
        self._pool        = pool
        self._poll_task:  Optional[asyncio.Task] = None
        self._export_task: Optional[asyncio.Task] = None
        # Track the last snapshot's total_requests to compute deltas
        self._last_total_requests: int = 0
        # Track processed record timestamps to avoid double-counting
        self._seen_timestamps: set[str] = set()

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._poll_task  = asyncio.create_task(self._poll_loop(),   name="usage_poll")
        self._export_task = asyncio.create_task(self._export_loop(), name="usage_export")
        log.info("usage_monitor.started")

    async def stop(self) -> None:
        for task in (self._poll_task, self._export_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        # Always export on shutdown — usage data resets on CLIProxyAPI restart
        await self._export_snapshot()
        log.info("usage_monitor.stopped")

    # ── Polling loop ──────────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self._poll_once()
            except CLIProxyError as e:
                log.error("usage_monitor.poll.failed", error=str(e))
            except Exception as e:
                log.exception("usage_monitor.poll.unexpected", error=str(e))
            await asyncio.sleep(USAGE_POLL_INTERVAL)

    async def _poll_once(self) -> None:
        snapshot = await self._client.get_usage()

        # Only process records we haven't seen before
        new_records = [
            r for r in snapshot.records
            if r.timestamp.isoformat() not in self._seen_timestamps
        ]

        if not new_records:
            return

        log.debug("usage_monitor.poll.new_records", count=len(new_records))

        for record in new_records:
            self._seen_timestamps.add(record.timestamp.isoformat())
            await self._process_record(record)

        # Keep seen_timestamps from growing unbounded: keep only last 10k entries
        if len(self._seen_timestamps) > 10_000:
            oldest = sorted(self._seen_timestamps)[:5_000]
            for ts in oldest:
                self._seen_timestamps.discard(ts)

        self._last_total_requests = snapshot.total_requests

    async def _process_record(self, record: TokenRecord) -> None:
        """
        For each new usage record:
        1. Look up the account by auth_index (establish mapping if new)
        2. Update the rate_limit_windows row for this 1-minute bucket
        3. If the record is a failure, signal AccountPool
        """
        account = await self._repo.get_by_auth_index(record.auth_index)

        if not account and record.auth_index:
            # New auth_index we haven't seen: try to match by provider
            # Heuristic: match the first READY account of the same provider
            # whose auth_index is not yet set.
            from .models import Provider
            try:
                provider = Provider(record.source)
            except ValueError:
                provider = None

            if provider:
                candidates = await self._repo.list_by_provider(provider)
                unlinked = [a for a in candidates if a.auth_index is None]
                if unlinked:
                    account = unlinked[0]
                    await self._pool.set_auth_index(account.account_id, record.auth_index)
                    log.info(
                        "usage_monitor.auth_index.linked",
                        auth_index=record.auth_index,
                        account_id=account.account_id,
                    )

        if not account:
            log.debug("usage_monitor.auth_index.unmatched", auth_index=record.auth_index)
            return

        # Floor timestamp to 1-minute window
        ts  = record.timestamp
        window_start = ts.replace(second=0, microsecond=0)

        window = RateLimitWindow(
            account_id    = account.account_id,
            provider      = account.provider,
            window_start  = window_start,
            requests_made = 1,
            tokens_used   = record.total_tokens,
            failures      = 1 if record.failed else 0,
        )
        await self._repo.upsert_rate_window(window)

        # Signal failure to AccountPool
        if record.failed:
            await self._pool.record_failure(account)
            log.warning(
                "usage_monitor.request.failed",
                account_id=account.account_id,
                auth_index=record.auth_index,
            )
        else:
            await self._pool.record_success(account)

        # Check rolling failure rate for this account over the last 5 minutes
        await self._check_failure_rate(account.account_id, window_start)

    async def _check_failure_rate(self, account_id: str, now: datetime) -> None:
        """
        If failure rate across recent windows exceeds FAILURE_RATE_THRESHOLD,
        trigger COOLDOWN even if consecutive failure count hasn't hit threshold.
        """
        since_ms = int((now.timestamp() - 300) * 1000)  # last 5 minutes
        windows = await self._repo.get_recent_windows(account_id, since_ms)
        if not windows:
            return

        total_requests = sum(w.requests_made for w in windows)
        total_failures = sum(w.failures       for w in windows)

        if total_requests < 5:
            return   # too few requests to judge

        rate = total_failures / total_requests
        if rate > FAILURE_RATE_THRESHOLD:
            account = await self._repo.get_by_id(account_id)
            if account and account.status == AccountStatus.READY:
                await self._pool.record_failure(account)
                log.warning(
                    "usage_monitor.high_failure_rate",
                    account_id=account_id,
                    failure_rate=f"{rate:.1%}",
                )

    # ── Export loop ───────────────────────────────────────────────────────

    async def _export_loop(self) -> None:
        while True:
            await asyncio.sleep(EXPORT_INTERVAL)
            try:
                await self._export_snapshot()
            except CLIProxyError as e:
                log.error("usage_monitor.export.failed", error=str(e))
            except Exception as e:
                log.exception("usage_monitor.export.unexpected", error=str(e))

    async def _export_snapshot(self) -> None:
        snapshot, raw_json = await self._client.export_usage()
        export_id = f"exp_{ULID()}"
        await self._repo.save_export(
            export_id      = export_id,
            exported_at    = snapshot.exported_at or datetime.now(timezone.utc),
            total_requests = snapshot.total_requests,
            total_tokens   = snapshot.total_tokens,
            raw_json       = raw_json,
        )
        log.info(
            "usage_monitor.export.saved",
            export_id=export_id,
            total_requests=snapshot.total_requests,
            total_tokens=snapshot.total_tokens,
        )

    async def restore_from_latest_export(self) -> None:
        """
        Call at startup after CLIProxyAPI is known to have just restarted.
        Reimports the last saved snapshot so usage history is not lost.
        """
        raw = await self._repo.get_latest_export()
        if not raw:
            log.info("usage_monitor.restore.no_export_found")
            return
        result = await self._client.import_usage(json.dumps(raw))
        log.info(
            "usage_monitor.restore.complete",
            added=result.get("added", 0),
            skipped=result.get("skipped", 0),
        )
```

---

## 7. Wiring It Together

### 7.1 `config.py`

```python
# uag/config.py
from dataclasses import dataclass


@dataclass
class Config:
    cliproxy_base_url:    str   = "http://localhost:8317"
    cliproxy_mgmt_key:    str   = ""          # set from env: UAG_MGMT_KEY
    db_path:              str   = "uag.db"
    pool_sync_interval:   int   = 60          # seconds
    restore_on_startup:   bool  = True        # reimport usage after restart


def load_config() -> Config:
    import os
    return Config(
        cliproxy_base_url  = os.getenv("CLIPROXY_BASE_URL", "http://localhost:8317"),
        cliproxy_mgmt_key  = os.getenv("UAG_MGMT_KEY", ""),
        db_path            = os.getenv("UAG_DB_PATH", "uag.db"),
        restore_on_startup = os.getenv("UAG_RESTORE_ON_STARTUP", "true").lower() == "true",
    )
```

### 7.2 Startup Sequence

```python
# uag/accounts/__init__.py  (or your app entrypoint)
from __future__ import annotations

import asyncio
import aiosqlite

from ..config import load_config
from .cliproxy_client import CLIProxyClient
from .db import AccountRepository
from .pool import AccountPool
from .usage_monitor import UsageMonitor


async def build_accounts_module():
    """
    Returns (pool, monitor, conn) — caller owns the connection lifecycle.

    Canonical startup order — do not change:
      1. Open DB + init schema
      2. Open CLIProxyAPI client
      3. Apply production config + enable usage statistics
      4. Restore usage snapshot (if configured)
      5. Start AccountPool (first sync is synchronous)
      6. Start UsageMonitor
    """
    cfg  = load_config()
    conn = await aiosqlite.connect(cfg.db_path)
    repo = AccountRepository(conn)
    await repo.init_schema()

    client  = CLIProxyClient(cfg.cliproxy_base_url, cfg.cliproxy_mgmt_key)
    await client.__aenter__()   # keep alive for module lifetime

    await client.configure_for_production()
    await client.ensure_usage_enabled()

    pool    = AccountPool(client, repo, sync_interval=cfg.pool_sync_interval)
    monitor = UsageMonitor(client, repo, pool)

    if cfg.restore_on_startup:
        await monitor.restore_from_latest_export()

    await pool.start()
    await monitor.start()

    return pool, monitor, conn, client


async def teardown_accounts_module(pool, monitor, conn, client) -> None:
    """Graceful shutdown — always export before closing."""
    await monitor.stop()    # triggers final export
    await pool.stop()
    await client.__aexit__(None, None, None)
    await conn.close()
```

---

## 8. Testing Checklist

Work through this list in order. Each item is one `pytest` test function.

### AccountRepository

```
[ ] upsert_account → get_by_id returns same data
[ ] upsert_account twice → updated_at advances, auth_index not overwritten with None
[ ] set_status → status changes, cooldown_until set and retrievable
[ ] list_by_provider + status filter returns only matching rows
[ ] upsert_rate_window twice → requests_made and tokens_used accumulate (not replace)
[ ] save_export → get_latest_export returns the most recent
```

### CLIProxyClient (use respx to mock httpx)

```
[ ] get_usage parses all fields from fixture response
[ ] get_usage returns empty records list when apis={} 
[ ] ensure_usage_enabled calls PUT only when currently false
[ ] start_oauth_flow returns OAuthFlowState with correct fields
[ ] poll_oauth_status returns "wait" / "ok" / "error" correctly
[ ] list_auth_files returns empty list on empty response
[ ] CLIProxyError raised on 4xx response
```

### AccountPool

```
[ ] _sync with 2 auth files creates 2 Account rows
[ ] _sync with previously-seen file updates status
[ ] _sync with file removed → account marked DISABLED
[ ] _sync does not overwrite COOLDOWN status
[ ] get_available returns None when no READY accounts
[ ] get_available releases expired COOLDOWN and returns account
[ ] record_failure N < THRESHOLD times → still READY
[ ] record_failure N >= THRESHOLD times → COOLDOWN
[ ] record_success resets failure counter
```

### UsageMonitor

```
[ ] _process_record with known auth_index updates rate_limit_windows
[ ] _process_record with unknown auth_index links to unlinked account
[ ] _process_record failed=True calls pool.record_failure
[ ] _poll_once deduplicates records already seen in _seen_timestamps
[ ] _check_failure_rate below threshold → no cooldown triggered
[ ] _check_failure_rate above threshold with >= 5 requests → record_failure called
[ ] _export_snapshot saves raw_json to DB
[ ] restore_from_latest_export calls import_usage with last saved JSON
[ ] restore_from_latest_export is a no-op when no export exists
```

---

## 9. Common Mistakes to Avoid

**Do not call `_sync()` inside `get_available()`.** Sync is background-only. If `get_available()` triggered a sync, slow CLIProxyAPI responses would block every Orchestrator dispatch.

**Do not store raw API key values anywhere.** `credential_id` in the DB uses the masked form. The full key is only held in memory during `add_api_key()` and passed directly to `CLIProxyClient`. It never touches SQLite.

**Do not trust `auth_index` mappings as stable.** CLIProxyAPI derives `auth_index` from a hash of the credential. If a token is refreshed and the underlying credential changes, the hash may change. Always check `get_by_auth_index` before assuming a mapping exists.

**Export before every restart.** `UsageMonitor.stop()` triggers a final export. If you kill the process with SIGKILL instead of clean shutdown, the last polling window is lost. Wire `SIGTERM` → `teardown_accounts_module()` at the top of your entrypoint.

**Do not set `seen_timestamps` as a persistent store.** It is intentionally in-memory. On restart, `restore_from_latest_export()` handles historical continuity; the `seen_timestamps` set only prevents double-processing within a single process lifetime.
