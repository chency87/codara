# Codara Docker Deployment

This directory contains Docker configuration files for deploying the Unified Agent Gateway (UAG).

## Quick Start

### 1. Prerequisites

- Docker Engine 24.0+
- Docker Compose v2.20+

### 2. Configuration

Copy the example environment file and configure:

```bash
cp .env.example .env
# Edit .env with your settings
```

### 3. Build & Run

#### Development
```bash
docker-compose -f docker-compose.dev.yml up --build
```

#### Production
```bash
docker-compose -f docker-compose.yml up --build
```

Or with production services:
```bash
docker-compose -f docker-compose.prod.yml up --build
```

## File Structure

| File | Description |
|------|-------------|
| `Dockerfile` | Multi-stage build for the UAG application |
| `docker-compose.yml` | Default compose file (SQLite + Redis) |
| `docker-compose.dev.yml` | Development configuration |
| `docker-compose.prod.yml` | Production with Nginx + SSL |
| `.env.example` | Environment variable template |
| `nginx.conf` | Nginx configuration for reverse proxy |
| `.dockerignore` | Build context exclusions |

## Services

### Codara (Main Application)
- Port: `8000` (configurable via `UAG_PORT`)
- Health check: `/management/v1/health`
- Dashboard: `/dashboard`

### Redis
- Port: `6379` (configurable via `REDIS_PORT`)
- Used for session locks and hot cache

### PostgreSQL (Optional)
- Port: `5432`
- For production, uncomment in `docker-compose.prod.yml`

### Nginx (Production)
- Port: `80` (HTTP)
- Port: `443` (HTTPS, if SSL configured)

## Volume Mounts

| Volume | Container Path | Description |
|--------|----------------|-------------|
| `codara_data` | `/data` | SQLite database |
| `codara_workspaces` | `/workspaces` | User workspaces |

## Provider Credentials

Mount provider auth files:

```yaml
volumes:
  - ${CODEX_AUTH_PATH:-~/.codex/auth.json}:/root/.codex/auth.json:ro
  - ${GEMINI_AUTH_PATH:-~/.gemini/oauth_creds.json}:/root/.gemini/oauth_creds.json:ro
```

## SSL/TLS (Production)

1. Place SSL certificates in `./ssl/` directory:
   - `cert.pem` - SSL certificate
   - `key.pem` - Private key

2. Uncomment the HTTPS server section in `nginx.conf`

3. Use `docker-compose.prod.yml`

## Management

### View Logs
```bash
docker-compose logs -f codara
```

### Stop Services
```bash
docker-compose down
```

### Rebuild
```bash
docker-compose build --no-cache
```

### Access Container Shell
```bash
docker-compose exec codara /bin/bash
```

## Security Notes

- Always change `UAG_MGMT_SECRET` and `API_TOKEN` in production
- Use SSL/TLS in production (via Nginx)
- Run as non-root user (already configured in Dockerfile)
- Keep database and credentials in volumes, not in image
