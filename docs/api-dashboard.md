# Design Specification: API Layer & Web Dashboard

## Unified Agent Gateway (UAG)

**Version:** 1.0  
**Status:** Design Draft  
**Depends On:** UAG SRDS v1.2  
**Audience:** Internal engineers and operators

---

## 0. Scope

This document covers two layers that sit above the UAG core runtime:

1. **The API Layer** — the full set of HTTP endpoints exposed by the Gateway, beyond the core `/v1/chat/completions` completion endpoint specified in SRDS §10. This includes management, observability, and control-plane APIs.
2. **The Web Dashboard** — an operator-facing UI for monitoring system health, managing accounts and sessions, and diagnosing failures.

These two layers are designed together because the dashboard is a first-party consumer of the management API. Every panel in the dashboard maps to an explicit API endpoint; there are no dashboard-internal data sources.

---

## 1. Design Principles

- **Dashboard = API client.** The dashboard has no privileged backend access. Every piece of data it displays is available via the management API, making the API independently useful for scripting and external tooling.
- **Read-heavy, write-sparse.** The management API is primarily observability. Mutations (account addition, session termination) are rare and require explicit operator intent.
- **Ops first.** The dashboard is designed for an operator diagnosing a problem at 2am, not for onboarding. Density and precision over visual polish.
- **Audit everything.** All state-mutating API calls are logged to an immutable audit trail with actor identity, timestamp, and before/after state.

---

## 2. API Layer Design

### 2.1 Base URL & Versioning

All management endpoints are namespaced under `/management/v1/` to distinguish them from the inference-compatible `/v1/` namespace.

```
/v1/chat/completions          ← inference API (SRDS §10)
/management/v1/*              ← management API (this document)
/dashboard/*                  ← static dashboard assets (served by Gateway)
```

Versioning is path-based. Breaking changes increment the version prefix. Both versions are served concurrently during transition periods (minimum 30 days).

### 2.2 Authentication

All `/management/v1/` endpoints require a bearer token with an `operator` scope. This is distinct from the `user` scope used by inference API clients.

```
Authorization: Bearer <operator_token>
```

Tokens are issued via `POST /management/v1/auth/token` with a service account credential. Token TTL is 8 hours. Refresh is supported via `POST /management/v1/auth/refresh`.

The dashboard authenticates via the same mechanism. On first load, if no valid token is present in `sessionStorage`, the dashboard redirects to a login page that exchanges credentials for a token.

### 2.3 Common Response Envelope

All management API responses follow a consistent envelope:

```json
{
  "ok": true,
  "data": { ... },
  "meta": {
    "request_id": "req_01J...",
    "timestamp": "2026-04-12T10:00:00Z",
    "page": { "cursor": "...", "has_more": true }
  }
}
```

Errors:

```json
{
  "ok": false,
  "error": {
    "code": "session_not_found",
    "message": "No session with id 'abc123' exists.",
    "request_id": "req_01J..."
  }
}
```

Pagination uses cursor-based pagination throughout. Offset pagination is not supported.

### 2.4 Endpoint Reference

#### System Health

|Method|Path|Description|
|---|---|---|
|`GET`|`/management/v1/health`|Overall system health: gateway, orchestrator, state store|
|`GET`|`/management/v1/overview`|Combined overview payload used by the dashboard home page|
|`GET`|`/management/v1/health/providers`|Per-provider reachability and latency|
|`GET`|`/management/v1/metrics`|Prometheus-compatible metrics scrape endpoint|

**`GET /management/v1/health` response:**

```json
{
  "ok": true,
  "data": {
    "status": "degraded",
    "components": {
      "gateway":      { "status": "ok",      "latency_ms": 1 },
      "orchestrator": { "status": "ok",      "latency_ms": 3 },
      "state_store":  { "status": "ok",      "latency_ms": 8 },
      "redis":        { "status": "degraded","latency_ms": 420, "message": "high latency" }
    },
    "checked_at": "2026-04-12T10:00:00Z"
  }
}
```

Overall `status` is `"ok"` only if all components are `"ok"`. Otherwise `"degraded"` or `"down"`.

---

#### Session Management

|Method|Path|Description|
|---|---|---|
|`GET`|`/management/v1/sessions`|List all sessions (paginated, filterable)|
|`GET`|`/management/v1/sessions/:id`|Get session detail|
|`DELETE`|`/management/v1/sessions/:id`|Terminate and evict a session|
|`POST`|`/management/v1/sessions/:id/reset`|Reset session to clean state (clears dirty flag)|
|`GET`|`/management/v1/sessions/:id/turns`|Get turn history for a session|

**Query parameters for `GET /management/v1/sessions`:**

|Param|Type|Description|
|---|---|---|
|`provider`|`string`|Filter by provider: `codex`, `gemini`, `opencode`|
|`status`|`string`|Filter by status: `idle`, `active`, `dirty`, `expired`|
|`workspace`|`string`|Filter by `cwd_path` prefix|
|`after`|`cursor`|Pagination cursor|
|`limit`|`int`|Results per page (default 50, max 200)|

---

#### Workspace Management

|Method|Path|Description|
|---|---|---|
|`GET`|`/management/v1/workspaces`|List managed workspaces under the provisioned workspaces root|
|`GET`|`/management/v1/workspaces/:id`|Get workspace detail, git metadata, bound sessions, and bound users|
|`POST`|`/management/v1/workspaces/:id/reset`|Wipe sessions bound to the workspace subtree while preserving files|
|`DELETE`|`/management/v1/workspaces/:id`|Delete the workspace directory from disk and wipe bound sessions|

Workspace records include:
- the resolved workspace path and relative path under `workspaces_root`
- scope classification (`base`, `subworkspace`, `orphan`)
- git metadata when the workspace is a git repository (branch, HEAD commit, remote, dirty state)
- bound users and bound sessions for the workspace subtree

---

#### Account Pool Management

|Method|Path|Description|
|---|---|---|
|`GET`|`/management/v1/accounts`|List all accounts in the pool|
|`GET`|`/management/v1/accounts/:id`|Get account detail + current usage metrics|
|`POST`|`/management/v1/accounts`|Register a new account credential|
|`POST`|`/management/v1/accounts/upload`|Register/update account with uploaded credential payload (`multipart/form-data`)|
|`POST`|`/management/v1/accounts/:id/select`|Mark CLI-primary and materialize credential into provider CLI auth path|
|`DELETE`|`/management/v1/accounts/:id`|Remove an account from the pool|
|`POST`|`/management/v1/accounts/:id/cooldown`|Manually force an account into COOLDOWN|
|`POST`|`/management/v1/accounts/:id/recover`|Manually release an account from COOLDOWN|

`POST /v1/chat/completions` and `POST /management/v1/playground/chat` both
accept either JSON or `multipart/form-data`. For multipart turns, send the JSON
request as `payload` and attach one or more files; the gateway stages them into
the bound workspace and forwards their relative paths to the provider CLI in an
injected system message.

**`POST /management/v1/accounts` request body:**

```json
{
  "provider": "codex",
  "auth_type": "API_KEY",
  "credential": "sk-...",
  "label": "codex-prod-01"
}
```

Credentials are encrypted at rest using AES-256-GCM. The raw credential is never returned in any response after creation — only a masked form (e.g., `sk-...vX9q`).

**`POST /management/v1/accounts/upload` form fields:**

|Field|Type|Required|Description|
|---|---|---|---|
|`provider`|`string`|yes|`codex`, `gemini`, `opencode`|
|`auth_type`|`string`|yes|`API_KEY` or `OAUTH_SESSION`|
|`label`|`string`|yes|Operator-visible label|
|`account_id`|`string`|no|If absent, backend auto-generates one|
|`credential_text`|`string`|conditional|Inline credential payload (key, `auth.json`, `oauth_creds.json`)|
|`credential_file`|`file`|conditional|Credential file upload (`.json`/`.txt`)|

Exactly one of `credential_text` or `credential_file` must be provided.

Credential storage and activation behavior:
- Uploaded credentials are encrypted in SQLite and also persisted in the local vault directory: `~/.config/codara/credentials/<provider>/<account_id>.cred`.
- The vault/SQLite registry is the only account inventory source shown by the dashboard. Provider auth files are not treated as separate accounts.
- Selecting an account for CLI use copies its credential into the provider auth path:
`codex` → `~/.codex/auth.json`, `gemini` → `~/.gemini/oauth_creds.json`, `opencode` → `~/.opencode/auth.json`.
- Target auth paths can be overridden with `UAG_CODEX_AUTH_PATH`, `UAG_GEMINI_AUTH_PATH`, and `UAG_OPENCODE_AUTH_PATH`.
- Automatic routing keeps one CLI-primary account active at a time and promotes the next healthiest ready account when the active one reaches 5% remaining headroom or less.

---

#### Observability

|Method|Path|Description|
|---|---|---|
|`GET`|`/management/v1/usage`|Aggregated token/request usage across all accounts|
|`POST`|`/management/v1/usage/refresh`|Force immediate usage resync across account pool|
|`GET`|`/management/v1/usage/accounts/:id`|Per-account usage time series|
|`GET`|`/management/v1/usage/sessions/:id`|Per-session token consumption breakdown|
|`GET`|`/management/v1/audit`|Paginated audit log of all management mutations|

---

#### Audit Log

All state-mutating management API calls produce an immutable audit entry. The audit log is append-only and cannot be modified or deleted via any API endpoint.

**`GET /management/v1/audit` response entry:**

```json
{
  "audit_id": "aud_01J...",
  "actor": "operator:svc-account-01",
  "action": "session.terminated",
  "target_type": "session",
  "target_id": "abc123",
  "before": { "status": "dirty" },
  "after":  { "status": "terminated" },
  "request_id": "req_01J...",
  "timestamp": "2026-04-12T10:02:00Z"
}
```

---

### 2.5 Rate Limiting

Management API endpoints are rate-limited separately from the inference API. Default limits:

|Endpoint Class|Limit|
|---|---|
|Read endpoints (`GET`)|600 req/min per token|
|Write endpoints (`POST`, `DELETE`)|60 req/min per token|
|Dashboard polling|15-30s refresh with standard query limits, depending on page volatility|
|Metrics scrape (`/metrics`)|60 req/min (no auth required from internal network)|

---

## 3. Web Dashboard Design

### 3.1 Technology Decision

The dashboard is a **single-page application**. Recommended stack:

|Concern|Recommendation|Rationale|
|---|---|---|
|Framework|**React 19 + TypeScript**|Largest operator tooling ecosystem; strong typing for API contract enforcement|
|Build|**Vite**|Fast dev iteration; no config overhead|
|Data fetching|**TanStack Query v5**|Stale-while-revalidate and polling in one library|
|Charts|**Recharts**|Composable, TypeScript-native, no canvas complexity|
|Styling|**Tailwind CSS**|Utility-first, no runtime overhead, consistent density|
|Tables|**TanStack Table v8**|Virtualized rows for large session lists; column-level filtering|

This is a recommendation, not a mandate. Vue 3 + Vite is an equivalent alternative. The key constraint is that the framework must handle **large paginated tables** without DOM thrashing and support responsive polling-based refreshes.

### 3.2 Layout

The dashboard uses a fixed left sidebar navigation with a main content area. No nested navigation deeper than two levels.

```
┌─────────────────────────────────────────────────────────────┐
│  UAG Operator Dashboard            [health indicator] [user] │
├──────────────┬──────────────────────────────────────────────┤
│              │                                              │
│  Overview    │                                              │
│  Sessions    │         Main Content Area                    │
│  Accounts    │                                              │
│  Providers   │                                              │
│  Usage       │                                              │
│  Audit Log   │                                              │
│              │                                              │
└──────────────┴──────────────────────────────────────────────┘
```

A persistent **system health badge** in the top bar shows `OK / DEGRADED / DOWN` at a glance, driven by the current health poll. Clicking it navigates to the Overview page.

### 3.3 Pages

#### Overview (Home)

The landing page. Purpose: answer "is anything on fire right now?" in under 5 seconds.

**Panels:**

|Panel|Data Source|Refresh|
|---|---|---|
|System health breakdown (component grid)|`GET /health`|30s poll|
|Active sessions count|`GET /sessions?status=active`|15s poll|
|Dirty sessions count (requires attention)|`GET /sessions?status=dirty`|15s poll|
|Accounts in cooldown|`GET /accounts` filtered|15s poll|
|Semaphore utilization gauge (active / max)|`GET /metrics`|10s poll|
|Recent state summary|`GET /sessions`, `GET /accounts`|15s poll|

The dirty sessions count and accounts-in-cooldown counts link directly to the filtered view of their respective pages.

---

#### Sessions

A dense, filterable table of all sessions. Designed for fast triage.

**Table columns:**

|Column|Source Field|Notes|
|---|---|---|
|Session ID|`client_session_id`|Truncated, click to copy full value|
|Provider|`provider`|Colored badge|
|Status|`status`|`idle` / `active` / `dirty` / `expired` with color coding|
|Workspace|`cwd_path`|Truncated path, hover for full|
|Last active|`updated_at`|Relative time ("3m ago")|
|Token cache|`prefix_hash` match|Hit / Miss badge from recent turn data|
|Actions|—|Terminate, Reset|

**Filters:** Provider, Status, Workspace prefix (text search), Last active (time range).

**Session Detail Drawer:** Clicking a row opens a side drawer (not a new page) showing:

- Full session metadata
- Turn history table: turn index, input tokens, output tokens, finish reason, duration, timestamp
- Workspace diff viewer for the most recent turn (unified diff with syntax highlighting)
- Raw `backend_id` (for manual provider-side debugging)
- Audit log entries scoped to this session

---

#### Accounts

Account pool management. Split into two tabs: **Active** and **Cooldown**.

**Active tab columns:**

|Column|Notes|
|---|---|
|Label|Human-readable name set at registration|
|Provider|Colored badge|
|Auth Type|`API_KEY` / `OAUTH_SESSION`|
|TPM Used / Limit|Progress bar|
|RPD Used / Limit|Progress bar|
|Sessions Bound|Count of sessions currently using this account|
|Actions|Force cooldown, Remove|

**Cooldown tab:** Shows accounts currently in COOLDOWN with time remaining and reason (e.g., `"429 from provider"`). Each row has a **Recover** action for manual override.

**Add Account modal:** Form with fields for Provider, Auth Type, Credential (password input), and Label. Submits to `POST /management/v1/accounts`. On success, the new account appears in the table; the credential is never shown again.

---

#### Providers

Health and latency view per provider. One card per provider (Codex, Gemini, OpenCode).

**Per-provider card content:**

- Reachability status badge
- P50 / P95 adapter latency (last 5 minutes), sourced from `/metrics`
- Active sessions count on this provider
- Accounts available / in cooldown on this provider
- Last health check timestamp

This page is entirely read-only. No mutations. It auto-refreshes via polling.

---

#### Usage

Time-series token consumption charts for capacity planning and cost attribution.

**Charts:**

|Chart|X-axis|Y-axis|Granularity|
|---|---|---|---|
|Total tokens/hour across all providers|Time|Tokens|1h buckets, last 24h|
|Per-provider token breakdown|Time|Tokens (stacked)|1h buckets, last 24h|
|Per-account RPD utilization|Account|% of RPD limit used|Current day|
|Cache hit rate over time|Time|Hit %|1h buckets, last 24h|

All charts use a time range selector (1h / 6h / 24h / 7d). The 7d view uses 6h buckets.

Data source: `GET /management/v1/usage` with `?granularity=1h&window=24h` query params.

---

#### Audit Log

Append-only, paginated table of all management mutations.

**Columns:** Timestamp, Actor, Action, Target Type, Target ID, Request ID.

Clicking a row expands an inline detail view showing the full `before` / `after` JSON diff.

**Filters:** Actor, Action type, Target type, Time range. No delete or export — operators should use direct DB access for bulk audit export.

---

### 3.4 Live Update Strategy

The dashboard uses polling for management state. It no longer maintains a persistent event-stream connection.

**Polling cadence:** Pages that show aggregate metrics use TanStack Query polling at intervals defined per-panel (10s for semaphore, 30s for health). Dashboard data is stale-safe by design and avoids streaming dependencies.

---

### 3.5 Error States

Every panel and table has an explicit error state (not just empty state). Error states show:

- The specific API call that failed
- The HTTP status and error code from the response envelope
- A **Retry** button that re-triggers the query
- A link to the Audit Log filtered to recent entries (in case the error is caused by a concurrent mutation)

Empty states (zero results, not errors) are distinct from error states. An empty Sessions table shows "No sessions match your filters" with a clear filters button, not an error message.

---

## 4. API ↔ Dashboard Mapping

This table is the authoritative cross-reference between dashboard panels and their API dependencies. Any API change that affects a field in this table requires a corresponding dashboard update.

|Dashboard Page / Panel|API Endpoint(s)|Update Mechanism|
|---|---|---|
|Overview — health grid|`GET /health`|30s poll|
|Overview — active sessions|`GET /sessions?status=active`|15s poll|
|Overview — dirty sessions|`GET /sessions?status=dirty`|15s poll|
|Overview — accounts in cooldown|`GET /accounts`|15s poll|
|Overview — semaphore gauge|`GET /metrics`|10s poll|
|Overview — events feed|Removed|n/a|
|Sessions — table|`GET /sessions`|15s poll + manual refresh|
|Sessions — detail drawer|`GET /sessions/:id`, `GET /sessions/:id/turns`|On open|
|Sessions — terminate|`DELETE /sessions/:id`|Mutation|
|Sessions — reset|`POST /sessions/:id/reset`|Mutation|
|Accounts — active tab|`GET /accounts`|15s poll + manual refresh|
|Accounts — cooldown tab|`GET /accounts` filtered|15s poll|
|Accounts — force cooldown|`POST /accounts/:id/cooldown`|Mutation|
|Accounts — recover|`POST /accounts/:id/recover`|Mutation|
|Accounts — add|`POST /accounts`|Mutation|
|Accounts — remove|`DELETE /accounts/:id`|Mutation|
|Providers — cards|`GET /usage`, `GET /metrics`|30s poll|
|Usage — all charts|`GET /usage`, `GET /usage/accounts/:id`|Time-range poll|
|Audit Log — table|`GET /audit`|Manual refresh + time-range filter|

---

## 5. Implementation Priorities

In order:

1. **Core management API** — `GET /health`, `GET /sessions`, `GET /accounts`. These unblock all dashboard development.
2. **Account mutation endpoints** — `POST /accounts`, `DELETE /accounts`, cooldown/recover. Required for ops to manage the pool without direct DB access.
3. **Session mutation endpoints** — `DELETE /sessions/:id`, `POST /sessions/:id/reset`. Required for triage workflows.
4. **Dashboard scaffold** — Layout, auth flow, Overview page wired to live data.
5. **Sessions page** — Table + detail drawer + diff viewer.
6. **Accounts page** — Active/Cooldown tabs + Add Account modal.
7. **Providers page** — Read-only, straightforward once health endpoints exist.
9. **Usage page** — Charts require `GET /usage` time-series data; implement last as it has the most backend aggregation work.
10. **Audit log page** — Low complexity once the audit trail is written by the mutation endpoints.
Implementation notes for the current codebase:

- The Overview page uses `GET /management/v1/overview` instead of fetching full session/account lists just to compute summary cards.
- Accounts, Sessions, and Audit Log all use cursor pagination to keep large datasets responsive.
- Audit Log includes text search plus actor/action/target filters.
- System-derived CLI auth rows are hidden from the visible account inventory unless they are explicitly registered into the vault.
