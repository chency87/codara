# Codara System Guide

Codara is a stateful gateway that lets OpenAI-compatible clients talk to CLI-native coding agents such as Codex, Gemini, and OpenCode while preserving session state, tracking workspace changes, and exposing an operator control plane.

This document is the authoritative high-level description of the current codebase. It describes the runtime that exists today, not an aspirational future architecture.

## 1. What Codara Does

Codara combines four responsibilities:

1. It exposes an OpenAI-compatible inference entry point at `POST /v1/chat/completions`.
2. It persists provider session state so repeated turns can reuse context.
3. It tracks workspaces, diffs, activity, and usage across users and sessions.
4. It exposes a management plane and dashboard for operators.

## 2. Product Surface

The current runtime has three ingress families:

- **Inference API**
  - Direct/operator use through `POST /v1/chat/completions`
  - Provisioned-user use through the same endpoint with a user API key
- **Channel ingress**
  - Telegram-first support through `/channels/telegram/{bot_name}/webhook` or polling mode
- **Management plane**
  - `/management/v1/*` APIs
  - `/dashboard` React UI

## 3. Runtime Architecture

```text
                         ┌─────────────────────────────┐
                         │        API Clients          │
                         │ OpenAI SDKs / curl / apps   │
                         └──────────────┬──────────────┘
                                        │
                         ┌──────────────▼──────────────┐
                         │      FastAPI Gateway        │
                         │ auth, request shaping,      │
                         │ management API, dashboard   │
                         └───────┬───────────┬─────────┘
                                 │           │
                  ┌──────────────▼───┐   ┌──▼────────────────┐
                  │ InferenceService │   │ Channel Adapters   │
                  │ user-bound turn  │   │ Telegram webhook / │
                  │ execution        │   │ polling ingress    │
                  └──────────────┬───┘   └──────────┬─────────┘
                                 │                  │
                                 └────────┬─────────┘
                                          │
                                ┌─────────▼─────────┐
                                │   Orchestrator    │
                                │ session locks,    │
                                │ concurrency, ATR, │
                                │ diff collection   │
                                └───────┬─────┬─────┘
                                        │     │
                     ┌──────────────────▼┐   ┌▼──────────────────┐
                     │ Provider Adapters │   │ Workspace Engine   │
                     │ Codex / Gemini /  │   │ git or hash diffs, │
                     │ OpenCode CLIs     │   │ locks, snapshots   │
                     └─────────┬─────────┘   └─────────┬──────────┘
                               │                       │
                ┌──────────────▼──────────────┐  ┌────▼─────────────┐
                │ SQLite + local vault state  │  │ File-backed logs │
                │ users, sessions, accounts,  │  │ and trace shards │
                │ channel bindings, audit     │  │ runtime + traces │
                └─────────────────────────────┘  └──────────────────┘
```

## 4. Core Execution Workflows

### 4.1 Provisioned User Request

Provisioned-user requests are the primary product path.

```text
User API Key
   │
   ▼
Gateway validates key and loads user/api_key
   │
   ▼
InferenceService resolves workspace_root from the user's provisioned workspace
   │
   ▼
client_session_id is namespaced as:
user_id::workspace_id::client_session_id
   │
   ▼
Orchestrator resumes or creates provider session
   │
   ▼
Adapter executes CLI turn
   │
   ▼
Workspace diff + ATR extraction
   │
   ▼
Session and turn state persisted
   │
   ▼
OpenAI-compatible response + Codara extensions
```

### 4.2 Direct or Operator Workspace Request

Direct/operator turns are allowed, but their `workspace_root` must pass the workspace safe-zone policy before execution.

```text
Client request with top-level workspace_root
   │
   ▼
Gateway validates path against workspaces_root
and rejects paths under isolated_envs_root
   │
   ▼
Orchestrator executes the turn if allowed
```

### 4.3 Telegram Channel Request

Telegram is the only fully implemented channel today.

```text
Telegram update
   │
   ▼
Telegram adapter validates bot + receive mode
   │
   ▼
ChannelService resolves linked Codara user
   │
   ▼
Conversation record provides workspace_id, provider,
and stable client_session_id
   │
   ▼
Shared InferenceService executes the same user-bound flow
used by /v1/chat/completions
   │
   ▼
Adapter sends reply text and keeps conversation state updated
```

## 5. State Model

### 5.1 Sessions

Codara persists session metadata in SQLite so provider-local `backend_id` values survive across requests and restarts.

Important session properties:

- `client_session_id`
- `backend_id`
- `provider`
- `account_id`
- `user_id`
- `api_key_id`
- `cwd_path`
- `status`
- `expires_at`

The runtime uses session reuse, but it does **not** currently implement a separate prompt-compression or prefix-rewrite engine on top of `prefix_hash`.

### 5.2 Workspaces

Workspaces are part of the product state, not just a request parameter.

Current behavior:

- provisioned users get a stable base workspace under `workspaces_root`
- user requests can target sub-workspaces with `workspace_id`
- direct/operator requests may pass `workspace_root` only if it stays inside the safe zone
- the workspace engine uses git metadata when available and falls back to recursive hash comparison otherwise

### 5.3 Projects

Projects are the user-facing creation layer for managed workspaces.

- `codara project create <name>` creates a folder under `workspaces_root`
- project folders are initialized with a template layout and `.codara/project.toml`
- project metadata is advisory product metadata; execution still depends on the workspace safe-zone and session model
- project API endpoints are aliases over workspace records, not a separate database model
- existing workspaces without `.codara/project.toml` remain valid workspaces

Default project layout:

```text
README.md
docs/
src/
scripts/
tests/
.codara/project.toml
```

Supported templates are `default`, `python`, `docs`, and `empty`.

### 5.3 Accounts and Providers

The provider model is intentionally asymmetric:

- **Codex**
  - supports managed account registration in SQLite + vault storage
  - supports CLI-primary selection and quota-aware fallback
  - uses the local installed `codex` binary
  - runs turns in an isolated provider home under `isolated_envs_root`
  - does **not** treat the host `~/.codex/auth.json` as the canonical managed credential source
- **Gemini**
  - system-local provider
  - uses the local installed CLI and host login state
  - not imported into the managed account pool
- **OpenCode**
  - system-local provider
  - uses the local installed CLI and host login state
  - not imported into the managed account pool

This is the current product boundary, not a temporary shortcut.

## 6. Configuration Model

Runtime configuration is block-based in `codara.toml`.
Use `codara.toml.example` as the sanitized starting point for new deployments.

Key sections:

- `[server]`
- `[database]`
- `[workspace]`
- `[logging]`
- `[limits]`
- `[providers.codex]`
- `[providers.gemini]`
- `[providers.opencode]`
- `[release]`
- `[telemetry]`
- `[channels.telegram]`

The runtime still consumes stable `settings.<field>` names internally; nested config blocks are normalized in `src/codara/config.py`.
The `[release]` block controls optional GitHub release update checks used by `codara version --check`, `/management/v1/version`, and the dashboard overview.

## 7. Management Plane

The management API is grouped into these domains:

- authentication
- users
- workspaces
- projects
- sessions
- accounts
- usage
- observability
- audit
- playground

The dashboard is a thin client over that API. The live dashboard pages are:

- Overview
- Agent Playground
- Active Sessions
- Workspaces
- Account Pool
- Users
- Providers
- Usage Metrics
- Observability
- Audit Logs

## 8. Observability Model

Codara separates product state from high-volume observability data.

```text
SQLite
  ├─ users
  ├─ api keys
  ├─ sessions
  ├─ accounts
  ├─ audit log
  └─ channel bindings

logs/runtime/YYYY/MM/DD/HH.jsonl
  └─ structured runtime log lines

logs/traces/...
  ├─ trace event shards
  └─ lightweight trace-root indexes
```

This split is intentional:

- SQLite is for product state and low-volume audit records.
- runtime logs are file-backed JSONL shards.
- traces are file-backed structured events and indexes.
- the Observability page merges traces and runtime logs by `trace_id` or `request_id` instead of trying to store everything in SQLite.

## 9. Security and Isolation Boundaries

Current protection boundaries:

- management APIs require operator authentication
- user APIs require provisioned user API keys
- direct `workspace_root` paths are checked against the safe zone
- `isolated_envs_root` is excluded from direct/operator workspace access
- managed Codex credentials stay inside the vault + isolated runtime path
- Telegram linking uses one-time expiring channel link tokens

## 10. What Is Intentionally Not Claimed

The current codebase does **not** implement all ideas from the older design docs. The following should be treated as future work unless the code says otherwise:

- distributed multi-node routing with Redis/Postgres
- execution timeout orchestration with partial response recovery
- fence-token stale-write protection
- full ATR validation before every response
- Lark/Feishu runtime adapters beyond scaffolding
- relational persistence for runtime logs or traces

## 11. Document Map

Use these docs as the current primary references:

- [README.md](../README.md) for install, config, and operator quickstart
- [api-dashboard.md](./api-dashboard.md) for management APIs and dashboard workflows
- [channel-design.md](./channel-design.md) for Telegram and channel-layer behavior
- [architecture.md](./architecture.md) for a concise internal component map

Historical design and analysis docs remain in `docs/`, but they should be treated as background material unless they explicitly match the current runtime.
