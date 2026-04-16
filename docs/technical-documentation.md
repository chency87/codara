# Unified Agent Gateway (UAG) - Technical Documentation

**Version:** 1.2  
**Status:** Engineering Final  
**Last Updated:** 2026-04-14

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture](#2-architecture)
3. [Core Components](#3-core-components)
4. [Data Flow & Workflows](#4-data-flow--workflows)
5. [Database Schema](#5-database-schema)
6. [API Reference](#6-api-reference)
7. [Security Model](#7-security-model)
8. [Deployment](#8-deployment)
9. [Operations & Monitoring](#9-operations--monitoring)

---

## 1. System Overview

### 1.1 Purpose

The Unified Agent Gateway (UAG) is a **stateful middleware** that bridges stateless OpenAI-compatible API clients with stateful, tool-augmented CLI agents (Codex, Gemini, OpenCode). It provides:

- **Persistent Sessions**: Maintains conversation context across API calls via the `/v1/chat/completions` endpoint
- **Account Pool Management**: Routes requests across multiple provider accounts with quota awareness
- **Workspace Isolation**: Manages file-system operations for multi-tenant environments
- **Session Reuse**: Persists provider-local runtime state and workspace metadata across compatible turns

### 1.2 Design Principles

| Principle | Description |
|-----------|-------------|
| **Stateful over Stateless** | Optimize token reuse across turns |
| **Provider Abstraction** | Uniform OpenAI-compatible API surface |
| **Workspace as Source of Truth** | File system is an external state component |
| **Fail Soft** | Graceful degradation on provider failure |
| **Cost-Aware Execution** | Token frugality is a first-class concern |

### 1.3 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CLIENTS                                        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐       │
│  │   Web UI    │  │   REST API  │  │   CLI       │  │   SDK       │       │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘       │
└─────────┼───────────────┼───────────────┼───────────────┼───────────────┘
          │               │               │               │
          ▼               ▼               ▼               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         GATEWAY LAYER                                       │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │                    FastAPI Application                               │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐               │  │
│  │  │   /v1/*      │  │ /management/*│  │  /dashboard │               │  │
│  │  │  Inference  │  │   Admin API   │  │   Static    │               │  │
│  │  └──────────────┘  └──────────────┘  └──────────────┘               │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      ORCHESTRATOR LAYER                                     │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │                      Orchestrator Engine                              │  │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐     │  │
│  │  │  Session   │  │   Account  │  │ Workspace  │  │   Middle   │     │  │
│  │  │  Manager   │  │    Pool    │  │   Engine   │  │  Out Comp. │     │  │
│  │  └────────────┘  └────────────┘  └────────────┘  └────────────┘     │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      ADAPTER LAYER                                          │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐                           │
│  │  Codex    │  │  Gemini    │  │  OpenCode  │                           │
│  │  Adapter  │  │  Adapter   │  │  Adapter   │                           │
│  │  (CLI)    │  │   (CLI)    │  │   (CLI)    │                           │
│  └────────────┘  └────────────┘  └────────────┘                           │
└─────────────────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                       PROVIDERS                                              │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐                           │
│  │   Codex   │  │  Gemini AI │  │  OpenCode  │                           │
│  │   CLI     │  │    API     │  │    MCP     │                           │
│  └────────────┘  └────────────┘  └────────────┘                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Architecture

### 2.1 Component Stack

The UAG follows a layered architecture:

```
┌────────────────────────────────────────────────────────┐
│              PRESENTATION LAYER                        │
│  ┌─────────────────────────────────────────────────┐   │
│  │  FastAPI Routes + Response Serialization       │   │
│  │  - /v1/chat/completions (Inference API)       │   │
│  │  - /management/v1/* (Admin API)               │   │
│  │  - /dashboard/* (Static UI)                   │   │
│  └─────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────┘
                          │
                          ▼
┌────────────────────────────────────────────────────────┐
│               BUSINESS LOGIC LAYER                    │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐    │
│  │ Orchestrator│  │ AccountPool │  │WorkspaceEng │    │
│  │  Engine    │  │  Manager    │  │  ine        │    │
│  └─────────────┘  └─────────────┘  └─────────────┘    │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐    │
│  │ UsageMonitor│  │ ATR Module  │  │ Compression │    │
│  │             │  │             │  │  Module     │    │
│  └─────────────┘  └─────────────┘  └─────────────┘    │
└────────────────────────────────────────────────────────┘
                          │
                          ▼
┌────────────────────────────────────────────────────────┐
│                ADAPTER LAYER                           │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐    │
│  │CodexAdapter│  │GeminiAdapt. │  │OpenCodeAdapt│    │
│  │  (CLI exec) │  │  (CLI exec) │  │  (CLI exec)│    │
│  └─────────────┘  └─────────────┘  └─────────────┘    │
└────────────────────────────────────────────────────────┘
                          │
                          ▼
┌────────────────────────────────────────────────────────┐
│                 PERSISTENCE LAYER                       │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐    │
│  │  SQLite    │  │  Credential │  │   Config    │    │
│  │  Database  │  │    Vault    │  │    Store    │    │
│  └─────────────┘  └─────────────┘  └─────────────┘    │
└────────────────────────────────────────────────────────┘
```

### 2.2 Concurrency Model

The UAG uses a **Hybrid Async-Worker Model**:

```
                    ┌─────────────────────┐
                    │   FastAPI Server    │
                    │  (Async Event Loop) │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
              ▼                ▼                ▼
     ┌────────────────┐ ┌────────────┐ ┌────────────────┐
     │  Semaphore    │ │ Per-Session│ │  Async I/O     │
     │  (Max 10)     │ │   Locks    │ │  Operations    │
     └────────┬───────┘ └─────┬──────┘ └────────────────┘
              │               │
              ▼               ▼
     ┌────────────────┐ ┌────────────┐
     │  CLI Process   │ │  Database  │
     │  (Subprocess)  │ │  Queries   │
     └────────────────┘ └────────────┘
```

- **Control Plane (Gateway)**: Non-blocking FastAPI event loop handles incoming HTTP requests
- **Data Plane (Task Runner)**: CLI processes spawned as async subprocesses
- **Per-Session Binding**: Each `client_session_id` bound to specific worker; concurrent requests serialized via per-session mutex
- **Semaphore-Gated Concurrency**: Global semaphore limits active agent executions

---

## 3. Core Components

### 3.1 Orchestrator Engine (`src/codara/orchestrator/engine.py`)

The central supervisor managing the lifecycle of CLI-based agents.

```python
class Orchestrator:
    def __init__(self, db_manager: DatabaseManager, max_concurrency: int = 10):
        self.db = db_manager
        self.account_pool = AccountPool(db_manager)
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self._session_locks: dict[str, asyncio.Lock] = {}
```

**Key Responsibilities:**
1. Session lifecycle management (create/resume/terminate)
2. Account acquisition and release
3. Workspace locking and snapshot management
4. Request dispatch with retry logic
5. Message preparation and retry coordination

### 3.2 Account Pool (`src/codara/accounts/pool.py`)

Manages provider credentials with intelligent routing:

```
┌─────────────────────────────────────────────────────────┐
│                  AccountPool Logic                      │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  1. Get all accounts for provider                      │
│     WHERE status IN ('active', 'ready')                │
│                                                         │
│  2. Filter ineligible accounts:                         │
│     - In cooldown period                               │
│     - Rate limit reached (429 triggered)               │
│     - Quota exhausted (hourly/weekly)                  │
│                                                         │
│  3. Sort by priority:                                  │
│     - CLI-primary account first (if healthy)           │
│     - Most remaining quota                             │
│     - Least recently used                              │
│                                                         │
│  4. Return best candidate                              │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

**Selection Algorithm:**
```python
def acquire_account(self, provider: ProviderType) -> Optional[Account]:
    candidates = self._eligible_accounts(provider, now=datetime.now(timezone.utc))
    current_primary = self.db.get_cli_primary_account(provider)
    if current_primary and current_primary in candidates and self._has_healthy_headroom(current_primary):
        return current_primary
    chosen = sorted(candidates, key=self._account_priority)[0]
    return self._promote_cli_primary(chosen)
```

### 3.3 Workspace Engine (`src/codara/workspace/engine.py`)

Handles all file-system operations:

- **Snapshot**: Captures pre-execution file hashes
- **Git Baseline**: User-bound workspaces are initialized as local git repositories with a valid `HEAD`
- **Diff Generation**: Uses `git diff HEAD` for git repos, augments untracked files with unified patches, and falls back to hash comparison for non-git workspaces
- **Lock Management**: Prevents concurrent workspace modifications

```python
class WorkspaceEngine:
    def take_snapshot(self) -> Dict[str, str]:
        """Capture hashes of all files in the workspace."""
        
    def generate_diff(self) -> Tuple[List[str], Optional[str]]:
        """Generate diff and list of modified files since last snapshot."""
        
    def acquire_lock(self, timeout: int = 300) -> bool:
        """Acquire workspace lock (prevents concurrent access)."""
```

### 3.4 Provider Adapters

Each provider has a dedicated adapter translating UAG JSON to provider-native CLI execution flows:

#### 3.4.1 Codex Adapter (`src/codara/adapters/codex.py`)

**Protocol**: local `codex exec` CLI execution

```
┌─────────────────────────────────────────────────────────┐
│              Codex Adapter Flow                         │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  1. Setup isolated environment                          │
│     - Create temp HOME directory                       │
│     - Write auth.json with credentials                 │
│                                                         │
│  2. Build command:                                     │
│     codex exec --json --full-auto -o <output>          │
│              -C <workspace> [prompt | resume <id>]    │
│                                                         │
│  3. Execute as subprocess                              │
│                                                         │
│  4. Parse JSONL output:                               │
│     - thread.started (new session)                     │
│     - turn.completed (usage metrics)                   │
│     - item.completed (final output)                   │
│                                                         │
│  5. Cleanup temp directory                            │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

#### 3.4.2 Gemini Adapter (`src/codara/adapters/gemini.py`)

**Protocol**: local Gemini CLI execution

```
┌─────────────────────────────────────────────────────────┐
│              Gemini Adapter Flow                        │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  1. Spawn: `gemini --yolo --output-format json`       │
│                                                         │
│  2. Pass `--model <model>` and optional               │
│     `--resume <backend_id>`                           │
│                                                         │
│  3. Provide the rendered prompt with `--prompt`       │
│                                                         │
│  4. Parse JSON output for `session_id`,               │
│     `response`, and token usage                        │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

#### 3.4.3 OpenCode Adapter (`src/codara/adapters/opencode.py`)

**Protocol**: local OpenCode CLI execution

```
┌─────────────────────────────────────────────────────────┐
│              OpenCode Adapter Flow                      │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  1. Spawn: `opencode run --format json --dir <cwd>`   │
│                                                         │
│  2. Pass `--model <provider/model>` and optional       │
│     `--session <backend_id>`                           │
│                                                         │
│  3. Read JSON events from stdout to recover            │
│     `session_id`, output text, and usage               │
│                                                         │
│  4. Return the assistant message through UAG           │
│                                                         │
│  5. Receive streaming:                                │
│     {"type":"turn.delta","text":"..."}                 │
│     {"type":"turn.done","session_id":"<new_id>",       │
│              "tool_calls":[...]}                        │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

---

## 4. Data Flow & Workflows

### 4.1 Inference Request Lifecycle

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    INFERENCE REQUEST LIFECYCLE                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Client                                                                   │
│    │                                                                      │
│    │ POST /v1/chat/completions                                            │
│    │ {                                                                     │
│    │   model: "uag-codex-v5",                                              │
│    │   messages: [...],                                                   │
│    │   uag_options: {provider, workspace_root, client_session_id}        │
│    │ }                                                                     │
│    ▼                                                                      │
│ ┌────────────────────────────────────────────────────────────────────┐   │
│ │ 1. AUTHENTICATION                                                     │   │
│ │    - Check Bearer token (operator or user API key)                   │   │
│ │    - Validate workspace access                                       │   │
│ └────────────────────────────────────────────────────────────────────┘   │
│    │                                                                      │
│    ▼                                                                      │
│ ┌────────────────────────────────────────────────────────────────────┐   │
│ │ 2. SESSION LOOKUP                                                    │   │
│ │    - Query SessionRegistry by client_session_id                     │   │
│ │    - If exists: Resume session (use backend_id)                     │   │
│ │    - If new: Create session record                                   │   │
│ └────────────────────────────────────────────────────────────────────┘   │
│    │                                                                      │
│    ▼                                                                      │
│ ┌────────────────────────────────────────────────────────────────────┐   │
│ │ 3. ACCOUNT SELECTION                                                 │   │
│ │    - Acquire account from AccountPool                              │   │
│ │    - Check cooldown, rate limits, quota headroom                    │   │
│ │    - If 429: Mark cooldown, retry with next account                │   │
│ └────────────────────────────────────────────────────────────────────┘   │
│    │                                                                      │
│    ▼                                                                      │
│ ┌────────────────────────────────────────────────────────────────────┐   │
│ │ 4. WORKSPACE SETUP                                                   │   │
│ │    - Acquire workspace lock (.uag_lock file)                        │   │
│ │    - Take file snapshot (hashes or git status)                      │   │
│ │    - Calculate prefix_hash for cache key                           │   │
│ └────────────────────────────────────────────────────────────────────┘   │
│    │                                                                      │
│    ▼                                                                      │
│ ┌────────────────────────────────────────────────────────────────────┐   │
│ │ 5. MESSAGE PREPARATION                                              │   │
│ │    - Preserve submitted messages                                   │   │
│ │    - Add retry hint on retried turns                               │   │
│ │    - Pass messages directly to the adapter                         │   │
│ └────────────────────────────────────────────────────────────────────┘   │
│    │                                                                      │
│    ▼                                                                      │
│ ┌────────────────────────────────────────────────────────────────────┐   │
│ │ 6. ADAPTER DISPATCH                                                 │   │
│ │    - Semaphore acquire (global concurrency limit)                  │   │
│ │    - Call adapter.send_turn(session, messages)                    │   │
│ │    - Parse provider response                                       │   │
│ └────────────────────────────────────────────────────────────────────┘   │
│    │                                                                      │
│    ▼                                                                      │
│ ┌────────────────────────────────────────────────────────────────────┐   │
│ │ 7. WORKSPACE DIFF                                                   │   │
│ │    - Generate diff (git diff or hash comparison)                   │   │
│ │    - Extract modified files list                                   │   │
│ │    - Run ATR (Action Translation & Reconstruction)                 │   │
│ └────────────────────────────────────────────────────────────────────┘   │
│    │                                                                      │
│    ▼                                                                      │
│ ┌────────────────────────────────────────────────────────────────────┐   │
│ │ 8. RESPONSE ASSEMBLY                                                │   │
│ │    - Build OpenAI-compatible response                              │   │
│ │    - Add extensions: modified_files, diff, actions, dirty          │   │
│ │    - Update session state in registry                              │   │
│ │    - Release account (update usage stats)                          │   │
│ └────────────────────────────────────────────────────────────────────┘   │
│    │                                                                      │
│    ▼                                                                      │
│  Response                                                                  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 4.2 Account Selection Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    ACCOUNT SELECTION FLOW                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  start                                                                      │
│    │                                                                       │
│    ▼                                                                       │
│  ┌──────────────────────────┐                                              │
│  │ Get accounts for provider │                                             │
│  │ WHERE status IN (active,  │                                             │
│  │       ready)               │                                             │
│  └────────────┬─────────────┘                                              │
│               │                                                             │
│               ▼                                                             │
│  ┌──────────────────────────┐                                              │
│  │ Filter by eligibility:  │                                             │
│  │ - cooldown_until > now  │                                             │
│  │ - hourly_used_pct >= 100 │                                             │
│  │ - weekly_used_pct >= 100 │                                             │
│  │ - rate_limit_reached     │                                             │
│  └────────────┬─────────────┘                                              │
│               │                                                             │
│               ▼                                                             │
│  ┌──────────────────────────┐                                              │
│  │ Rotation policy:         │                                             │
│  │ 1. Keep one CLI-primary  │                                             │
│  │ 2. Rotate at <=5%        │                                             │
│  │ 3. Promote most headroom │                                             │
│  └────────────┬─────────────┘                                              │
│               │                                                             │
│               ▼                                                             │
│        ┌─────┴─────┐                                                        │
│        │ candidates │                                                        │
│        │  empty?   │                                                        │
│        └─────┬─────┘                                                        │
│         yes  │ no                                                           │
│    ┌────────┘  │                                                            │
│    ▼           ▼                                                            │
│  ┌──────┐  ┌────────┐                                                      │
│  │ Fail │  │ Return │                                                      │
│  │Error │  │ Best   │                                                      │
│  │      │  │Account │                                                      │
│  └──────┘  └────────┘                                                      │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 4.3 Session State Machine

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      SESSION STATE MACHINE                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│                         ┌──────────┐                                        │
│                         │   IDLE   │                                        │
│                         │  (init)  │                                        │
│                         └────┬─────┘                                        │
│                              │                                              │
│              ┌───────────────┼───────────────┐                              │
│              │               │               │                              │
│              ▼               ▼               ▼                              │
│      ┌────────────┐  ┌────────────┐  ┌────────────┐                        │
│      │ New turn  │  │  Timeout   │  │ External  │                        │
│      │  starts   │  │ (CLI kill) │  │  signal   │                        │
│      └─────┬──────┘  └─────┬──────┘  └─────┬──────┘                        │
│            │               │               │                               │
│            ▼               ▼               ▼                               │
│      ┌──────────┐   ┌──────────┐   ┌──────────┐                           │
│      │  ACTIVE  │   │  DIRTY   │   │  DIRTY   │                           │
│      │ (locked) │   │ (needs   │   │ (needs   │                           │
│      └────┬─────┘   │  repair) │   │  repair) │                           │
│           │         └──────────┘   └──────────┘                           │
│           │                                                      │
│           ▼                                                      │
│      ┌──────────┐                                               │
│      │ Turn     │                                               │
│      │ completes│                                               │
│      └────┬─────┘                                               │
│           │                                                      │
│           ▼                                                      │
│      ┌──────────┐                                               │
│      │   IDLE   │                                               │
│      │ (ready)  │                                               │
│      └──────────┘                                               │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 4.4 Session Reuse Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    SESSION REUSE FLOW                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Submitted Messages                                                         │
│       │                                                                     │
│       ▼                                                                     │
│  ┌──────────────────────────────────────┐                                  │
│  │ 1. Preserve message list           │                                  │
│  │    - no semantic compression       │                                  │
│  │    - add retry hint only on retry  │                                  │
│  └────────────────────────────────────┘                                  │
│                  │                                                           │
│                  ▼                                                           │
│  ┌──────────────────────────────────────┐                                  │
│  │ 2. Workspace hash bookkeeping        │                                  │
│  │    - derived from workspace tree     │                                  │
│  │    - stored on the session           │                                  │
│  └──────────────────────────────────────┘                                  │
│                  │                                                           │
│                  ▼                                                           │
│  ┌──────────────────────────────────────┐                                  │
│  │ 3. Session/backend reuse             │                                  │
│  │    - same client session id          │                                  │
│  │    - same provider backend id        │                                  │
│  └──────────────────────────────────────┘                                  │
│                  │                                                           │
│                  ▼                                                           │
│  Persisted runtime state for provider-local resume                         │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 5. Database Schema

### 5.1 Core Tables

```sql
-- ============================================================================
-- ACCOUNTS TABLE: Provider credentials and usage tracking
-- ============================================================================
CREATE TABLE accounts (
    account_id               TEXT        PRIMARY KEY,
    credential_id            TEXT,
    inventory_source        TEXT        NOT NULL DEFAULT 'vault',
    provider                 TEXT        NOT NULL,
    auth_type                TEXT        NOT NULL,
    label                    TEXT        NOT NULL,
    encrypted_credential     TEXT,
    status                   TEXT        NOT NULL DEFAULT 'active',
    auth_index               TEXT,
    cooldown_until           INTEGER,
    last_seen_at             INTEGER,
    last_used_at             INTEGER,
    cli_primary              INTEGER     NOT NULL DEFAULT 0,
    
    -- Usage metrics
    usage_tpm                INTEGER     NOT NULL DEFAULT 0,
    usage_rpd                INTEGER     NOT NULL DEFAULT 0,
    usage_hourly             INTEGER     NOT NULL DEFAULT 0,
    usage_weekly             INTEGER     NOT NULL DEFAULT 0,
    
    -- Limits
    tpm_limit                INTEGER     NOT NULL DEFAULT 100000,
    rpd_limit                INTEGER     NOT NULL DEFAULT 5000,
    hourly_limit             INTEGER     NOT NULL DEFAULT 50000,
    weekly_limit             INTEGER     NOT NULL DEFAULT 1000000,
    remaining_compute_hours  FLOAT       NOT NULL DEFAULT 0.0,
    
    -- Usage percentages (from provider APIs)
    hourly_used_pct          REAL,
    weekly_used_pct          REAL,
    hourly_reset_after_seconds INTEGER,
    weekly_reset_after_seconds INTEGER,
    hourly_reset_at          INTEGER,
    weekly_reset_at          INTEGER,
    access_token_expires_at  INTEGER,
    
    -- Provider-specific fields
    usage_source             TEXT,
    plan_type                 TEXT,
    rate_limit_allowed       INTEGER,
    rate_limit_reached       INTEGER,
    credits_has_credits      INTEGER,
    credits_unlimited        INTEGER,
    credits_overage_limit_reached INTEGER,
    approx_local_messages_min INTEGER,
    approx_local_messages_max INTEGER,
    approx_cloud_messages_min INTEGER,
    approx_cloud_messages_max INTEGER
);

-- ============================================================================
-- SESSIONS TABLE: Active conversation threads
-- ============================================================================
CREATE TABLE sessions (
    client_session_id  TEXT        PRIMARY KEY,
    backend_id         TEXT        NOT NULL,          -- thread_id (Codex) or checkpoint_id (Gemini)
    provider           TEXT        NOT NULL,
    account_id         TEXT        NOT NULL REFERENCES accounts(account_id),
    user_id            TEXT        REFERENCES users(user_id),
    api_key_id         TEXT        REFERENCES api_keys(key_id),
    cwd_path           TEXT        NOT NULL,
    prefix_hash        TEXT        NOT NULL,          -- SHA-256(file_tree_metadata) for workspace bookkeeping
    status             TEXT        NOT NULL DEFAULT 'idle',  -- 'idle' | 'active' | 'dirty' | 'expired'
    fence_token        INTEGER     NOT NULL DEFAULT 0,
    created_at         INTEGER     NOT NULL,
    updated_at         INTEGER     NOT NULL,
    expires_at         INTEGER     NOT NULL
);

-- ============================================================================
-- USERS TABLE: Multi-tenant user management
-- ============================================================================
CREATE TABLE users (
    user_id          TEXT        PRIMARY KEY,
    email             TEXT        NOT NULL UNIQUE,
    display_name     TEXT        NOT NULL,
    status           TEXT        NOT NULL DEFAULT 'active',
    workspace_path   TEXT        NOT NULL UNIQUE,
    created_at       INTEGER     NOT NULL,
    created_by       TEXT        NOT NULL,
    updated_at       INTEGER     NOT NULL,
    max_api_keys     INTEGER     NOT NULL DEFAULT 1,
    max_concurrency  INTEGER     NOT NULL DEFAULT 3
);

-- ============================================================================
-- API_KEYS TABLE: User authentication tokens
-- ============================================================================
CREATE TABLE api_keys (
    key_id           TEXT        PRIMARY KEY,
    user_id          TEXT        NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    key_hash         TEXT        NOT NULL UNIQUE,
    key_prefix       TEXT        NOT NULL,
    label            TEXT,
    status           TEXT        NOT NULL DEFAULT 'active',
    last_used_at     INTEGER,
    expires_at       INTEGER,
    created_at       INTEGER     NOT NULL,
    revoked_at       INTEGER
);

-- ============================================================================
-- TURNS TABLE: Conversation turn history
-- ============================================================================
CREATE TABLE turns (
    turn_id           TEXT        PRIMARY KEY,
    client_session_id TEXT        NOT NULL REFERENCES sessions(client_session_id),
    user_id           TEXT,
    provider          TEXT        NOT NULL,
    account_id        TEXT        NOT NULL,
    input_tokens      INTEGER     NOT NULL DEFAULT 0,
    output_tokens     INTEGER     NOT NULL DEFAULT 0,
    finish_reason     TEXT,
    duration_ms       INTEGER     NOT NULL DEFAULT 0,
    diff              TEXT,
    actions           TEXT,
    timestamp        INTEGER     NOT NULL
);

-- ============================================================================
-- AUDIT_LOG TABLE: Action audit trail
-- ============================================================================
CREATE TABLE audit_log (
    audit_id          TEXT        PRIMARY KEY,
    actor              TEXT        NOT NULL,
    action             TEXT        NOT NULL,
    target_type       TEXT        NOT NULL,
    target_id         TEXT        NOT NULL,
    before_state      TEXT,
    after_state       TEXT,
    request_id        TEXT,
    timestamp         INTEGER     NOT NULL
);

-- ============================================================================
-- USER_USAGE TABLE: Token usage aggregation
-- ============================================================================
CREATE TABLE user_usage (
    user_id           TEXT        NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    period            TEXT        NOT NULL,        -- ISO date (YYYY-MM-DD)
    provider         TEXT        NOT NULL,
    input_tokens      INTEGER     NOT NULL DEFAULT 0,
    output_tokens     INTEGER    NOT NULL DEFAULT 0,
    cache_hit_tokens  INTEGER     NOT NULL DEFAULT 0,
    request_count     INTEGER     NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, period, provider)
);

-- ============================================================================
-- WORKSPACE_RESETS TABLE: Session wipe history
-- ============================================================================
CREATE TABLE workspace_resets (
    reset_id          TEXT        PRIMARY KEY,
    user_id           TEXT        NOT NULL REFERENCES users(user_id),
    triggered_by     TEXT        NOT NULL,
    actor_id         TEXT        NOT NULL,
    sessions_wiped   INTEGER     NOT NULL,
    reset_at          INTEGER     NOT NULL
);
```

### 5.2 Indices

```sql
CREATE INDEX idx_sessions_account ON sessions(account_id);
CREATE INDEX idx_sessions_expires ON sessions(expires_at);
CREATE INDEX idx_sessions_cwd ON sessions(cwd_path);
CREATE INDEX idx_sessions_user ON sessions(user_id);
CREATE INDEX idx_sessions_api_key ON sessions(api_key_id);
CREATE INDEX idx_audit_timestamp ON audit_log(timestamp);
CREATE INDEX idx_turns_session ON turns(client_session_id);
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_api_keys_user ON api_keys(user_id);
CREATE INDEX idx_api_keys_hash ON api_keys(key_hash);
CREATE INDEX idx_user_usage_user ON user_usage(user_id, period);
CREATE INDEX idx_resets_user ON workspace_resets(user_id);
```

---

## 6. API Reference

### 6.1 Inference API

#### `POST /v1/chat/completions`

Standard OpenAI-compatible request with UAG extensions. Provisioned user
clients send their bearer API key directly to this endpoint and only provide
the minimal `uag_options` they actually control.

**Request:**
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

For turns that include local files, the same endpoint also accepts
`multipart/form-data`. Put the JSON request in a `payload` form field and attach
one or more files in the same request. The gateway writes those uploads into
the resolved workspace under `.uag/uploads/<session-scope>/...` and prepends a
system message with the relative paths so the downstream CLI can read them.

**`uag_options` Field Reference:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `provider` | `"codex" \| "gemini" \| "opencode"` | required | Target provider |
| `workspace_root` | `string` | null | Operator/internal only. User-key requests should omit this because the gateway injects the bound workspace root |
| `workspace_id` | `string` | null | Optional logical workspace selector for multiple isolated user work areas |
| `client_session_id` | `string` | auto-generated UUID | Session identity for resumption |
| `manual_mode` | `bool` | false | Advanced ATR-only mode that returns actions instead of applying diffs |

**Response:**
```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1699000000,
  "model": "uag-codex-v5",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "..."},
    "finish_reason": "stop"
  }],
  "extensions": {
    "modified_files": ["src/auth.ts"],
    "diff": "--- a/src/auth.ts\n+++ b/src/auth.ts\n@@ ...",
    "actions": [{
      "action_id": "atr_1",
      "type": "patch",
      "format": "search_replace",
      "path": "src/auth.ts",
      "search": "old_value = 1",
      "replace": "old_value = 2",
      "exact": true
    }],
    "dirty": false,
    "client_session_id": "thread-1",
    "workspace_id": "project-a"
  }
}
```

### 6.2 Management API

#### Authentication

```bash
# Get access token
curl -X POST http://localhost:8000/management/v1/auth/token \
  -H "Content-Type: application/json" \
  -d '{"operator_secret": "your-api-token"}'
```

#### Core Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/management/v1/overview` | System health and summary |
| GET | `/management/v1/accounts` | List all accounts |
| POST | `/management/v1/accounts/upload` | Add new account |
| POST | `/management/v1/accounts/{id}/select` | Set CLI-primary account |
| GET | `/management/v1/sessions` | List active sessions |
| GET | `/management/v1/users` | List provisioned users |
| POST | `/management/v1/users` | Create new user |
| GET | `/management/v1/usage` | Usage summary |
| POST | `/management/v1/usage/refresh` | Force usage sync |
| GET | `/management/v1/audit` | Audit logs |
| POST | `/management/v1/playground/chat` | Dashboard playground |

---

## 7. Security Model

### 7.1 Authentication

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        AUTHENTICATION FLOW                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Request                                                                    │
│    │                                                                      │
│    ▼                                                                      │
│  ┌────────────────────────────────────────────────────────────────────┐   │
│  │ Check Authorization Header                                         │   │
│  │ Authorization: Bearer <token>                                     │   │
│  └──────────────────────────┬─────────────────────────────────────────┘   │
│                             │                                                │
│         ┌──────────────────┼──────────────────┐                           │
│         │                  │                  │                            │
│         ▼                  ▼                  ▼                            │
│  ┌────────────┐    ┌────────────┐    ┌────────────┐                         │
│  │ Operator  │    │ User API   │    │  Operator │                         │
│  │ Passkey   │    │   Key      │    │  Token    │                         │
│  │(UAG_MGMT_ │    │ (uagk_*)   │    │ (JWT)     │                         │
│  │ SECRET)   │    │            │    │           │                         │
│  └─────┬──────┘    └─────┬──────┘    └─────┬──────┘                         │
│        │                 │                  │                               │
│        ▼                 ▼                  ▼                               │
│  ┌────────────┐    ┌────────────┐    ┌────────────┐                         │
│  │ Full Admin │    │ User-Scoped│    │ Validate   │                         │
│  │ Access     │    │ Access     │    │ JWT claims │                         │
│  └────────────┘    └────────────┘    └────────────┘                         │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 7.2 Workspace Isolation

```python
def _ensure_user_workspace(user_id: str) -> str:
    root = Path(settings.workspaces_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    workspace_path = root / user_id
    workspace_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return str(workspace_path)

def _resolve_user_workspace(base_workspace_path: str, workspace_id: Optional[str]) -> tuple[str, str]:
    # Validate workspace_id to prevent path traversal
    normalized = _normalize_workspace_id(workspace_id)
    workspace_path = (base_path / normalized).resolve()
    
    # Ensure workspace is within base_path (prevent ../)
    if os.path.commonpath([str(base_path), str(workspace_path)]) != str(base_path):
        raise HTTPException(status_code=400, detail="Invalid workspace_id")
```

### 7.3 Credential Handling

- Credentials encrypted at rest using `secrets.encrypt()`
- Isolated temp HOME for each CLI execution
- Materialized credentials only for CLI-primary account

---

## 8. Deployment

### 8.1 Development Setup

```bash
# Install dependencies
uv sync --extra dev
pip install -e .

# Build UI (optional)
cd ui && npm install && npm run build

# Start server
codara serve --host 0.0.0.0 --port 8000
```

### 8.2 Production Configuration

```toml
# codara.toml
[server]
host = "0.0.0.0"
port = 8000
secret_key = "${UAG_MGMT_SECRET}"

[database]
path = "/var/lib/codara/codara.db"

[orchestrator]
max_concurrency = 10
session_ttl_hours = 24

[workspace]
root = "/var/workspaces"
isolated_envs_root = "/var/workspaces/isolated_envs"
lock_timeout = 300

[providers.codex]
usage_endpoints = "https://chatgpt.com/backend-api/wham/usage,https://api.openai.com/dashboard/codex/usage"
oauth_url = "https://auth0.openai.com/oauth/token"
default_model = "gpt-5-codex"

[providers.gemini]
default_model = "gemini-2.5-pro"

[providers.opencode]
default_model = "openai/gpt-5"
```

### 8.3 Scaling Strategy

| Environment | Session Store | Hot Cache |
|-------------|---------------|-----------|
| Development | SQLite (local) | - |
| Production | PostgreSQL | Redis |

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    PRODUCTION DEPLOYMENT                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   Load Balancer (nginx/haproxy)                                             │
│         │                                                                  │
│    ┌────┴────┬────┬────┐                                                   │
│    ▼         ▼    ▼    ▼                                                   │
│ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐                                         │
│ │ UAG  │ │ UAG  │ │ UAG  │ │ UAG  │  (Stateless workers)                   │
│ │Node 1│ │Node 2│ │Node 3│ │Node 4│                                         │
│ └──┬───┘ └──┬───┘ └──┬───┘ └──┬───┘                                         │
│    └────────┼────────┼────────┘                                             │
│             │        │                                                      │
│      ┌──────┴───────┴──────┐                                                │
│      │                     │                                                │
│      ▼                     ▼                                                │
│ ┌─────────┐          ┌─────────────┐                                         │
│ │ Postgres│          │   Redis     │                                         │
│ │  (SQLite│          │  (Sessions, │                                         │
│ │  fallback)         │   Locks)    │                                         │
│ └─────────┘          └─────────────┘                                         │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 9. Operations & Monitoring

### 9.1 Health Checks

```bash
# System health
GET /management/v1/health

# Provider health
GET /management/v1/health/providers

# Response
{
  "status": "ok",
  "components": {
    "gateway": {"status": "ok", "latency_ms": 0.5},
    "orchestrator": {"status": "ok", "latency_ms": 2.1},
    "state_store": {"status": "ok", "latency_ms": 0.3}
  },
  "providers": [
    {
      "provider": "codex",
      "status": "ok",
      "active_sessions": 12,
      "accounts_available": 3
    }
  ],
  "checked_at": "2026-04-14T12:00:00Z"
}
```

### 9.2 Metrics

```bash
# Prometheus metrics endpoint
GET /metrics

# Example metrics:
# uag_sessions_total 42
# uag_sessions_dirty 1
# uag_accounts_total 5
# uag_accounts_cooldown 1
# uag_provider_sessions{provider="codex"} 30
```

### 9.3 Key Performance Indicators

| Metric | Goal | Measurement |
|--------|------|--------------|
| **Token Cache Hit Rate** | > 80% | `prefix_hash` match rate |
| **Recovery Latency** | < 2.0s | Time from 429 to retry |
| **Context Fidelity** | 100% | Golden diff test suite |
| **Protocol Overhead** | < 100ms | P95 latency delta |

---

## Appendix: File Structure

```
src/codara/
├── __init__.py
├── config.py                    # Configuration management
├── main.py                     # Entry point
├── cli/                        # CLI commands
├── core/
│   ├── models.py               # Pydantic models
│   ├── security.py             # Auth utilities
│   └── atr.py                 # Action Translation & Reconstruction
├── database/
│   └── manager.py             # SQLite operations
├── gateway/
│   └── app.py                 # FastAPI application
├── accounts/
│   ├── pool.py                # Account selection
│   ├── vault.py               # Credential storage
│   └── monitor.py             # Usage sync
├── adapters/
│   ├── base.py                # Adapter interface
│   ├── codex.py               # Codex CLI adapter
│   ├── gemini.py              # Gemini API adapter
│   └── opencode.py            # OpenCode MCP adapter
├── orchestrator/
│   └── engine.py              # Request orchestration
└── workspace/
    └── engine.py              # File system operations
```
