The AccountPool cannot be a static collection of tokens. It must be a dynamic state machine capable of self-healing. Since the access token extracted from a provider OAuth payload typically expires every 60 minutes, the Token Refresh Utility is the heartbeat of system uptime.

The implementation contract is:

1. **Vault-backed inventory.** Accounts shown by UAG come from the encrypted SQLite/vault registry, not by scanning `~/.codex` or `~/.gemini`.
2. **CLI-primary activation.** Provider auth files are runtime activation targets for the selected CLI-primary account, not separate inventory entries.
3. **Single active account rotation.** New work uses one active CLI-primary account at a time. If it is cooling down, expired, rate-limited, or drops to 5% remaining headroom or less, the next healthiest ready account is promoted automatically and becomes the only active account.
4. **Refresh before failure.** OAuth accounts should be refreshed pre-emptively near expiry and reactively on `401/403`.

Operationally, this means:

- the dashboard account pool only shows vault-backed registrations by default,
- system-level CLI auth detected on disk is not treated as a first-class pool member,
- selecting an account updates the runtime auth file for the CLI, while the durable source remains the vault/SQLite record.

Here is the implementation specification for the Automated Identity Refresh (AIR) sub-module.

## 1. The Identity State Machine

> **Note:** This section describes the design model that informed the implementation. The actual implementation uses different patterns (see `src/codara/accounts/pool.py`).

The AccountPool uses a simpler state model than originally designed:

- `ACTIVE` / `READY`: Token is valid and has remaining quota.
- `COOLDOWN`: Account hit a `429` rate limit and is temporarily sidelined.
- `_uses_subscription_quota()` distinguishes between WHAM/OAuth (subscription-based) vs API-key accounts.
- Token refresh is handled by credential extraction at use time with expiry inference from stored credentials.

## 2. Implementation Reference

For the actual implementation, see `src/codara/accounts/pool.py`:

- `acquire_account()` - main entry point for getting an active account
- `_eligible_accounts()` - filters by status and cooldown
- `_has_healthy_headroom()` - checks 5% minimum threshold
- `mark_cooldown()` - handles 429 responses with 60s backoff
- `_headroom_pct()` - calculates remaining quota percentage

For credential handling, see `src/codara/accounts/vault.py` for the vault-backed persistence layer.
    def __init__(self):
        # OpenAI's internal OAuth endpoint used by Codex CLI
        self.oauth_url = "https://auth0.openai.com/oauth/token"
        self.client_id = "pSBy7653hPjmN42D8pP2B45P8A456S" # Standard Codex CLI Client ID

    def refresh_access_token(self, identity: Identity):
        if identity.status == "REFRESHING":
            return False

## 3. Actual Integration (pool.py)

The AccountPool integrates with the Orchestrator through these methods:

| Scenario | Pool Action | Result |
| --- | --- | --- |
| Normal | Account has healthy headroom (>5%). | Use current CLI-primary account, proceed. |
| Low headroom | `< 5%` remaining quota. | Promote next healthiest account to CLI-primary. |
| `401 Unauthorized` | Account selection fails. | Retry with next eligible account. |
| `429 Rate Limit` | `mark_429()` called. | Account status set to `cooldown` for 60s, request routed to next account. |

See `src/codara/accounts/pool.py` for the actual implementation of:
- `acquire_account()` - main selection logic
- `mark_429()` - rate limit handling  
- `release_account()` - usage tracking after turn completion
- `register_account()` - new account credential ingestion with vault encryption
