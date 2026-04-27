# amesh: Agent Mesh Runtime for ACP & CLI Agents

amesh is a high-performance, stateful gateway that bridges OpenAI-compatible clients with CLI-native agents like Codex, Gemini, and OpenCode. It reuses session state, manages isolated workspaces, and provides an operator dashboard.

## Key Features

- **Session Persistence**: Persistent turns with context reuse.
- **Workspace Isolation**: Automated filesystem management for agents.
- **Operator Dashboard**: Full control plane for users, sessions, and logs.
- **OpenAI Compatible**: Drop-in replacement for existing LLM integrations.

## Documentation

- **[System Overview](docs/index.md)**: High-level features and capabilities.
- **[Inference API](docs/inference-api.md)**: Usage guide for `POST /v1/chat/completions`.
- **[Architecture](docs/architecture.md)**: System diagrams and request flows.
- **[Deployment](docs/deployment.md)**: Docker and configuration guide.
- **[CLI Reference](docs/cli-reference.md)**: Command-line utilities for system and workspaces.
- **[Core Concepts](docs/concepts.md)**: Deep dive into Sessions, Tasks, and Workspaces.
- **[Management API](docs/management-api.md)**: Reference for the control plane.

## Quick Start

### 1. Install

```bash
uv sync --extra dev
pip install -e .
```

### 2. Configure

```bash
cp configs/amesh.toml.example amesh.toml
cp configs/.env.example .env
# Edit .env and set your API_TOKEN
```

### 3. Start

```bash
# Start the backend gateway
amesh serve --port 8000

# (Optional) Build and serve the dashboard
cd ui && npm install && npm run build
```

## Run with Docker

```bash
docker compose up --build -d
```

Access the dashboard at `http://localhost:8000/dashboard` using your `API_TOKEN`.

## Development

```bash
# Run tests
pytest

# UI Dev Server
cd ui && npm run dev
```
