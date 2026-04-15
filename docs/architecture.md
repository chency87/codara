# UAG Architecture Detail

This document explains the internal runtime engine of the Unified Agent Gateway.

## 1. Request Lifecycle

Every request follows a canonical flow:

1.  **Gateway Ingress**: Validates `uag_options` and checks auth.
2.  **Session Lookup**: Resumes existing session state from the `SessionRegistry` (SQLite).
3.  **Account Selection**: Fetches a rate-limit-aware account from the `AccountPool`.
4.  **Orchestrator Dispatch**: Serializes requests for the same session and gates global concurrency via a semaphore.
5.  **Workspace Snapshot**: Captures file system state (git status or file hashes).
6.  **CLI Execution**: Spawns the provider adapter (e.g., Codex) as a subprocess.
7.  **Workspace Diff**: Compares post-execution state with the snapshot to generate a unified diff.
8.  **State Persistence**: Updates the `backend_id` and session timestamps in the registry.

## 2. Core Components

### Orchestrator Runtime
The central supervisor managing the lifecycle of CLI-based agents. It ensures single-writer access to a workspace per session through internal locks.

### Provider Adapters
Adapters translate the UAG JSON payload into provider-native CLI execution flows:
- **Codex**: local `codex exec` subprocesses with isolated credential materialization.
- **Gemini**: local `gemini` CLI subprocesses using the host login state.
- **OpenCode**: local `opencode run` subprocesses using the host login state.

### Workspace Engine
Handles all file-level operations. It uses `git diff` when available and falls back to recursive file hash comparison. It also implements workspace locking via `.uag_lock` files to prevent concurrent modification by different sessions or external actors.

### SessionRegistry & AccountPool
Persistence layers using SQLite to ensure that conversation context and provider credentials survive gateway restarts.

## 3. Session Metadata

- **Workspace Hash Tracking**: Stores a hash of the current workspace tree on the session row for bookkeeping and inspection. The live runtime does not yet implement a prompt-rewrite or provider cache-optimization layer on top of this value.
