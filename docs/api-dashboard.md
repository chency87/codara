# Management API and Dashboard Guide

This document describes the current Codara control plane: the `/management/v1/*` APIs and the `/dashboard` UI that consumes them.

## 1. Control-Plane Model

The dashboard is a first-party client of the management API. It does not have privileged backend-only data access.

```text
Operator
   │
   ▼
/dashboard login
   │
   ▼
POST /management/v1/auth/token
   │
   ▼
Bearer token in sessionStorage
   │
   ▼
Dashboard pages query /management/v1/*
```

## 2. Authentication

Management endpoints require an operator bearer token or the configured management secret for token minting.

Important notes:

- the login screen exchanges the operator secret for a bearer token
- the dashboard stores the access token in `sessionStorage`
- `API_TOKEN` remains the primary operational secret name
- `UAG_MGMT_SECRET` is a supported fallback alias

## 3. Current Management API Surface

### 3.1 Users

Routes:

- `GET /management/v1/users`
- `GET /management/v1/users/{user_id}`
- `POST /management/v1/users`
- `POST /management/v1/users/{user_id}/suspend`
- `POST /management/v1/users/{user_id}/unsuspend`
- `DELETE /management/v1/users/{user_id}`
- `GET /management/v1/users/{user_id}/keys`
- `DELETE /management/v1/users/{user_id}/keys/{key_id}`
- `POST /management/v1/users/{user_id}/keys/rotate`
- `POST /management/v1/users/{user_id}/channels/link-token`
- `POST /management/v1/users/{user_id}/workspace/reset`
- `GET /management/v1/users/{user_id}/usage`

Dashboard behavior:

- the Users page shows the real `user_id`
- operators can rotate keys
- operators can create Telegram link tokens
- operators can reset a user's workspace sessions

### 3.2 Workspaces

Routes:

- `GET /management/v1/workspaces`
- `GET /management/v1/workspaces/{workspace_id}`
- `POST /management/v1/workspaces/{workspace_id}/reset`
- `DELETE /management/v1/workspaces/{workspace_id}`

The Workspaces page is the operator-facing inventory of the managed workspace tree. It includes git metadata, bound users, and bound sessions.

### 3.3 Sessions

Routes:

- `GET /management/v1/sessions`
- `GET /management/v1/sessions/{session_id}`
- `GET /management/v1/sessions/{session_id}/turns`
- `DELETE /management/v1/sessions/{session_id}`
- `POST /management/v1/sessions/{session_id}/reset`

### 3.4 Accounts

Routes:

- `GET /management/v1/accounts`
- `GET /management/v1/accounts/{account_id}`
- `POST /management/v1/accounts`
- `POST /management/v1/accounts/upload`
- `POST /management/v1/accounts/{account_id}/select`
- `POST /management/v1/accounts/{account_id}/cooldown`
- `POST /management/v1/accounts/{account_id}/recover`
- `DELETE /management/v1/accounts/{account_id}`

Current provider policy:

- Codex is the only managed account provider.
- Gemini and OpenCode are local-only runtimes and are not imported into the managed account pool.

Current Codex isolation policy:

- selecting a Codex account marks it CLI-primary in Codara
- managed Codex credentials stay in the vault and isolated runtime path
- selecting a managed Codex account does **not** overwrite the host `~/.codex/auth.json`

### 3.5 Usage

Routes:

- `GET /management/v1/usage`
- `GET /management/v1/usage/timeseries`
- `POST /management/v1/usage/refresh`
- `GET /management/v1/usage/accounts/{account_id}`
- `GET /management/v1/usage/sessions/{session_id}`

### 3.6 Observability

Routes:

- `GET /management/v1/health`
- `GET /management/v1/health/providers`
- `GET /management/v1/providers/models`
- `GET /management/v1/overview`
- `GET /management/v1/metrics`
- `GET /management/v1/traces`
- `GET /management/v1/traces/{trace_id}`
- `GET /management/v1/logs`
- `POST /management/v1/observability/prune`

Observability storage model:

- traces are file-backed structured events
- runtime logs are file-backed JSONL shards
- the API merges or filters them at query time

### 3.7 Audit and Playground

Routes:

- `GET /management/v1/audit`
- `POST /management/v1/playground/chat`

The Playground is intentionally routed through a dedicated dashboard admin user rather than an ad hoc operator-only execution path.

## 4. Dashboard Page Map

The current dashboard routes and responsibilities are:

| Route | Page | Purpose |
| --- | --- | --- |
| `/` | Overview | High-level runtime, provider, and audit summary |
| `/playground` | Agent Playground | Operator testing through the shared user-bound flow |
| `/sessions` | Active Sessions | Inspect, copy, reset, or delete sessions |
| `/workspaces` | Workspaces | Inspect managed workspaces and reset/delete them |
| `/accounts` | Account Pool | Register Codex accounts, select CLI-primary, cooldown/recover |
| `/users` | Users | Provision users, rotate keys, issue Telegram link tokens, reset workspace sessions |
| `/providers` | Providers | Runtime provider readiness and model inventory |
| `/usage` | Usage Metrics | Aggregate and time-series usage views |
| `/observability` | Observability | Unified trace + runtime-log workflow |
| `/audit` | Audit Logs | Search and inspect management mutations |

## 5. Key Operator Workflows

### 5.1 Provision a User

```text
Users page
   │
   ▼
POST /management/v1/users
   │
   ▼
one-time raw API key returned
   │
   ▼
user begins calling /v1/chat/completions
```

### 5.2 Link a Telegram User

```text
Users page
   │
   ▼
Create channel link token
POST /management/v1/users/{user_id}/channels/link-token
   │
   ▼
operator copies raw token
   │
   ▼
Telegram user sends: /link <token>
   │
   ▼
channel_user_links row is created
```

### 5.3 Diagnose a Runtime Problem

```text
Overview or Providers page
   │
   ▼
Observability page
   │
   ├─ query trace roots
   ├─ query runtime log messages
   └─ reconstruct one merged timeline by trace_id
```

## 6. Current Config and Deployment Notes

The dashboard and management plane rely on:

- block-based `codara.toml`
- a built UI bundle under `ui/dist`
- the management secret in `.env` or environment variables

The dashboard does not require a separate backend service. It is served directly by the gateway.
The gateway also provides a dashboard-scoped history fallback: extensionless `/dashboard/*` paths return `ui/dist/index.html` so browser refreshes on React Router pages continue to work. Missing dashboard asset paths still return 404s, and API paths such as `/management/v1/*` are not affected by the fallback.

## 7. Known Boundaries

Current intentional boundaries:

- the dashboard surfaces real management actions instead of acting as a generic API console
- Observability is the primary log/trace debugging page; standalone Traces/Logs routes remain secondary
- only Telegram is fully implemented as a channel runtime today
