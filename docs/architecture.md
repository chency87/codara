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
  └─ UsageMonitor

Runtime Core
  ├─ Orchestrator
  ├─ Provider adapters
  └─ Workspace engine

Persistence
  ├─ SQLite product state
  ├─ vault credential files
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

Provider adapters are intentionally local-CLI based:

- Codex uses the local installed `codex` executable with a managed isolated home
- Gemini uses the local installed `gemini` CLI with host login state
- OpenCode uses the local installed `opencode` CLI with host login state

## 3. Persistence Boundaries

### SQLite owns

- users
- API keys
- sessions
- turns
- accounts
- usage summaries
- workspace resets
- channel links, conversations, link tokens, runtime state
- audit log

### File-backed storage owns

- runtime logs under `logs/runtime/...`
- traces under `logs/traces/...`

### Vault storage owns

- managed provider credential blobs under the Codara config directory

## 4. Isolation Model

Codara uses two different runtime-auth models:

- **Managed isolated model**
  - Codex credentials are stored in the vault and injected into an isolated runtime home
- **System-local model**
  - Gemini and OpenCode depend on the host machine's local CLI login

That split is deliberate. Do not collapse them into one generic provider-auth model in docs or code.

## 5. Dashboard Map

The live dashboard routes are:

- `/`
- `/playground`
- `/sessions`
- `/workspaces`
- `/accounts`
- `/users`
- `/providers`
- `/usage`
- `/observability`
- `/audit`

`/traces` and `/logs` still exist as standalone pages, but the main debugging workflow is the unified Observability page.
