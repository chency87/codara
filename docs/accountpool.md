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

Each account in the AccountPool should follow a specific state lifecycle:

- `ACTIVE`: Token is valid and has remaining quota.
- `EXPIRED`: Access token is invalid, but refresh token is available.
- `REFRESHING`: A background lock is held to prevent multiple threads from refreshing the same account.
- `COOLDOWN`: The account hit a `429` rate limit and is temporarily sidelined.

## 2. Automated Token Refresh Logic

The utility must handle both pre-emptive (before expiry) and reactive (on `401 Unauthorized`) refreshes.

Identity Data Structure

Python
from pydantic import BaseModel
from datetime import datetime, timedelta

class Identity(BaseModel):
    account_id: str
    access_token: str
    refresh_token: str
    expires_at: datetime
    status: str = "ACTIVE"
    
    def is_expired(self, buffer_minutes: int = 5):
        # Trigger refresh 5 minutes before actual expiry to prevent race conditions
        return datetime.now() + timedelta(minutes=buffer_minutes) >= self.expires_at
The Refresh Utility (AIR_Service)Pythonimport requests
import time

class TokenRefreshService:
    def __init__(self):
        # OpenAI's internal OAuth endpoint used by Codex CLI
        self.oauth_url = "https://auth0.openai.com/oauth/token"
        self.client_id = "pSBy7653hPjmN42D8pP2B45P8A456S" # Standard Codex CLI Client ID

    def refresh_access_token(self, identity: Identity):
        if identity.status == "REFRESHING":
            return False

        identity.status = "REFRESHING"
        payload = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "refresh_token": identity.refresh_token
        }

        try:
            response = requests.post(self.oauth_url, json=payload, timeout=10)
            response.raise_for_status()
            
            new_data = response.json()
            identity.access_token = new_data["access_token"]
            # Some providers rotate the refresh_token too
            identity.refresh_token = new_data.get("refresh_token", identity.refresh_token)
            identity.expires_at = datetime.now() + timedelta(seconds=new_data["expires_in"])
            identity.status = "ACTIVE"
            
            # Persist back to the UAG Session Store/auth.json
            self._persist_identity(identity)
            return True

        except Exception as e:
            identity.status = "BROKEN"
            print(f"CRITICAL: Failed to refresh account {identity.account_id}: {e}")
            return False

    def _persist_identity(self, identity: Identity):
        # Logic to sync back to disk (auth.json) or the UAG SQLite Registry
        pass
## 3. Integration with the Orchestrator

To ensure zero-downtime execution, the Orchestrator should interact with the AccountPool using a safe-check wrapper before every agent turn.

| Scenario | Orchestrator Action | Result |
| --- | --- | --- |
| Normal | Token is valid. | Request proceeds immediately. |
| Near-expiry | Trigger `AIR_Service` in the background. | Request proceeds; next request gets the refreshed token. |
| `401 Unauthorized` | Block current thread, run `refresh_access_token`. | Request is retried once with the new token. |
| `429 Rate Limit` | Set status to `COOLDOWN`. | Request is routed to a different identity in the pool. |
