# Inference API Reference

Codara provides an OpenAI-compatible inference entry point at `POST /v1/chat/completions`.

## 1. Direct Operator Request

Operators can call the inference API directly by providing an absolute `workspace_root`.

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "uag-codex-v5",
    "messages": [{"role": "user", "content": "Review this repo."}],
    "uag_options": {
        "provider": "codex",
        "workspace_root": "/absolute/path/to/project",
        "client_session_id": "thread-1"
    }
  }'
```

## 2. Provisioned User Request

Provisioned users call the same endpoint using their dedicated API key. The gateway automatically injects the user's bound workspace root.

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer uagk_live_..." \
  -d '{
    "model": "gemini-2.5-pro",
    "messages": [{"role": "user", "content": "Review my changes."}],
    "uag_options": {
        "provider": "gemini",
        "workspace_id": "project-a/feature-x",
        "client_session_id": "thread-1"
    }
  }'
```

### Request Options (`uag_options`)

| Field | Type | Description |
|:---|:---|:---|
| `provider` | string | **Required**. `codex`, `gemini`, or `opencode`. |
| `workspace_root` | string | Absolute path to the workspace. (Operator use only). |
| `workspace_id` | string | Optional sub-selector beneath a user's workspace. |
| `client_session_id`| string | Stable ID for session resumption. |
| `manual_mode` | boolean | If true, returns actions without applying diffs. |

## 3. Response Extensions

In addition to standard OpenAI fields, Codara returns several extension fields:

- `modified_files`: List of paths changed in the workspace during the turn.
- `diff`: Unified diff string of the changes (empty if no changes).
- `actions`: Extracted **ATR** (Agent Tool Result) actions (e.g., search/replace blocks).
- `dirty`: Boolean indicating if the session or workspace state might be inconsistent due to an error.

## 4. Model Aliases

You can use explicit provider models (e.g., `gpt-5-codex`) or `uag-*` aliases.
Aliases resolve to the default models configured in `codara.toml`.

To list available models:
- **User**: `GET /v1/user/providers/models` (requires User API Key)
- **Operator**: `GET /management/v1/providers/models` (requires Operator Token)
