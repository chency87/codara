# Unified Agent Gateway (UAG)

Codara is a stateful gateway that lets OpenAI-compatible clients talk to CLI-native agents such as Codex, Gemini, and OpenCode while reusing sessions and managing workspace state.

## What it does

- Maintains persistent sessions behind `/v1/chat/completions`
- Reuses workspace-aware context to reduce repeated prompt cost
- Exposes an operator dashboard and management API for users, workspaces, sessions, and audit logs

## Install

```bash
uv sync --extra dev
pip install -e .
```

If you want the dashboard, install the UI dependencies once:

```bash
cd ui && npm install
```

## Start the gateway

```bash
codara serve --host 0.0.0.0 --port 8000
```

`codara serve` no longer blocks on an implicit UI build. If `ui/dist` is missing, the API still starts and the CLI prints the command you need for the dashboard. Build the dashboard explicitly when you need it:

```bash
cd ui && npm run build
# or
codara serve --build-ui
```

If you change files under `ui/src`, rebuild the dashboard before using the served `/dashboard` bundle. `codara serve` warns when the checked-in `ui/dist` build is older than the current UI source tree.

The gateway serves dashboard client-side routes with a history fallback. After `ui/dist` exists, refreshing `/dashboard/workspaces`, `/dashboard/accounts`, or another dashboard page returns the React shell instead of a FastAPI JSON 404.

## Run with Docker

```bash
cp configs/.env.example .env
# Edit API_TOKEN before exposing the service.
docker compose up --build
```

The image builds the dashboard bundle during `docker build`, installs the Codex, Gemini, and OpenCode CLIs, then starts `codara serve` inside the container. Runtime state is persisted in named volumes for `/data`, `/config`, `/logs`, and `/workspaces`; see [`docker-compose_README.md`](docker-compose_README.md) for deployment details.

The GitHub workflow publishes container images to GitHub Container Registry:

```bash
docker pull ghcr.io/chency87/codara:latest
```

Tagged releases such as `v0.1.0` publish matching semver tags. If the package is not publicly pullable after the first publish, mark the GHCR package visibility as public in the repository package settings.

Check the installed framework version locally:

```bash
codara version
codara version --check
```

`codara version --check` uses the GitHub latest-release API only when `[release].enabled = true` and `[release].repository` is configured in `codara.toml`.

Runtime defaults come from [`codara.toml`](codara.toml), which is organized into blocks such as `[server]`, `[workspace]`, `[providers.*]`, and `[channels.*]`. Environment variables still override config values, including:

Start from the sanitized example when creating a local config:

```bash
cp configs/codara.toml.example codara.toml
```

- `API_TOKEN` for operator login (`UAG_MGMT_SECRET` remains a supported fallback alias)
- `UAG_WORKSPACES_ROOT` for provisioned user workspaces
- `UAG_CODEX_STALL_TIMEOUT_SECONDS`, `UAG_GEMINI_STALL_TIMEOUT_SECONDS`, and `UAG_OPENCODE_STALL_TIMEOUT_SECONDS` for killing provider CLIs that are alive but no longer producing stdout/stderr progress

Telegram channels support both:
- `receive_mode = "webhook"` for public HTTPS webhook delivery
- `receive_mode = "polling"` for long-polling via Telegram `getUpdates` when you do not have a webhook endpoint

In polling mode, Codara disables any existing Telegram webhook for the bot at startup and stores the per-bot update offset locally so polling can resume after restarts.

Linked Telegram users can create and switch workspaces directly:

```text
/workspaces
/workspace_create news-pulse --template python
/workspace_info news-pulse
/workspace news-pulse
```

## Create workspaces

```bash
codara workspace create news-pulse
codara workspace create agent-lab --template python --provider codex
codara workspace list
codara workspace info news-pulse
```

The default template creates:

```text
README.md
docs/
src/
scripts/
tests/
```

Available templates are `default`, `python`, `docs`, and `empty`.

## Operator dashboard and management API

1. Set `UAG_MGMT_SECRET`
2. Start the gateway
3. Open `/dashboard`
4. Enter the `API_TOKEN` value from `.env` on the login screen to exchange it for a management token

The dashboard is a thin client over `/management/v1/*`. Useful endpoints:

- `POST /management/v1/auth/token`
- `GET /management/v1/overview`
- `GET /management/v1/version?check_updates=true`
- `GET /management/v1/workspaces`
- `POST /management/v1/workspaces`
- `GET /management/v1/traces`
- `GET /management/v1/logs`
- `GET /management/v1/providers/models`
- `POST /management/v1/playground/chat`
- `GET /management/v1/audit`

Runtime logs are emitted as structured JSON and trace events are persisted as datetime-partitioned JSONL files under the configured telemetry trace root. The management plane exposes trace roots and trace-event details for cross-component debugging.
The dashboard includes an **Observability** explorer that lets operators search trace roots and runtime messages together, then reconstruct a trace-centric timeline from correlated events and log lines.
Use `[logging].retention_days` and `[telemetry].trace_retention_days` to control how long file-backed runtime logs and trace shards are kept. The Observability explorer also supports quick and custom time-range filters.

The dashboard Playground runs as a dedicated **Dashboard Admin** user inside the provisioned workspaces root. That default user is managed like any other provisioned user, remains visible in the Users panel, and Playground turns bind to that user's active API-key identity and `workspace_id` instead of an arbitrary absolute path.

The dashboard also includes a dedicated **Workspaces** page that inventories the
managed workspaces tree, shows git metadata for repo-backed workspaces, and lets
operators inspect or wipe bound sessions before optionally deleting a workspace
directory from disk.

## Inference API

### Operator or direct workspace request

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "uag-codex-v5",
    "messages": [{"role": "user", "content": "Review this repo."}],
    "uag_options": {
        "provider": "codex",
        "workspace_root": "/absolute/path/to/project",
        "client_session_id": "thread-1"
    }
  }'
```

### Provisioned user request

Provision a user from the dashboard or management API, then
call the same endpoint with that user API key. User requests normally only need
the standard bearer header plus `uag_options`:

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer uagk_live_..." \
  -d '{
    "model": "gemini-2.5-pro",
    "messages": [{"role": "user", "content": "Review my changes."}],
    "uag_options": {
        "provider": "gemini",
        "workspace_id": "project-a/feature-x",
        "client_session_id": "thread-1"
    }
  }'
```

UAG namespaces the real session as `user_id::workspace_id::client_session_id` so each user workspace gets stable session reuse.

For user-key requests:

- `workspace_root` is injected by the gateway from the provisioned workspace and should usually be omitted.
- `workspace_id` is only needed when the user wants multiple isolated sub-workspaces; otherwise the default workspace is used.
- `manual_mode` is an advanced runtime control and can usually be omitted.
- `model` may be either an explicit provider runtime model (for example `gpt-5-codex`, `gemini-2.5-pro`, or `opencode/big-pickle`) or a `uag-*` alias; aliases resolve to the configured provider default model from `codara.toml`.
- `GET /v1/user/providers/models` returns the current provider model listings; operators can query the same inventory via `GET /management/v1/providers/models`.

### Response extensions

Responses include normal OpenAI-compatible fields plus:

- `modified_files`
- `diff`
- `actions` (normalized exact workspace operations such as search/replace patches, unified diffs, or JSON file-write actions)
- `dirty`

## Development

Common commands:

```bash
pytest -q
cd ui && npm run build
```

For design details, start with:

- [`docs/index.md`](docs/index.md)
- [`docs/api-dashboard.md`](docs/api-dashboard.md)
- [`docs/channel-design.md`](docs/channel-design.md)
- [`docs/architecture.md`](docs/architecture.md)
