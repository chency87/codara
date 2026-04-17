# Codex Token Usage Notes

This note explains how Codara thinks about Codex usage credentials and quota polling.

## 1. Source of Truth

For managed Codex accounts, the source of truth is the vault-backed credential stored by Codara, not the host machine's `~/.codex/auth.json`.

Current rule:

- the host `~/.codex/auth.json` may be used as an import source when an operator registers a Codex account
- after import, the managed credential lives in the vault/SQLite account record
- Codara injects that managed credential into the isolated runtime home during execution
- Codara should not treat the host auth file as the canonical runtime copy for managed accounts

If quota values have not been observed yet, the management API returns `null` for limits and reset timestamps instead of inventing placeholder defaults.

## 2. What Token Is Used for Usage Polling

Codara prefers these Codex usage sources in this order:

1. OAuth session token extracted from the stored managed credential
2. API key extracted from the stored managed credential
3. configured billing fallback from `[providers.codex].billing_api_key`

For Codex OAuth usage, the runtime primarily relies on the WHAM quota endpoint.

## 3. Current Codex Usage Endpoints

### Subscription quota view

Primary endpoint:

- `GET https://chatgpt.com/backend-api/wham/usage`

This is the main Codara path for Codex OAuth quota tracking.

Expected header shape:

- `Authorization: Bearer <access_token>`
- `User-Agent: OpenAI-Codex-CLI/...`

### Organization usage fallback

For API-key-style usage collection, Codara can query the organization usage endpoint:

- `GET https://api.openai.com/v1/organization/usage/completions`

## 4. Operational Notes

- Codex OAuth access tokens are short-lived.
- refresh-token handling matters for long-running monitoring.
- live rate-limit and usage-window information from provider responses is more useful than inventing local placeholder counters.

## 5. Workflow Summary

```text
Operator imports Codex auth.json
   │
   ▼
Codara stores the credential in vault + SQLite
   │
   ▼
Usage monitor extracts bearer/API key from the stored credential
   │
   ▼
Monitor queries Codex usage endpoints
   │
   ▼
Account usage fields are updated for routing and dashboard views
```

## 6. Runtime Auth Sync-Back

Codara does not refresh Codex OAuth for every provider turn. The turn path uses
the vault credential to seed the account-scoped isolated `HOME`, then lets the
Codex CLI manage its own local session file during execution.

After a successful Codex turn, Codara reads the isolated `.codex/auth.json`. If
the CLI wrote a valid updated OAuth credential, Codara saves that updated copy
back to the vault and account record. Failed turns do not sync auth back.

This keeps the real host `~/.codex/auth.json` untouched while preventing the
vault copy from becoming stale when the Codex CLI refreshes its own isolated
session state.
