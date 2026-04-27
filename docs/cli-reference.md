# CLI Reference

The `amesh` command-line tool provides utilities for starting the gateway and managing workspaces.

## 1. System Commands

### Start the Gateway
```bash
amesh serve --host 0.0.0.0 --port 8000
```
- `--build-ui`: Force a rebuild of the dashboard before starting.

### Check Version
```bash
amesh version
amesh version --check  # Check for updates on GitHub
```

## 2. Workspace Management

Manage isolated agent directories directly from the host.

### Create a Workspace
```bash
amesh workspace create news-pulse
amesh workspace create agent-lab --template python --provider codex
```

**Templates**:
- `default`: Basic structure (README, docs, src, scripts, tests).
- `python`: Python-specific layout.
- `docs`: Documentation-only layout.
- `empty`: Empty directory.

### List and Info
```bash
amesh workspace list
amesh workspace info news-pulse
```

## 3. Environment Overrides

The CLI respects environment variables which override `amesh.toml` settings:

- `API_TOKEN`: Master secret for dashboard/management API.
- `UAG_WORKSPACES_ROOT`: Path to provisioned workspaces.
- `UAG_DATABASE_PATH`: Path to SQLite database.
