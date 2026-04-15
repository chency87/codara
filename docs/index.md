# System Design Specification: Unified Agent Gateway (UAG)

**Version:** 1.2  
**Status:** Engineering Final  
**Core Objective:** To bridge stateless API clients with stateful, tool-augmented CLI agents (Codex, Gemini, OpenCode) while minimizing token costs through aggressive session reuse and workspace-aware caching.

---

## 0. Design Principles

- **Stateful over Stateless** — optimize token reuse across turns
- **Provider Abstraction** — uniform OpenAI-compatible API surface
- **Workspace as Source of Truth** — file system is an external state component
- **Fail Soft** — graceful degradation on provider failure
- **Cost-Aware Execution** — token frugality is a first-class concern

---

## 1. Architectural Overview

The UAG operates as a **Stateful Provider-Adapter Middleware**. Unlike a standard proxy that merely forwards packets, the UAG maintains a persistent Session State and interacts with the local file system to provide a unified OpenAI-compatible interface.

### 1.1 Component Stack

## 2. Request Lifecycle

This is the canonical flow every request follows, end to end:

```
1.  Client Request     →  Gateway (auth, parse uag_options)
2.  Session Lookup     →  SessionRegistry (resume or create)
3.  Account Selection  →  AccountPool for Codex, system-local provider policy for Gemini/OpenCode
4.  Dispatch           →  Orchestrator (acquire semaphore, bind worker)
5.  Translation        →  Adapter Layer (JSON → provider protocol)
6.  CLI Execution      →  Spawned subprocess (Codex / Gemini / OpenCode)
7.  Workspace Diff     →  File-System Engine (git diff or hash check)
8.  ATR (optional)     →  Action Translation & Reconstruction Module
9.  Response Assembly  →  Inject diff/actions into response extensions
10. State Persistence  →  Write updated backend ID to SessionRegistry
```

---

## 3. Orchestrator Runtime Engine

The Orchestrator is the most critical component for system stability. It manages the lifecycle of CLI-based agents through a structured supervisor-worker model.

### 3.1 Concurrency Model

The UAG uses a **Hybrid Async-Worker Model**:

- **Control Plane (Gateway):** A FastAPI application handles incoming HTTP requests.
- **Data Plane (Task Runner):** Provider CLIs are spawned as asyncio subprocesses from the Python orchestrator.
    - **Per-Session Binding:** Each `client_session_id` is bound to a specific worker. Concurrent requests to the same session are serialized via a per-session mutex.
    - **Semaphore-Gated Concurrency:** A global semaphore limits the number of _active_ agent executions. Inactive (idling/resumed) sessions remain in the `SessionRegistry` without consuming semaphore slots.

### 3.2 Failure Handling

|Failure Mode|Response|
|---|---|
|Execution timeout (configurable, default 120s)|SIGKILL subprocess; mark output `"status": "incomplete"`|
|Workspace lock not released (node crash)|Lock has a TTL (default 300s); expired locks are force-released by the next session resumption using a fencing token|
|Malformed adapter response|Log and return `502` with `"error": "adapter_parse_failure"`; workspace is not modified|
|Workspace permission error|Return `403` with `"error": "workspace_access_denied"` before dispatch|
|Partial CLI output|Return partial content with `"status": "partial"`; workspace flagged `"dirty": true`|

### 3.3 Workspace Concurrency

- **Single-writer per workspace** — enforced with the workspace engine's `.uag_lock` file.
- **Lock fencing:** Each lock acquisition increments a monotonic `fence_token`. Stale writes from a crashed node are rejected if their `fence_token` is lower than the current value.
- **External modification detection** — hash mismatch between pre/post execution surfaced to client as `"warning": "external_modification_detected"`.

---

## 4. Account & Identity Management

### 4.1 AccountPool

Codex credentials are managed through the centralized `AccountPool`. Gemini and OpenCode run through the locally installed CLI using the host user's existing login state instead of uploaded pool-managed credentials.

Automatic selection follows a simple default policy:

1. Keep exactly one active CLI-primary account per provider.
2. Continue routing new work to that active account while it has more than 5% remaining headroom and is otherwise healthy.
3. If the active account cools down, expires, rate-limits, or drops to 5% headroom or less, promote the healthiest ready account to become the new CLI-primary account.
4. Keep the vault/SQLite registry as the inventory source of truth; provider CLI auth files are only activation targets for the currently active runtime identity.

**Supported auth types:**

|Type|Description|
|---|---|
|`OAUTH_SESSION`|Browser-based session token|
|`API_KEY`|Standard programmatic key|

### 4.2 Rate Limit Handling

- **Leaky Bucket per account:** Each account tracks its `TPM` (Tokens Per Minute) and `RPD` (Requests Per Day) locally.
- **429 handling:** On a `429` response, the account moves to `COOLDOWN` state for a backoff period (exponential, capped at 60s). The request is transparently retried with the next available identity. Target recovery latency: **< 2.0s**.
- **UsageMonitor:** A background task polls provider billing APIs on a configurable interval to sync usage metrics.

**Provider billing API endpoints:**

|Provider|Endpoint|
|---|---|
|Codex|`https://chatgpt.com/backend-api/wham/usage` (primary OAuth quota endpoint in the current runtime)|
|Gemini|Configured WHAM/usage endpoints from `UAG_GEMINI_USAGE_ENDPOINTS`|
|OpenCode|No background billing sync endpoint is implemented today|

Billing sync prefers the stored OAuth session token for the account. If a usable session token is not present, the monitor falls back to the configured billing key or an API-key account credential:
- `UAG_CODEX_BILLING_API_KEY` for Codex usage polling fallback.
- `UAG_GEMINI_BILLING_API_KEY` for Gemini usage polling fallback.
On Codex OAuth `401/403`, the monitor attempts a refresh-token grant and retries usage fetch once before marking the account as expired.

Account credentials are persisted in the local vault directory (`~/.config/codara/credentials`) and encrypted in SQLite. When an operator selects a CLI-primary account, UAG materializes that credential into the provider-specific auth path used by the local CLI runtime.

---

## 5. Stateful Session Management

The core value of UAG is transforming ephemeral API calls into a continuous Conversation Thread.

### 5.1 SessionRegistry Schema

```sql
CREATE TABLE sessions (
    client_session_id  TEXT        PRIMARY KEY,
    backend_id         TEXT        NOT NULL,          -- thread_id (Codex) or checkpoint_id (Gemini)
    provider           TEXT        NOT NULL,          -- 'codex' | 'gemini' | 'opencode'
    account_id         TEXT        NOT NULL REFERENCES accounts(account_id),
    cwd_path           TEXT        NOT NULL,
    prefix_hash        TEXT        NOT NULL,          -- SHA-256(system_prompt || file_tree_metadata)
    status             TEXT        NOT NULL DEFAULT 'idle',  -- 'idle' | 'active' | 'dirty' | 'expired'
    fence_token        INTEGER     NOT NULL DEFAULT 0,
    created_at         INTEGER     NOT NULL,          -- Unix epoch ms
    updated_at         INTEGER     NOT NULL,
    expires_at         INTEGER     NOT NULL           -- Unix epoch ms; enforced by UsageMonitor GC
);

CREATE INDEX idx_sessions_account ON sessions(account_id);
CREATE INDEX idx_sessions_expires ON sessions(expires_at);
CREATE INDEX idx_sessions_cwd     ON sessions(cwd_path);
```

**Eviction policy:** Sessions with `expires_at < now()` are deleted by a background GC task running every 60s. Default TTL is 24 hours from `updated_at`.

### 5.2 Thread Lifecycle

1. **Context Resumption:** If a `client_session_id` exists in the registry, the gateway performs a `thread/resume`. This triggers prefix caching, reducing input token costs by up to **90%**.
2. **State Persistence:** On turn completion, the updated `backend_id` is written back to the registry. This allows a user to switch between CLI and Web UI without losing context.
3. **Dirty State:** If a turn ends abnormally (timeout, SIGKILL), the session status is set to `dirty`. The next request on that session will prompt the client with a `"warning": "session_dirty"` and offer resume or reset options.

---

## 6. Adapter Layer

Each provider requires a dedicated adapter module that translates the standard UAG JSON payload into the provider's native protocol. The adapter interface is:

```python
from typing import Protocol

class ProviderAdapter(Protocol):
    async def send_turn(self, session: "Session", messages: list["Message"]) -> "TurnResult": ...
    async def resume_session(self, backend_id: str) -> "Session": ...
    async def terminate_session(self, backend_id: str) -> None: ...
```

### 6.1 Codex Adapter (CLI exec/resume)

**Protocol:** `codex exec --json` / `codex exec resume --json` over stdio JSONL events.

**Handshake sequence:**

```
1. Spawn new turn: `codex exec --json --cwd <cwd_path> <prompt>`
2. Resume turn: `codex exec resume <backend_id> --json --cwd <cwd_path> <prompt>`
3. Stream JSONL events such as `thread.started`, `turn.started`, `item.completed`, `turn.completed`
4. Persist the returned thread identifier as the next `backend_id`
```

**Error surface:**

|JSON-RPC Error Code|Meaning|UAG Action|
|---|---|---|
|`-32001`|Thread not found|Create new session|
|`-32002`|Context overflow|Return provider error to client/operator|
|`-32003`|Rate limit|Move account to COOLDOWN, retry with next|
|`-32099`|Internal server error|Return `502` to client|

### 6.2 Gemini Adapter (CLI)

**Protocol:** local `gemini` CLI execution.

**Handshake sequence:**

```
1. Spawn `gemini --yolo --output-format json`
2. Pass `--model <model>` and optional `--resume <backend_id>`
3. Provide the rendered prompt with `--prompt`
4. Parse JSON output for `session_id`, `response`, and token usage
```

**Error surface:**

|CLI/Error Surface|Meaning|UAG Action|
|---|---|---|
|Missing CLI binary|Gemini CLI not installed|Return runtime error to client/operator|
|Auth/login error text|Local Gemini CLI is not logged in|Return runtime error to client/operator|
|`429` / rate-limit text|Provider capacity or rate limit exhausted|Return quota/rate-limit error to client|

### 6.3 OpenCode Adapter (CLI)

**Protocol:** local `opencode run` CLI execution.

**Handshake sequence:**

```
1. Spawn `opencode run --format json --dir <cwd>`
2. Pass `--model <provider/model>` and optional `--session <backend_id>`
3. Stream or collect JSON events from stdout
4. Recover `session_id`, assistant output, and usage from the event stream
```

**Error surface:**

|CLI/Error Surface|Meaning|UAG Action|
|---|---|---|
|Missing CLI binary|OpenCode CLI not installed|Return runtime error to client/operator|
|Auth/login error text|Local OpenCode CLI is not logged in|Return runtime error to client/operator|
|`429` / rate-limit text|Provider capacity or rate limit exhausted|Return quota/rate-limit error to client|

---

## 7. Runtime Context Handling

- **Session reuse only:** the runtime preserves provider-local `backend_id` values per persisted session so the next turn can resume the same CLI conversation when the provider supports it.
- **Prefix hash bookkeeping:** the session table stores a hash of the current workspace tree, but the live runtime does not yet use that hash to rewrite prompts or drive a cache-optimization path.
- **No built-in summarizer/compression:** the submitted message list is passed through as-is; there is no automatic semantic compression stage.

---

## 8. Workspace & File-System Engine

### 8.1 Edit Extraction & Diffs

Because CLI tools modify files directly, the UAG must "close the loop" to return code changes to the client.

- **Snapshot:** File hashes captured immediately before CLI execution begin.
- **Watcher Pattern:** User-bound workspaces are initialized as local git repositories with a valid `HEAD`, so after CLI termination UAG can usually run `git diff HEAD`; other workspaces still fall back to recursive file hash comparison when needed.
- **Patch Payload:** Captured changes are converted to Unified Diff format and injected into the response:

```json
{
  "extensions": {
    "modified_files": ["src/main.rs"],
    "diff": "--- a/src/main.rs\n+++ b/src/main.rs\n@@ -1,4 +1,5 @@\n ...",
    "dirty": false
  }
}
```

### 8.2 Sandboxing & Security

- **Safe Zones:** All `workspace_path` inputs must be subdirectories of a whitelist configured in `UAG_ROOT_DIR`. Requests outside this boundary are rejected with `403` before dispatch.
- **Path Sanitization:** All `workspace_path` values are resolved with `std::fs::canonicalize` before comparison to prevent `../` traversal.
- **Safe Zone Validation:** Performed at request ingress, before the session lookup step.

---

## 9. Action Translation & Reconstruction (ATR) Module

The ATR Module enables a "Manual Update" workflow by decoupling model output from file-system execution.

- **Extraction:** Captures internal rollout logs from CLI stdout and translates them into Actionable Payloads.
- **Output Formats:**
    - **JSON Actions:** normalized into exact operations like `{"type": "write_file", "path": "index.ts", "content": "..."}`
    - **Aider-Style Blocks:** normalized into `search_replace` patch actions with explicit `path`, `search`, and `replace` fields
    - **Unified Diffs:** normalized into patch actions that preserve the exact diff payload
- **Dry-Run Validation:** ATR only returns exact search/replace actions when it can identify the target path and operation fields cleanly.
- **Workspace Diff Translation:** When a turn changes files in a git-backed workspace, the captured `git diff HEAD` patch is also normalized into ATR patch actions so clients receive exact file operations even if the assistant response itself was not structured as actions.
- **Mode Toggle:** Controlled by `"manual_mode": true` in `uag_options`. When `true`, the Workspace Engine skips diff capture and returns only ATR actions extracted from the assistant output.

---

## 10. API Interface Specification

### `POST /v1/chat/completions`

Standard OpenAI-compatible request, augmented with `uag_options`. User clients
must send their provisioned API key in the standard bearer header:

```http
Authorization: Bearer uagk_live_...
```

Request body:

```json
{
  "model": "uag-codex-v5",
  "messages": [
    {"role": "user", "content": "Refactor the auth module to use JWT."}
  ],
  "uag_options": {
    "provider": "codex",
    "workspace_id": "project-a",
    "client_session_id": "thread-1"
  }
}
```

The endpoint also accepts `multipart/form-data` when a turn needs local files.
Send the same JSON body as a string form field named `payload`, plus one or
more uploaded files. The gateway stages those files under the bound workspace at
`.uag/uploads/<session-scope>/...` and injects a system message that tells the
target CLI where to find them.

**`uag_options` field reference:**

|Field|Type|Default|Description|
|---|---|---|---|
|`provider`|`"codex" \| "gemini" \| "opencode"`|required|Target provider|
|`workspace_root`|`string`|`null`|Operator/internal only. For user API keys the gateway resolves this automatically from the provisioned workspace|
|`workspace_id`|`string`|`null`|Optional logical sub-workspace selector. Use it when the user wants multiple isolated work areas under the provisioned base workspace|
|`session_persistence`|`bool`|`true`|Advanced toggle for session reuse; most clients should use the default|
|`manual_mode`|`bool`|`false`|Advanced ATR-only mode that returns actions instead of applying diffs|
|`client_session_id`|`string`|auto-generated UUID|Session identity for resumption|

---

## 11. Distributed Deployment

### 11.1 Scaling Strategy

UAG instances are stateless compute nodes. All state is externalized:

|Environment|Session Store|Hot Cache|
|---|---|---|
|Development|SQLite (local)|—|
|Production|Postgres (durable)|Redis (hot sessions)|

### 11.2 Session Routing

Requests **must** be routed by `client_session_id` to reach the correct state. Options:

- **Sticky sessions** (simple): Load balancer affinity by session ID. Suitable for moderate scale.
- **Consistent hashing** (preferred): Routes by hash of `client_session_id`. Tolerates node addition/removal with minimal session disruption.

### 11.3 Failure Recovery

On node failure, sessions are recovered from the State Store:

1. New node receives request with `client_session_id`.
2. SessionRegistry lookup succeeds (state is in Postgres/Redis).
3. Orchestrator restarts the CLI process and resumes from `backend_id`.
4. If `fence_token` in registry is higher than the lock held by the failed node, the stale lock is invalidated.

---

## 12. Performance & Success Metrics

|Metric|Goal|Measurement Method|
|---|---|---|
|**Token Cache Hit Rate**|> 80%|`prefix_hash` match rate logged per session in SessionRegistry|
|**Recovery Latency**|< 2.0s|Time from `429` receipt to first byte of retry response|
|**Context Fidelity**|100%|Golden diff test suite: 50 fixtures with known pre/post file states|
|**Protocol Overhead**|< 100ms|P95 latency delta: UAG-proxied vs. direct CLI execution, measured in CI|

---

## 13. Implementation Priorities

The two primary engineering milestones, in order:

1. **`SessionRegistry` + SQLite schema** — unblocks all stateful session work. DDL is in §5.1.
2. **Codex local CLI adapter** — highest-priority provider; wire isolated credential materialization into orchestrator dispatch.

Subsequent priorities were Gemini local CLI support, OpenCode local CLI support, and ATR iteration.
