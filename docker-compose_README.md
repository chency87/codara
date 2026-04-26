# Codara Docker Deployment

Use the default `docker-compose.yml` for the current single-node deployment path. It builds one Codara application image, serves the FastAPI backend and built dashboard from the same container, and runs Redis as a local sidecar.

## Quick Start

```bash
cp configs/.env.example .env
cp configs/codara.toml.example codara.toml
# Edit API_TOKEN and any provider/runtime settings.

docker compose up --build
```

Open:

- dashboard: `http://localhost:8000/dashboard`
- health: `http://localhost:8000/management/v1/health`

The container always listens on port `8000`. Change the published host port with `CODARA_HTTP_PORT` in `.env`.

## Published Image

The repository workflow publishes the image to GitHub Container Registry:

```bash
docker pull ghcr.io/chency87/codara:latest
```

To run from the published image instead of building locally, set this in `.env` and remove or ignore the compose `build` block in your deployment override:

```env
CODARA_IMAGE=ghcr.io/chency87/codara:latest
```

GHCR package visibility is controlled in GitHub package settings. After the first publish, mark the package public if anonymous pulls should work.

## Runtime Layout

| Host/Compose volume | Container path | Purpose |
| --- | --- | --- |
| `codara_data` | `/data` | SQLite database |
| `codara_config` plus `./codara.toml` | `/config` | active config, encryption master key, credential vault |
| `codara_logs` | `/logs` | structured runtime logs and trace shards |
| `codara_workspaces` | `/workspaces` | user workspaces |
| `redis_data` | `/data` in Redis | Redis append-only state |

Important environment variables set by compose:

- `UAG_CONFIG_PATH=/config/codara.toml`
- `UAG_CONFIG_DIR=/config`
- `UAG_DATABASE_PATH=/data/codara.db`
- `UAG_LOGS_ROOT=/logs`
- `UAG_WORKSPACES_ROOT=/workspaces`

## Dashboard Build

The Docker image builds `ui/dist` during `docker build` and copies it into `/app/ui/dist`. Refresh-safe dashboard routes such as `/dashboard/workspaces` depend on that bundle containing `index.html`.

For local source changes:

```bash
docker compose build codara
docker compose up
```

## Provider CLIs

Codara executes provider CLIs by command name: `codex`, `gemini`, and `opencode`. The base Docker image installs Node.js 20 plus:

- `@openai/codex`
- `@google/gemini-cli`
- `opencode-ai`

By default compose builds with `@latest` packages:

```env
CODEX_CLI_PACKAGE=@openai/codex@latest
GEMINI_CLI_PACKAGE=@google/gemini-cli@latest
OPENCODE_CLI_PACKAGE=opencode-ai@latest
PNPM_PACKAGE=pnpm@latest
NODE_VERSION=24
NVM_VERSION=0.40.3
```

Pin these values in `.env` for reproducible production images. Keep provider auth inside Codara-managed credentials; do not mount host auth files into `/root`, because the container runs as the non-root `codara` user.

## Operations

```bash
docker compose logs -f codara
docker compose exec codara bash
docker compose restart codara
docker compose down
```

Use `docker compose down -v` only when you intentionally want to delete the database, config vault, logs, workspaces, and Redis state.

## Security Notes

- Replace `API_TOKEN` in `.env` before exposing the service.
- Do not bake `codara.toml`, `.env`, provider auth, database files, logs, or workspaces into the image.
- Put TLS termination in a reverse proxy in front of `codara` for public deployments.
- Back up both `codara_data` and `codara_config`; the database and encryption key/credential vault are both required to recover managed accounts.
