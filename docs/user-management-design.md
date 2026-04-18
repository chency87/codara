# Design Specification: User Management & API Key System
## Unified Agent Gateway (UAG)

**Depends On:** UAG SRDS v1.2, UAG API & Dashboard Design v1.0  
**Audience:** Internal engineers and operators

---

## 0. Scope

This document specifies the user identity model, API key lifecycle, workspace-per-user allocation strategy, and the self-service portal available to end users. It is intentionally scoped to the **user plane** — the operator plane is covered in the API & Dashboard Design document.

The three core guarantees this system must provide:

1. **Identity isolation** — every request is traceable to a specific user; no credential sharing between users.
2. **Workspace isolation** — each user's sessions are bound to a dedicated workspace directory, enabling prefix caching and preventing cross-user context bleed.
3. **Self-service within bounds** — users can inspect their own state and rotate their own credential without operator involvement, but cannot access any other user's data.

---

## 1. Data Model

### 1.1 Schema

```sql
-- Core user record
CREATE TABLE users (
    user_id         TEXT        PRIMARY KEY,          -- uag_usr_<ulid>
    email           TEXT        NOT NULL UNIQUE,
    display_name    TEXT        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'active',  -- 'active' | 'suspended' | 'deleted'
    workspace_path  TEXT        NOT NULL UNIQUE,      -- absolute path, set at provisioning
    created_at      INTEGER     NOT NULL,             -- Unix epoch ms
    created_by      TEXT        NOT NULL,             -- operator actor id
    updated_at      INTEGER     NOT NULL
);

-- API keys (one active key per user; rotations revoke the prior active key)
CREATE TABLE api_keys (
    key_id          TEXT        PRIMARY KEY,          -- uag_key_<ulid>
    user_id         TEXT        NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    key_hash        TEXT        NOT NULL UNIQUE,      -- SHA-256 of raw key; raw key never stored
    key_prefix      TEXT        NOT NULL,             -- first 8 chars of raw key, for display
    label           TEXT,                             -- optional human name, e.g. "laptop"
    status          TEXT        NOT NULL DEFAULT 'active',  -- 'active' | 'revoked'
    last_used_at    INTEGER,                          -- Unix epoch ms; updated on each use
    expires_at      INTEGER,                          -- NULL = no expiry
    created_at      INTEGER     NOT NULL,
    revoked_at      INTEGER                           -- NULL if not revoked
);

-- Per-user usage accounting (updated by UsageMonitor)
CREATE TABLE user_usage (
    user_id         TEXT        NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    period          TEXT        NOT NULL,             -- ISO date: '2026-04-12'
    provider        TEXT        NOT NULL,             -- 'codex' | 'gemini' | 'opencode'
    input_tokens    INTEGER     NOT NULL DEFAULT 0,
    output_tokens   INTEGER     NOT NULL DEFAULT 0,
    cache_hit_tokens INTEGER    NOT NULL DEFAULT 0,
    request_count   INTEGER     NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, period, provider)
);

-- Workspace reset log (append-only)
CREATE TABLE workspace_resets (
    reset_id        TEXT        PRIMARY KEY,          -- uag_rst_<ulid>
    user_id         TEXT        NOT NULL REFERENCES users(user_id),
    triggered_by    TEXT        NOT NULL,             -- 'user' | 'operator'
    actor_id        TEXT        NOT NULL,             -- user_id or operator token id
    sessions_wiped  INTEGER     NOT NULL,             -- count of sessions cleared
    reset_at        INTEGER     NOT NULL
);

CREATE INDEX idx_api_keys_user     ON api_keys(user_id);
CREATE INDEX idx_api_keys_hash     ON api_keys(key_hash);
CREATE INDEX idx_user_usage_user   ON user_usage(user_id, period);
CREATE INDEX idx_resets_user       ON workspace_resets(user_id);
```

### 1.2 User ID Format

User IDs follow the pattern `uag_usr_<ulid>`. ULIDs are used instead of UUIDs because they are monotonically sortable, which simplifies audit log queries and cursor-based pagination without a secondary `created_at` sort key.

The same pattern applies to all entity IDs in this system: `uag_key_<ulid>`, `uag_rst_<ulid>`.

---

## 2. User Provisioning

### 2.1 Lifecycle

Users are created exclusively by operators via the management API. There is no self-registration flow.

```
Operator                  UAG Management API            File System
   │                             │                           │
   │  POST /management/v1/users  │                           │
   │────────────────────────────▶│                           │
   │                             │  mkdir workspace_path     │
   │                             │──────────────────────────▶│
   │                             │  chmod 700                │
   │                             │──────────────────────────▶│
   │                             │  INSERT users             │
   │                             │  INSERT api_keys (first)  │
   │                             │                           │
   │◀────────────────────────────│                           │
   │  {user_id, api_key (once)}  │                           │
```

The raw API key is returned **exactly once** in the provisioning response. It is not stored; only its SHA-256 hash is persisted. The operator is responsible for delivering the key to the user securely (e.g., via a secrets manager or encrypted email).

### 2.2 Workspace Allocation

Each user is assigned a dedicated workspace directory at provisioning time:

```
{UAG_WORKSPACES_ROOT}/{user_id}/
```

For example: `/var/uag/workspaces/uag_usr_01HXYZ.../`

Rules:
- The directory is created by the Gateway process at provisioning time with permissions `700` (owner read/write/execute only).
- `workspace_path` is stored in the `users` table and used as the `cwd_path` for all sessions belonging to this user.
- The path is immutable after provisioning. If a user is deleted, the directory is **not** automatically removed — it is flagged for operator review and manual cleanup to prevent accidental data loss.
- All workspace paths must be subdirectories of `UAG_WORKSPACES_ROOT`. The path is validated with `canonicalize()` before insertion (see SRDS §8.2).

### 2.3 Provisioning Request

```
POST /management/v1/users
Authorization: Bearer <operator_token>

{
  "email": "alice@example.com",
  "display_name": "Alice",
  "key_label": "initial",        // optional label for the first key
  "key_expires_at": null,        // null = no expiry; ISO 8601 timestamp for expiring keys
  "max_concurrency": 3           // per-user in-flight turn limit
}
```

Response (key shown only here, never again):

```json
{
  "ok": true,
  "data": {
    "user_id": "uag_usr_01HXYZ...",
    "email": "alice@example.com",
    "display_name": "Alice",
    "workspace_path": "/var/uag/workspaces/uag_usr_01HXYZ.../",
    "api_key": {
      "key_id": "uag_key_01HABC...",
      "raw_key": "uagk_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
      "label": "initial",
      "expires_at": null
    },
    "created_at": "2026-04-12T10:00:00Z"
  }
}
```

### 2.4 User Status Transitions

| Transition | Trigger | Effect |
|---|---|---|
| `active` → `suspended` | `POST /management/v1/users/:id/suspend` | All API keys rejected with `403`; sessions remain in registry but no new turns accepted |
| `suspended` → `active` | `POST /management/v1/users/:id/unsuspend` | API keys re-accepted; existing sessions resumable |
| `active` → `deleted` | `DELETE /management/v1/users/:id` | All keys revoked; sessions evicted; workspace directory flagged (not deleted) |

Soft delete only — the `users` row is retained with `status = 'deleted'` for audit integrity. Hard purge requires a separate operator action with explicit confirmation.

---

## 3. API Key System

### 3.1 Key Format

API keys follow the format:

```
uagk_live_<32 random base62 chars>
```

Total length: 42 characters. The `uagk_live_` prefix allows keys to be identified in logs, secret scanners, and paste detectors without exposing the secret portion. A test environment uses `uagk_test_` prefix.

### 3.2 Key Validation Flow

Every inference request (`POST /v1/chat/completions`) passes through key validation before reaching the Orchestrator:

```
1. Extract:   Authorization: Bearer uagk_live_<secret>
2. Hash:      SHA-256(raw_key) → key_hash
3. Lookup:    SELECT * FROM api_keys WHERE key_hash = ? AND status = 'active'
4. Check:     key not expired (expires_at IS NULL OR expires_at > now())
5. Resolve:   JOIN users WHERE user_id = key.user_id AND status = 'active'
6. Inject:    user_id and workspace_path into request context
7. Update:    SET last_used_at = now()
```

Step 7 is currently a direct database update in the authenticated request path. The key hash lookup in step 3 uses the index on `api_keys(key_hash)` and should complete in under 1ms on warm cache.

**Rejection responses:**

| Condition | HTTP Status | Error Code |
|---|---|---|
| No Authorization header | `401` | `missing_api_key` |
| Key not found | `401` | `invalid_api_key` |
| Key revoked | `401` | `api_key_revoked` |
| Key expired | `401` | `api_key_expired` |
| User suspended | `403` | `user_suspended` |
| User deleted | `403` | `user_deleted` |

All rejection responses are identical in timing (constant-time comparison is used in step 3) to prevent key enumeration via timing side-channels.

### 3.3 Single-Key Rotation Model

Each user has exactly **one active API key** at a time. Rotating a key revokes the prior active key and returns a new raw key once. This keeps attribution simple and avoids orphaned long-lived credentials while still supporting recovery when a key is lost or exposed.

Operators control throughput with a per-user concurrency limit via `PATCH /management/v1/users/:id` with `{"max_concurrency": N}`.

### 3.4 Key Rotation (User-Initiated)

Users rotate keys through the self-service portal or the user API. Rotation immediately revokes the prior active key and returns a fresh raw key once.

```
POST /v1/user/keys
Authorization: Bearer <current_key>

{
  "label": "laptop-2026",
  "expires_at": null
}
```

Response returns the raw new key once only; there is no overlap window with the prior active key because Codara enforces a single-active-key model.

---

## 4. Workspace-per-User & Session Binding

### 4.1 How User Identity Drives Caching

When a request arrives with a valid API key, the Gateway injects the user's `workspace_path` into the normalized runtime `workspace_root` before forwarding to the Orchestrator. By default the base workspace is used directly, but the client may also provide a logical `workspace_id` to select a stable sub-workspace under that base path.

```
Incoming request:
  POST /v1/chat/completions
  Authorization: Bearer uagk_live_...
  { "model": "uag-codex-v5", "messages": [...], "provider": "codex", "workspace_id": "project-a" }

After key validation, Gateway enriches:
  workspace_root  = "/var/uag/workspaces/uag_usr_01HXYZ.../project-a"
  workspace_id    = "project-a"
  client_session_id = "<user_id>::<workspace_id>::<client_provided_session_id_or_default>"
  user_id         = "<user_id>"
  api_key_id      = "<resolved_key_id>"
```

The `client_session_id` is namespaced with the user's ID and workspace ID to prevent collisions between users or between separate workspaces owned by the same user.

In addition to the namespaced session ID, the runtime should persist explicit ownership metadata on each session row:

- `sessions.user_id` = owning user
- `sessions.api_key_id` = most recent active key that touched the session

This allows the management API and dashboard to show the bound user and key without relying on client-side string parsing alone. If the same user resumes a session with a newer key during rotation, `api_key_id` should update to that latest key while the session ownership remains with the same `user_id`.

### 4.2 Token Reduction via Prefix Caching

Because requests within the same `workspace_id` share the same rooted `workspace_path`, the `prefix_hash` (SHA-256 of normalized system prompt + file tree metadata) remains stable across turns. This is the primary token reduction mechanism:

- On the first request of a session, the full system prompt + file tree is sent.
- On subsequent requests, the cached prefix is reused — only the new turn content is charged as input tokens.
- Target cache hit rate: **> 80%** (SRDS §12).

The stability of a user-scoped workspace path is what makes this hit rate achievable. Users cannot supply arbitrary absolute paths; they may only choose `workspace_id` values that resolve beneath their provisioned base workspace, preserving both cache stability and path isolation.

### 4.3 Session Namespace

The `SessionRegistry` (SRDS §5.1) stores sessions keyed by `client_session_id`. For user-plane requests, the session ID is composed as:

```
client_session_id = "{user_id}::{workspace_id}::{session_label}"
```

Where `workspace_id` defaults to `"default"` and `session_label` defaults to `"default"` if the client does not provide them. A user who wants multiple parallel workstreams can pass distinct labels inside a stable workspace:

```json
{ "provider": "codex", "workspace_id": "project-a", "client_session_id": "feature-branch-auth" }
```

This generates `client_session_id = "uag_usr_01HXYZ...::project-a::feature-branch-auth"`, keeping each workstream's context separate while still reusing the same workspace-aware cache surface for that project.

---

## 5. Workspace Reset

### 5.1 What Reset Does

A workspace reset **wipes session state only**. It does not touch the files on disk in the user's workspace directory. Specifically:

1. All `SessionRegistry` entries where `client_session_id` starts with `{user_id}::` are deleted.
2. Any active CLI processes bound to those sessions receive SIGTERM (graceful) then SIGKILL after 5s.
3. A `workspace_resets` record is appended with the count of sessions wiped and the actor identity.
4. The workspace directory on disk is untouched — all files the agent has created or modified remain in place.

The rationale: files on disk represent work product. Losing them would be destructive and surprising. Session state (conversation history, cached prefixes) is ephemeral scaffolding — resetting it is the point.

### 5.2 Reset Endpoints

**User-initiated reset** (via user API or self-service portal):

```
POST /v1/user/workspace/reset
Authorization: Bearer <user_api_key>
```

Response:

```json
{
  "ok": true,
  "data": {
    "reset_id": "uag_rst_01HDEF...",
    "sessions_wiped": 3,
    "workspace_path": "/var/uag/workspaces/uag_usr_01HXYZ.../",
    "files_preserved": true,
    "reset_at": "2026-04-12T10:05:00Z"
  }
}
```

**Operator-initiated reset** (for support/triage):

```
POST /management/v1/users/:id/workspace/reset
Authorization: Bearer <operator_token>
```

Same response shape; `triggered_by` is set to `"operator"` in the `workspace_resets` log.

### 5.3 Effect on In-Flight Requests

If a reset is requested while a session has an active CLI execution:

1. The in-flight request completes its current turn (or hits its timeout).
2. The session is marked `status = 'expired'` in the registry immediately.
3. The response to the in-flight request includes `"warning": "session_reset_pending"`.
4. Once the turn completes, the session is deleted and the reset record written.

Reset does not interrupt an in-flight turn mid-execution. This prevents workspace corruption from a partially-applied diff.

---

## 6. User Self-Service API

All user-facing endpoints live under `/v1/user/`. They require a `user`-scoped API key (any active key belonging to the user). Users can only access their own data — there is no concept of user-to-user visibility.

### 6.1 Endpoint Reference

#### Identity & Keys

| Method | Path | Description |
|---|---|---|
| `GET` | `/v1/user/me` | Get own profile (user_id, email, display_name, workspace_path) |
| `GET` | `/v1/user/keys` | List own API keys (no raw key values) |
| `POST` | `/v1/user/keys` | Create a new API key |
| `DELETE` | `/v1/user/keys/:key_id` | Revoke a key (cannot revoke the key making the request) |

#### Sessions & Workspace

| Method | Path | Description |
|---|---|---|
| `GET` | `/v1/user/sessions` | List own sessions (filterable by status, label) |
| `GET` | `/v1/user/sessions/:id` | Get session detail and turn history |
| `POST` | `/v1/user/workspace/reset` | Reset workspace session state |
| `GET` | `/v1/user/workspace/resets` | List own reset history |

#### Usage

| Method | Path | Description |
|---|---|---|
| `GET` | `/v1/user/usage` | Own token usage, by day and provider |
| `GET` | `/v1/user/usage/sessions/:id` | Per-session token breakdown |

### 6.2 `GET /v1/user/usage` Response

```json
{
  "ok": true,
  "data": {
    "window": { "from": "2026-04-05", "to": "2026-04-12" },
    "summary": {
      "total_input_tokens": 142000,
      "total_output_tokens": 38000,
      "total_cache_hit_tokens": 108000,
      "cache_hit_rate": 0.76,
      "total_requests": 47
    },
    "by_day": [
      {
        "date": "2026-04-12",
        "provider": "codex",
        "input_tokens": 18000,
        "output_tokens": 5200,
        "cache_hit_tokens": 14400,
        "request_count": 6
      }
    ]
  }
}
```

---

## 7. Self-Service Portal

The self-service portal is a lightweight SPA served at `/portal/`. It is a **separate frontend** from the operator dashboard, with a distinct auth flow and a strictly limited data scope (users see only their own data).

### 7.1 Tech Stack

Shares the same stack recommendation as the operator dashboard (React 19 + TypeScript + Vite + TanStack Query + Tailwind CSS). The two SPAs can share component primitives but must not share routing or auth state.

### 7.2 Auth Flow

Users authenticate to the portal using their API key directly — there is no separate username/password login. On first load:

1. Portal presents an API key input field.
2. User pastes their key.
3. Portal calls `GET /v1/user/me` with the key.
4. On success, the key is stored in `sessionStorage` (not `localStorage`) and the portal loads.
5. On tab close, the session is cleared.

This keeps the portal stateless on the server side — no session cookies, no OAuth flows.

### 7.3 Pages

#### API Keys

Displays all active keys as a table with columns: Label, Key Prefix (e.g., `uagk_liv...`), Created, Last Used, Expires, Status. Actions: **Create new key**, **Revoke**.

Create new key opens a modal:
- Label (optional text input)
- Expiry (date picker or "No expiry")
- On confirm: calls `POST /v1/user/keys`, displays raw key in a one-time reveal modal with a copy button and a warning: *"This key will not be shown again."*

Revoke opens a confirmation dialog. If the user only has one active key, the revoke button is disabled with a tooltip: *"Create a new key before revoking your last one."*

#### Sessions

Filterable table of the user's own sessions: Label, Provider, Status, Last Active, Token Usage (input / output / cache). Clicking a row expands a detail panel showing:

- Turn history (index, tokens, finish reason, timestamp)
- Most recent diff output (if available)
- Session label and `backend_id` (for debugging)

No mutations available — users cannot terminate individual sessions. The only session mutation available to users is the full workspace reset.

#### Workspace

Shows:
- Current workspace path (read-only display)
- Active session count
- Dirty session count (if any, highlighted in amber)
- Reset history table: Date, Triggered By, Sessions Wiped

**Reset button:** Prominently placed. Clicking opens a confirmation dialog:

> *"This will clear all session history and conversation context for your workspace. Files on disk will not be affected. This cannot be undone."*

On confirm: calls `POST /v1/user/workspace/reset`, shows a success banner with the count of sessions wiped.

#### Usage

Token consumption charts matching the operator dashboard's Usage page, scoped to the current user:

- Tokens per day (input / output / cache-hit, stacked bar, last 7 days)
- Cache hit rate over time (line chart)
- Per-provider breakdown (stacked bar)
- Summary stats: total requests, total tokens, cache hit rate for the selected window

Time range selector: 7d / 30d.

---

## 8. Operator Management API (User Plane Extensions)

These extend the management API from the previous document with user-specific operations.

| Method | Path | Description |
|---|---|---|
| `GET` | `/management/v1/users` | List all users (paginated) |
| `GET` | `/management/v1/users/:id` | Get user detail |
| `POST` | `/management/v1/users` | Provision new user (returns raw key once) |
| `PATCH` | `/management/v1/users/:id` | Update display name, max_concurrency limit |
| `POST` | `/management/v1/users/:id/suspend` | Suspend user |
| `POST` | `/management/v1/users/:id/unsuspend` | Unsuspend user |
| `DELETE` | `/management/v1/users/:id` | Soft-delete user |
| `GET` | `/management/v1/users/:id/keys` | List user's API keys |
| `POST` | `/management/v1/users/:id/keys/rotate` | Rotate the user's single active key and reveal the replacement once |
| `DELETE` | `/management/v1/users/:id/keys/:key_id` | Revoke a non-active key record |
| `POST` | `/management/v1/users/:id/workspace/reset` | Operator-initiated workspace reset |
| `GET` | `/management/v1/users/:id/usage` | User's token usage (operator view) |

These endpoints appear on the operator dashboard's Users page, which is a new page to add to the dashboard spec from the previous document.

---

## 9. Operator Dashboard — Users Page (Addendum)

This page is an addendum to the UAG API & Dashboard Design document (§3.3).

**Table columns:**

| Column | Notes |
|---|---|
| User ID | Truncated, click to copy |
| Email | |
| Display Name | |
| Status | `active` / `suspended` / `deleted` badge |
| Active Keys | Count |
| Active Sessions | Count |
| Total Tokens (30d) | Aggregate from `user_usage` |
| Created | Relative time |
| Actions | Suspend, Reset Workspace, View Detail |

**User Detail Drawer:** Opens on row click, showing full profile, key list (masked prefixes only unless a fresh raw key was just revealed), session list, usage chart (last 30 days), and reset history.

**Provision User button:** Opens a modal matching the `POST /management/v1/users` request shape. On success, displays a one-time reveal of the raw API key with a copy button. The drawer only copies a usable raw key while that fresh reveal is still present in browser memory; otherwise it shows the masked prefix as an identifier and instructs the operator to rotate the key.

---

## 10. Security Considerations

**Key storage:** Raw API keys are never written to any persistent store. Only `SHA-256(raw_key)` is stored. If the database is compromised, keys cannot be recovered.

**Key transmission:** Raw keys are transmitted exactly once — in the provisioning or creation response over TLS. The response body must be delivered over HTTPS only; the Gateway must reject HTTP for any endpoint under `/v1/user/` and `/management/v1/`.

**Timing attacks:** The key validation path (§3.2) uses constant-time SHA-256 comparison. All rejection responses return `401` or `403` with identical response time regardless of whether the key exists, is revoked, or was never issued.

**Workspace path isolation:** `workspace_path` is resolved at provisioning time and stored in the database. At request time, the Gateway reads `workspace_path` from the database — the user cannot supply or influence their own workspace path. This makes path traversal attacks impossible from the user plane.

**Audit coverage:** All management mutations and all user-initiated mutations (key creation, key revocation, workspace reset) produce audit log entries. The audit log is append-only (§2.4 of the API & Dashboard Design document).

**Suspension propagation:** When a user is suspended, the rejection happens at the key validation step (§3.2) — the request never reaches the Orchestrator or the file system.

---

## 11. Implementation Priorities

In order:

1. **`users` and `api_keys` schema** — DDL in §1.1. Unblocks everything.
2. **Key validation middleware** — §3.2. Must be implemented before any user-plane endpoint is exposed.
3. **Workspace provisioning** — `POST /management/v1/users` with directory creation, key generation, one-time return of raw key.
4. **User self-service API** — `/v1/user/me`, `/v1/user/keys`, `/v1/user/workspace/reset`. Minimum viable self-service.
5. **Session namespacing** — `{user_id}::{session_label}` composition in §4.3. Required for isolation once real users exist.
6. **Usage accounting** — `user_usage` table writes in `UsageMonitor`. Required for the usage page and operator cost attribution.
7. **Self-service portal** — API Keys page first (most critical for users), then Workspace, Sessions, Usage.
8. **Operator dashboard Users page** — Addendum in §9. Builds on top of all the above.
