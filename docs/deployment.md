# Deployment & Configuration

Codara is designed to be deployed as a containerized service, typically alongside the provider CLIs it orchestrates.

## 1. Docker Deployment

The recommended way to run Codara is using `docker compose`.

### Quick Start

1. **Clone the repository.**
2. **Create a `.env` file**:
    ```bash
    cp configs/.env.example .env
    # Edit .env and set your API_TOKEN and other preferences
    ```
3. **Configure `amesh.toml`**:
    ```bash
    cp configs/amesh.toml.example configs/amesh.toml
    # Adjust provider settings or resource limits
    ```
4. **Launch**:
    ```bash
    docker compose up --build -d
    ```

### Volume Mapping

The Docker container expects several persistent volumes:
- `/data`: SQLite database (`amesh.db`).
- `/config`: Configuration file (`amesh.toml`).
- `/logs`: Runtime logs and trace shards.
- `/workspaces`: The root directory for all agent workspaces.

## 2. Configuration (`amesh.toml`)

The configuration is organized into functional blocks:

| Section | Description |
|:---|:---|
| `[server]` | Host, port, and security settings (JWT algorithm, etc.). |
| `[database]` | Path to the SQLite database file. |
| `[workspace]` | Root path and lock timeouts. |
| `[logging]` | Log rotation, retention, and CLI capture settings. |
| `[providers.*]` | Specific settings for Codex, Gemini, or OpenCode (e.g., default models). |
| `[telemetry]` | Controls for trace persistence and JSON logging. |

## 3. Environment Variables

Environment variables defined in `.env` override values in `amesh.toml`. Key variables include:

- `API_TOKEN`: The master secret for the management API and dashboard login.
- `REDIS_URL`: (Optional) For high-availability locking or caching.
- `AMESH_HTTP_PORT`: The port on the host machine to bind to (default: `8000`).

## 4. Scaling & Performance

- **Concurrency**: Controlled via `max_concurrency` in `amesh.toml` and per-user limits.
- **Resource Isolation**: Ensure the host machine has sufficient disk space in the `/workspaces` volume for multiple project checkouts.
- **Provider Auth**: The container depends on the host environment's CLI login state. Ensure you run `gemini login` or equivalent within the container if using managed identities.

## 5. Container Registry

Codara images are automatically published to the GitHub Container Registry (GHCR) via GitHub Actions.

- **Latest Image**: `ghcr.io/chency87/amesh:latest`
- **Tagged Releases**: Images are also tagged with semantic versions (e.g., `v0.1.0`) on every release tag.

To pull the latest image:
```bash
docker pull ghcr.io/chency87/amesh:latest
```
