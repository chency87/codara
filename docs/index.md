# Codara System Guide

Codara is a stateful gateway that lets OpenAI-compatible clients talk to CLI-native coding agents such as Codex, Gemini, and OpenCode while preserving session state, tracking workspace changes, and exposing an operator control plane.

## 1. What Codara Does

Codara combines four responsibilities:

1. It exposes an OpenAI-compatible inference entry point at `POST /v1/chat/completions`.
2. It persists provider session state so repeated turns can reuse context.
3. It tracks workspaces, tasks, diffs, and activity across users and sessions.
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
                                │ tasks, concurrency│
                                └───────┬─────┬─────┘
                                        │     │
                     ┌──────────────────▼┐   ┌▼──────────────────┐
                     │ Provider Adapters │   │ Workspace Engine   │
                     │ Codex / Gemini /  │   │ git or hash diffs, │
                     │ OpenCode CLIs     │   │ locks, snapshots   │
                     └─────────┬─────────┘   └─────────┬──────────┘
                               │                       │
                ┌──────────────▼──────────────┐  ┌────▼─────────────┐
                │ SQLite Persistence          │  │ File-backed logs │
                │ users, workspaces,          │  │ and trace shards │
                │ sessions, tasks, audit      │  │ runtime + traces │
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
InferenceService resolves workspace_id
   │
   ▼
Orchestrator binds request to Task and Session
   │
   ▼
Adapter executes CLI turn
   │
   ▼
Workspace diff + ATR extraction
   │
   ▼
Session and task state persisted
   │
   ▼
OpenAI-compatible response + Codara extensions
```

## 5. State Model

### 5.1 Sessions

Codara persists session metadata in SQLite so provider-local `backend_id` values survive across requests and restarts. Sessions are bound to a specific Workspace.

### 5.2 Workspaces

Workspaces are first-class entities in Codara. They represent a managed directory on the file system where an agent operates.

- provisioned users can have multiple workspaces.
- each workspace is initialized with a template layout.
- the workspace engine uses git metadata when available and falls back to recursive hash comparison otherwise.

### 5.3 Tasks

Every request to the inference API is tracked as a `Task`. Tasks belong to a `Session` and provide a detailed audit trail of individual agent turns, including their prompts, statuses, and results.

## 6. Configuration Model

Runtime configuration is block-based in `codara.toml`.

Key sections:
- `[server]`
- `[database]`
- `[workspace]`
- `[logging]`
- `[providers.codex]`
- `[providers.gemini]`
- `[providers.opencode]`

## 7. Management Plane

The dashboard live pages are:
- Overview
- Agent Playground
- Active Sessions
- Workspaces
- Users
- Providers
- Observability
- Audit Logs

## 8. Document Map

- [README.md](../README.md) for install, config, and operator quickstart
- [api-dashboard.md](./api-dashboard.md) for management APIs and dashboard workflows
- [channel-design.md](./channel-design.md) for Telegram and channel-layer behavior
- [architecture.md](./architecture.md) for a concise internal component map
