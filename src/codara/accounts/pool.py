import json
import base64
from datetime import datetime, timedelta, timezone
from typing import Optional, List
from codara.core.models import Account, ProviderType, AuthType
from codara.database.manager import DatabaseManager
from codara.core.models import is_account_enabled_status
from codara.core.security import secrets
from codara.accounts.vault import CredentialVault

class AccountPool:
    _MIN_HEADROOM_PCT = 5.0

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.vault = CredentialVault()

    def acquire_account(self, provider: ProviderType) -> Optional[Account]:
        """Get the single active account for the provider, rotating when headroom is unhealthy."""
        now = datetime.now(timezone.utc)
        candidates = self._eligible_accounts(provider, now)
        if not candidates:
            return None

        current_primary = self.db.get_cli_primary_account(provider)
        if current_primary is not None:
            refreshed_primary = next((account for account in candidates if account.account_id == current_primary.account_id), None)
            if refreshed_primary and self._has_healthy_headroom(refreshed_primary):
                return refreshed_primary

        chosen = self._select_rotation_candidate(candidates)
        return self._promote_cli_primary(chosen) or chosen

    def _eligible_accounts(self, provider: ProviderType, now: datetime) -> list[Account]:
        with self.db._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM accounts
                WHERE provider = ?
                  AND encrypted_credential IS NOT NULL
                  AND COALESCE(inventory_source, 'vault') != 'system'
                  AND status IN ('active', 'ready')
                ORDER BY COALESCE(last_used_at, 0) ASC, account_id ASC
                """,
                (provider.value,),
            ).fetchall()

        candidates: list[Account] = []
        for row in rows:
            account = self.db._row_to_account(row)
            if self._is_account_eligible(account, now):
                candidates.append(account)
        return candidates

    def _select_rotation_candidate(self, candidates: list[Account]) -> Account:
        return sorted(candidates, key=self._account_priority)[0]

    def _promote_cli_primary(self, account: Account) -> Optional[Account]:
        promoted = self.db.set_cli_primary_account(account.account_id)
        if promoted is not None:
            self.activate_for_cli(promoted.account_id)
        return promoted

    def _is_account_eligible(self, account: Account, now: datetime) -> bool:
        if account.cooldown_until:
            cooldown_until = account.cooldown_until
            if cooldown_until.tzinfo is None:
                cooldown_until = cooldown_until.replace(tzinfo=timezone.utc)
            if cooldown_until > now:
                return False

        if self._uses_subscription_quota(account):
            if account.rate_limit_reached is True:
                return False
            if account.rate_limit_allowed is False:
                return False
            if account.hourly_used_pct is not None and account.hourly_used_pct >= 100:
                return False
            if account.weekly_used_pct is not None and account.weekly_used_pct >= 100:
                return False
            return True

        if account.usage_tpm >= account.tpm_limit:
            return False
        if account.usage_rpd >= account.rpd_limit:
            return False
        return True

    def _account_priority(self, account: Account) -> tuple[float, float, int, str]:
        headroom_pct = self._headroom_pct(account)
        remaining_bias = -(headroom_pct if headroom_pct is not None else 101.0)
        last_used = int(account.last_used_at.timestamp()) if account.last_used_at else 0
        return (remaining_bias, last_used, account.account_id)

    def _uses_subscription_quota(self, account: Account) -> bool:
        auth_type_value = getattr(getattr(account, "auth_type", None), "value", str(getattr(account, "auth_type", "")))
        return account.usage_source == "wham" or (account.provider == ProviderType.CODEX and auth_type_value == "OAUTH_SESSION")

    def _headroom_pct(self, account: Account) -> Optional[float]:
        if self._uses_subscription_quota(account):
            used_windows = [
                float(value)
                for value in (account.hourly_used_pct, account.weekly_used_pct)
                if value is not None
            ]
            if not used_windows:
                return None
            return max(0.0, min(100.0 - max(used_windows), 100.0))

        remaining_ratios = []
        if account.tpm_limit:
            remaining_ratios.append(max(0.0, (account.tpm_limit - account.usage_tpm) / float(account.tpm_limit)))
        if account.rpd_limit:
            remaining_ratios.append(max(0.0, (account.rpd_limit - account.usage_rpd) / float(account.rpd_limit)))
        if not remaining_ratios:
            return None
        return max(0.0, min(min(remaining_ratios) * 100.0, 100.0))

    def _has_healthy_headroom(self, account: Account, headroom_pct: Optional[float] = None) -> bool:
        if headroom_pct is None:
            headroom_pct = self._headroom_pct(account)
        if headroom_pct is None:
            return True
        return headroom_pct > self._MIN_HEADROOM_PCT

    def mark_429(self, account_id: str):
        """Handle a 429 response by putting the account into a 60s cooldown."""
        self.mark_cooldown(account_id, duration_seconds=60)

    def mark_cooldown(self, account_id: str, duration_seconds: int = 60):
        """Put an account into cooldown."""
        account = self.db.get_account(account_id)
        if account:
            account.cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)
            account.status = "cooldown"
            self.db.save_account(account)
            if account.cli_primary:
                self.acquire_account(account.provider)

    def release_account(self, account_id: str, tokens_used: int = 0):
        """Update account usage after a turn."""
        account = self.db.get_account(account_id)
        if account:
            account.usage_tpm += tokens_used
            account.usage_rpd += 1
            account.usage_hourly += tokens_used
            account.usage_weekly += tokens_used
            # Simulate reduction in compute time (e.g. 0.1h per turn for now)
            account.remaining_compute_hours = max(0, account.remaining_compute_hours - 0.1)
            account.status = "ready" if is_account_enabled_status(account.status) or account.status == "cooldown" else account.status
            account.last_used_at = datetime.now(timezone.utc)
            self.db.save_account(account)
            if account.cli_primary and not self._has_healthy_headroom(account):
                self.acquire_account(account.provider)

    def register_account(self, account: Account, raw_credential: str):
        """Encrypt and save a new account."""
        raw_credential = self._normalize_credential(account, raw_credential)
        credential_id, auth_index = self._infer_account_metadata(raw_credential)
        account.credential_id = account.credential_id or credential_id or account.account_id
        account.auth_index = account.auth_index or auth_index
        account.access_token_expires_at = self._infer_token_expiry(raw_credential)
        self._persist_credential(account, raw_credential)

    def update_credential(self, account_id: str, raw_credential: str) -> Optional[Account]:
        """Replace encrypted credential for an existing account."""
        account = self.db.get_account(account_id)
        if not account:
            return None
        raw_credential = self._normalize_credential(account, raw_credential)
        credential_id, auth_index = self._infer_account_metadata(raw_credential)
        if credential_id:
            account.credential_id = credential_id
        if auth_index:
            account.auth_index = auth_index
        account.access_token_expires_at = self._infer_token_expiry(raw_credential)
        self._persist_credential(account, raw_credential)
        return account

    def get_credential(self, account_id: str) -> Optional[str]:
        """Get the decrypted credential for an account."""
        account = self.db.get_account(account_id)
        stored_credential = None
        if account and account.encrypted_credential:
            try:
                stored_credential = secrets.decrypt(account.encrypted_credential)
            except Exception:
                pass
        if account:
            if stored_credential is None:
                stored_credential = self.vault.load_credential(account.provider, account.account_id)
            return self._prefer_canonical_cli_credential(account, stored_credential)
        return stored_credential

    def activate_for_cli(self, account_id: str) -> Optional[str]:
        """Copy selected account credential into provider CLI auth path."""
        account = self.db.get_account(account_id)
        if not account:
            return None
        credential = self.get_credential(account_id)
        if not credential:
            return None
        path = self.vault.materialize_to_cli(account.provider, credential)
        return str(path)

    def _persist_credential(self, account: Account, raw_credential: str):
        account.encrypted_credential = secrets.encrypt(raw_credential)
        self.db.save_account(account)
        self.vault.save_credential(account.provider, account.account_id, raw_credential)
        if account.cli_primary:
            self.vault.materialize_to_cli(account.provider, raw_credential)

    def _normalize_credential(self, account: Account, raw_credential: str) -> str:
        if not self._should_consider_cli_auth(account):
            return raw_credential
        cli_credential = self.vault.load_cli_credential(account.provider)
        if not cli_credential:
            return raw_credential
        if self._credential_quality(cli_credential) > self._credential_quality(raw_credential):
            return cli_credential
        return raw_credential

    def _prefer_canonical_cli_credential(self, account: Account, stored_credential: Optional[str]) -> Optional[str]:
        if not self._should_consider_cli_auth(account):
            return stored_credential
        cli_credential = self.vault.load_cli_credential(account.provider)
        if not cli_credential:
            if stored_credential:
                self.vault.materialize_to_cli(account.provider, stored_credential)
            return stored_credential
        cli_quality = self._credential_quality(cli_credential)
        stored_quality = self._credential_quality(stored_credential)
        if cli_quality <= stored_quality:
            if stored_credential and stored_quality > cli_quality:
                self.vault.materialize_to_cli(account.provider, stored_credential)
            return stored_credential
        account.access_token_expires_at = self._infer_token_expiry(cli_credential)
        credential_id, auth_index = self._infer_account_metadata(cli_credential)
        if credential_id:
            account.credential_id = credential_id
        if auth_index:
            account.auth_index = auth_index
        self._persist_credential(account, cli_credential)
        return cli_credential

    def _should_consider_cli_auth(self, account: Account) -> bool:
        return (
            account.provider == ProviderType.CODEX
            and account.auth_type == AuthType.OAUTH_SESSION
            and account.cli_primary
        )

    def _credential_quality(self, credential: Optional[str]) -> int:
        if not credential:
            return -1
        try:
            payload = json.loads(credential)
        except Exception:
            return 0
        if not isinstance(payload, dict):
            return 0

        tokens = payload.get("tokens")
        quality = 0
        if payload.get("auth_mode"):
            quality += 1
        if payload.get("last_refresh"):
            quality += 1
        if isinstance(tokens, dict):
            if isinstance(tokens.get("access_token"), str) and tokens.get("access_token"):
                quality += 2
            if isinstance(tokens.get("refresh_token"), str) and tokens.get("refresh_token"):
                quality += 4
            if isinstance(tokens.get("id_token"), str) and tokens.get("id_token"):
                quality += 4
            if isinstance(tokens.get("account_id"), str) and tokens.get("account_id"):
                quality += 1
        return quality

    def _infer_account_metadata(self, raw_credential: str) -> tuple[Optional[str], Optional[str]]:
        try:
            payload = json.loads(raw_credential)
        except Exception:
            return None, None

        credential_id = None
        auth_index = None

        def walk(value):
            nonlocal credential_id, auth_index
            if isinstance(value, dict):
                for key, item in value.items():
                    lowered = key.lower()
                    if credential_id is None and lowered in {"credential_id", "email", "account", "name", "label"} and isinstance(item, str) and item.strip():
                        credential_id = item.strip()
                    if auth_index is None and lowered in {"auth_index", "authindex"} and isinstance(item, str) and item.strip():
                        auth_index = item.strip()
                    if isinstance(item, (dict, list)):
                        walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(payload)
        return credential_id, auth_index

    def _infer_token_expiry(self, raw_credential: str) -> Optional[datetime]:
        try:
            payload = json.loads(raw_credential)
        except Exception:
            return None

        now = datetime.now(timezone.utc)
        candidates: list[datetime] = []

        def parse_epoch(value) -> Optional[datetime]:
            if isinstance(value, (int, float)):
                ts = int(value)
                if ts > 10_000_000_000:
                    ts = ts // 1000
                if ts > 0:
                    return datetime.fromtimestamp(ts, tz=timezone.utc)
            return None

        def parse_iso(value) -> Optional[datetime]:
            if isinstance(value, str):
                text = value.strip()
                if not text:
                    return None
                if text.endswith("Z"):
                    text = text[:-1] + "+00:00"
                try:
                    dt = datetime.fromisoformat(text)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt.astimezone(timezone.utc)
                except ValueError:
                    return None
            return None

        def decode_jwt_exp(token: str) -> Optional[datetime]:
            parts = token.split(".")
            if len(parts) < 2:
                return None
            payload_b64 = parts[1]
            padding = "=" * (-len(payload_b64) % 4)
            try:
                decoded = base64.urlsafe_b64decode(payload_b64 + padding).decode("utf-8")
                claims = json.loads(decoded)
            except Exception:
                return None
            exp = claims.get("exp")
            return parse_epoch(exp)

        def walk(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    lowered = key.lower()
                    if lowered in {"expires_at", "access_token_expires_at", "expiry", "exp"}:
                        dt = parse_epoch(item) or parse_iso(item)
                        if dt:
                            candidates.append(dt)
                    if lowered == "expires_in" and isinstance(item, (int, float)):
                        candidates.append(now + timedelta(seconds=int(item)))
                    if lowered in {"access_token", "accesstoken", "id_token", "idtoken"} and isinstance(item, str):
                        dt = decode_jwt_exp(item)
                        if dt:
                            candidates.append(dt)
                    if isinstance(item, (dict, list)):
                        walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(payload)
        future_candidates = [c for c in candidates if c > now]
        if future_candidates:
            return min(future_candidates)
        if candidates:
            return max(candidates)
        return None
