# Observability

Codara provides deep visibility into the interactions between clients, the gateway, and the underlying provider runtimes.

## 1. Traces

Codara captures detailed execution traces for every request.

- **Request ID**: Every call to the gateway is assigned a unique Request ID.
- **Trace Context**: Traces are propagated through the Orchestrator to the Adapters.
- **Persistence**: Traces are stored as individual files under `logs/traces/` for efficient high-volume writes and later retrieval by the dashboard.
- **Visualization**: The "Explorer" page in the dashboard provides a timeline view of these traces.

## 2. Logs

Codara employs a multi-tiered logging strategy:

- **System Logs**: High-level application events (starting services, auth failures) written to standard output and `logs/amesh.log`.
- **Runtime Logs**: Transactional logs for individual agent turns, capturing specific logic branching within the Orchestrator.
- **CLI Capture**: Full capture of `stdout` and `stderr` from the provider CLIs (Gemini, Codex, etc.). These are stored in `logs/cli-runs/` and are crucial for debugging "stalled" or failed agent executions.

## 3. Audit Log

The **Audit Log** tracks all management-level actions performed by operators.

- **Actions**: Creating users, provisioning API keys, deleting workspaces, or resetting sessions.
- **Actors**: Records the specific operator identity (or system process) that performed the action.
- **State Changes**: When possible, it captures the "before" and "after" state of the affected entity.

## 4. Health Monitoring

The gateway exposes a health check endpoint at `/management/v1/health`.

It reports:
- **Database Status**: Connectivity to SQLite.
- **Provider Status**: Availability of local CLI binaries.
- **System Load**: Current task concurrency vs. limits.
- **Disk Usage**: Available space in the workspace volume.
