# Codara Architecture Map

This document is a concise map of the live component boundaries. Use [index.md](./index.md) for the fuller system guide.

## 1. Component Map

```text
Ingress
  ├─ /v1/chat/completions
  ├─ /management/v1/*
  ├─ /v1/user/*
  └─ /channels/telegram/{bot_name}/webhook or polling

Gateway Layer
  ├─ auth and token validation
  ├─ request normalization
  ├─ management response shaping
  └─ dashboard static asset serving

Shared Services
  ├─ InferenceService
  ├─ ChannelService

Runtime Core
  ├─ Orchestrator
  ├─ Provider adapters
  └─ Workspace engine

Persistence
  ├─ SQLite product state
  ├─ runtime log shards
  └─ trace shards
```

## 2. Request Boundaries

### User-bound execution

User-bound turns should flow through `InferenceService` so workspace binding, session naming, and attachment staging stay consistent across:

- user API requests
- dashboard playground requests
- Telegram channel requests

### Provider execution

Provider adapters are intentionally local-CLI based and use the host system's native login state.

## 3. Persistence Boundaries

### SQLite owns

- users
- API keys
- workspaces
- sessions
- tasks
- turns
- workspace resets
- channel links, conversations, link tokens, runtime state
- audit log

### File-backed storage owns

- runtime logs under `logs/runtime/...`
- traces under `logs/traces/...`

## 4. Isolation Model

Codara depends on the host machine's local CLI login for all providers (Gemini, OpenCode, Codex).

## 5. Dashboard Map

The live dashboard routes are:

- `/`
- `/playground`
- `/sessions`
- `/workspaces`
- `/users`
- `/providers`
- `/observability`
- `/audit`

`/traces` and `/logs` still exist as standalone pages, but the main debugging workflow is the unified Observability page.
