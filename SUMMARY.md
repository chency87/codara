# Unified Agent Gateway (UAG) - Feature Summary

The UAG is a robust middleware for stateful, tool-augmented CLI agents.

## Implemented Features

### 1. Core Runtime & Orchestration
- **Async Orchestrator**: Manages session lifecycles, concurrency (semaphores), and workspace isolation.
- **Session Registry**: Persistent SQLite-based storage for session state, context hashes, and thread IDs.
- **Workspace Engine**: Handles file-system snapshots, git-based diffing, and workspace locking.

### 2. Provider Adapters
- **Codex Adapter**: local `codex exec` execution with resumable sessions.
- **Gemini Adapter**: local `gemini` CLI execution with resumable sessions.
- **Provider Abstraction**: Unified `ProviderAdapter` protocol for easy extension.

### 3. ATR (Action Translation & Reconstruction)
- **Manual Mode**: Supports returning searchable/replaceable actions to the client instead of applying them.
- **Regex Extraction**: Parses Aider-style blocks from assistant output.

### 4. Management & Operations
- **Management API**: Full suite of endpoints for health, session control, and user management.
- **Audit Logging**: Immutable record of all operator actions.
- **React Dashboard**: Management UI for system monitoring, workspaces, users, sessions, and observability.

## Project Structure

```
/workspaces/codara/
├── src/codara/
│   ├── core/           # Foundational models, compression, ATR
│   ├── workspace/      # File system engine
│   ├── database/       # SQLite persistence
│   ├── adapters/       # Provider-specific protocols
│   ├── orchestrator/   # Supervisor logic
│   ├── gateway/        # FastAPI application and API routing
│   └── cli/            # Central management CLI
├── ui/                 # React dashboard (Vite)
├── docs/               # System specifications
└── README.md           # Entrypoint documentation
```


## Getting Started

1. **Install dependencies**: `uv pip sync uv.lock`
2. **Start the server**: `codara serve --port 8000`
3. **Run the dashboard**: `cd ui && npm run dev`
