# Copilot Instructions

## Build, test, and lint commands

### Python backend

- Install/sync dependencies: `uv pip sync uv.lock`
- Install the package for local CLI use: `pip install -e .`
- Run the full Python test suite: `pytest -q`
- Run one test file: `pytest -q tests/test_account_pool.py`
- Run one test case: `pytest -q tests/test_account_pool.py::test_cli_primary_falls_back_when_headroom_is_nearly_exhausted`
- Start the API gateway and dashboard: `amesh serve --port 8000`

### Dashboard (`ui/`)

- Install UI dependencies if needed: `cd ui && npm install`
- Run the dashboard dev server: `cd ui && npm run dev`
- Build the dashboard: `cd ui && npm run build`
- Lint the dashboard: `cd ui && npm run lint`

## High-level architecture

- `src/amesh/gateway/app.py` is the main entrypoint. It exposes:
  - OpenAI-compatible inference at `/v1/chat/completions`
  - operator management APIs under `/management/v1/*`
  - user self-service APIs under `/v1/user/*`
  - token issuance and user/operator auth handling
- `src/amesh/orchestrator/engine.py` is the runtime coordinator. A request flows through session lookup, workspace setup, account selection, adapter dispatch, workspace diffing, turn persistence, and usage accounting.
- `src/amesh/database/manager.py` is the SQLite source of truth. It owns schema creation and CRUD for accounts, sessions, audit logs, turns, users, API keys, and workspace resets.
- `src/amesh/accounts/pool.py` manages registered provider credentials. It selects accounts by readiness/quota headroom, stores encrypted credentials in SQLite plus the local vault, and materializes the selected credential into provider CLI auth paths when needed.
- `src/amesh/accounts/monitor.py` refreshes account usage and token state by delegating to provider adapters, then writes the results back into the account records.
- Provider-specific behavior lives in `src/amesh/adapters/`. The orchestrator talks to adapters through a shared interface; adapters translate between the UAG session model and the provider CLI/runtime protocol.
- `src/amesh/workspace/engine.py` treats the workspace as part of runtime state. It snapshots file trees, acquires a `.uag_lock`, and generates diffs via git or file hashing.
- The React dashboard in `ui/` is a first-party client of the management API. It does not have a separate backend.

## Key conventions

- Follow `AGENTS.md` for non-trivial work. The repository expects the compound-engineering workflow:
  1. read the relevant `.agents` rules and learnings
  2. write/update a plan under `.agents/plans/`
  3. implement in small, testable increments
  4. capture durable learnings afterward
- Keep changes surgical and aligned with the existing layout. Prefer updating existing modules over introducing new top-level structure.
- For Python changes, run the narrowest relevant `pytest` target from the repository root. For UI changes, use the commands inside `ui/`.
- User-authenticated inference requests do not directly trust arbitrary workspaces. The gateway resolves them into the user’s provisioned workspace tree and namespaces sessions as `user_id::workspace_id::session_label`.
- Account inventory is expected to come from the database/vault-backed pool. Provider CLI auth files are runtime materialization targets, not a separate source of durable account records.
- Preserve honest observability contracts. When usage or quota values are unknown, the codebase prefers returning `null`/unknown instead of inventing defaults for the dashboard.
