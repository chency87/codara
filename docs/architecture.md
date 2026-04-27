# System Architecture

Codara is built as a modular gateway that coordinates between external API clients, internal orchestration logic, and local CLI-native provider runtimes.

## Component Overview

```mermaid
graph TD
    Client[API Clients / SDKs] --> Gateway[FastAPI Gateway]
    Gateway --> Auth[Auth & Token Validation]
    Gateway --> InfService[Inference Service]
    Gateway --> ChannelService[Channel Service]
    
    InfService --> Orchestrator[Orchestrator Engine]
    ChannelService --> Orchestrator
    
    Orchestrator --> WSEngine[Workspace Engine]
    Orchestrator --> Adapters[Provider Adapters]
    
    Adapters --> CLI[Local CLIs: Gemini / Codex / OpenCode]
    
    Orchestrator --> SQLite[(SQLite Persistence)]
    Orchestrator --> Logs[(File-backed Logs & Traces)]
```

## Request Flow

The following diagram illustrates the lifecycle of a standard inference request:

```mermaid
sequenceDiagram
    participant C as Client
    participant G as Gateway
    participant O as Orchestrator
    participant W as Workspace Engine
    participant A as Provider Adapter
    participant P as Local CLI
    
    C->>G: POST /v1/chat/completions (with API Key)
    G->>G: Validate API Key & Resolve User
    G->>O: handle_request(messages, options)
    
    O->>O: Acquire Session & User Locks
    O->>W: Acquire Workspace Lock
    W->>W: Take Filesystem Snapshot (if not Git)
    
    O->>A: send_turn(session, messages)
    A->>P: Execute CLI (gemini/codex/opencode)
    P-->>A: CLI Output + Backend ID
    A-->>O: TurnResult
    
    O->>W: generate_diff()
    W-->>O: Modified Files + Unified Diff
    
    O->>O: Extract ATR Actions
    O->>O: Persist Session, Task & Turn State
    
    O-->>G: Final TurnResult
    G-->>C: OpenAI-compatible JSON Response
```

## Internal Layer Responsibilities

### 1. Gateway Layer
- **FastAPI Application**: Serves as the web entry point.
- **Request Shaping**: Normalizes OpenAI-style requests into internal `UagOptions`.
- **Security**: Handles JWT-based dashboard auth and API-key-based user auth.

### 2. Orchestration Layer
- **Concurrency Control**: Uses semaphores and per-session/per-user locks to prevent race conditions.
- **Task Management**: Creates and tracks the lifecycle of every request as a `Task`.
- **State Persistence**: Interfaces with SQLite to store long-lived metadata.

### 3. Execution Layer
- **Provider Adapters**: Specialized wrappers for different LLM runtimes (Gemini, Codex, OpenCode). They communicate with local CLIs via subprocesses.
- **Workspace Engine**: Manages the directory where agents work. It handles locking, Git integration, and filesystem diffing.

### 4. Persistence Layer
- **SQLite**: Primary store for users, workspaces, sessions, tasks, and audit logs.
- **Log Shards**: Transactional execution logs and traces stored as individual files for high-volume observability.
