# Unified Agent Gateway (UAG)

Codara is a stateful gateway that lets OpenAI-compatible clients talk to CLI-native agents such as Codex, Gemini, and OpenCode while reusing sessions, managing workspace state, and routing across a quota-aware account pool.

## What it does

- Maintains persistent sessions behind `/v1/chat/completions`
- Reuses workspace-aware context to reduce repeated prompt cost
- Stores provider credentials in the vault/SQLite registry instead of treating local CLI auth files as inventory
- Selects accounts automatically based on health, cooldown state, and remaining quota headroom
- Exposes an operator dashboard and management API for accounts, sessions, usage, and audit logs

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

Check the installed framework version locally:

```bash
codara version
codara version --check
```

`codara version --check` uses the GitHub latest-release API only when `[release].enabled = true` and `[release].repository` is configured in `codara.toml`.

Runtime defaults come from [`codara.toml`](codara.toml), which is organized into blocks such as `[server]`, `[workspace]`, `[providers.*]`, and `[channels.*]`. Environment variables still override config values, including:

- `API_TOKEN` for operator login (`UAG_MGMT_SECRET` remains a supported fallback alias)
- `UAG_WORKSPACES_ROOT` for provisioned user workspaces
- `UAG_CODEX_STALL_TIMEOUT_SECONDS`, `UAG_GEMINI_STALL_TIMEOUT_SECONDS`, and `UAG_OPENCODE_STALL_TIMEOUT_SECONDS` for killing provider CLIs that are alive but no longer producing stdout/stderr progress
- `UAG_CODEX_AUTH_PATH`, `UAG_GEMINI_AUTH_PATH`, `UAG_OPENCODE_AUTH_PATH` for CLI auth materialization targets
- `UAG_RELEASE_REPOSITORY` and `UAG_RELEASE_CHECK_ENABLED` for GitHub release update checks

Telegram channels support both:
- `receive_mode = "webhook"` for public HTTPS webhook delivery
- `receive_mode = "polling"` for long-polling via Telegram `getUpdates` when you do not have a webhook endpoint

In polling mode, Codara disables any existing Telegram webhook for the bot at startup and stores the per-bot update offset locally so polling can resume after restarts.

Linked Telegram users can create and switch project workspaces directly:

```text
/projects
/project_create news-pulse python
/project_info news-pulse
/project news-pulse
```

## Register accounts

The visible account pool comes from the vault-backed registry. UAG does **not** treat `~/.codex/auth.json` or `~/.gemini/oauth_creds.json` as standalone accounts to display in the dashboard.
Codex is the only provider with managed account registration. Gemini and OpenCode use the locally installed CLI login on the host system and are not imported into the managed account pool.

Examples:

```bash
# Codex OAuth session from an existing auth.json
codara account add \
  --id codex-main \
  --provider codex \
  --auth-type OAUTH_SESSION \
  --label "Codex Main" \
  --credential-file ~/.codex/auth.json

# Raw API key account
printf 'sk-example\n' > /tmp/codex.key
codara account add \
  --id codex-api \
  --provider codex \
  --auth-type API_KEY \
  --label "Codex API" \
  --credential-file /tmp/codex.key
```

When an operator selects a Codex account as CLI-primary, Codara marks it as the preferred managed identity for Codex routing. The managed credential stays in the vault and is injected into the isolated Codex runtime home during execution instead of overwriting the host machine's `~/.codex/auth.json`. Automatic routing still falls back to another ready account when the primary one is expired, cooling down, or close to depletion.

## Create projects

`project` is the user-facing name for a managed workspace. Internally, projects still use the workspace safe-zone, session binding, diff tracking, and reset/delete behavior.

```bash
codara project create news-pulse
codara project create agent-lab --template python --provider codex
codara project list
codara project info news-pulse
```

The default template creates:

```text
README.md
docs/
src/
scripts/
tests/
.codara/project.toml
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
- `GET /management/v1/projects`
- `POST /management/v1/projects`
- `GET /management/v1/workspaces`
- `GET /management/v1/accounts`
- `GET /management/v1/traces`
- `GET /management/v1/logs`
- `GET /management/v1/providers/models`
- `POST /management/v1/playground/chat`
- `POST /management/v1/usage/refresh`
- `GET /management/v1/audit`

Usage/auth refresh activity is written into the audit log, and the audit page supports text search plus actor/action/target filters.
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
    "model": "gpt-5-codex",
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
the standard bearer header plus `provider` and optional `workspace_id` and
`client_session_id`:

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

If quota data has not been observed yet, the management API returns `null` for limits and reset timestamps instead of inventing defaults.

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
- [`docs/accountpool.md`](docs/accountpool.md)
- [`docs/token_usage.md`](docs/token_usage.md)
