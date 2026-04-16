import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import httpx

from codara.accounts.pool import AccountPool
from codara.adapters.codex import CodexAdapter
from codara.adapters.gemini import GeminiAdapter
from codara.adapters.opencode import OpenCodeAdapter
from codara.config import Settings, get_settings
from codara.core.models import AuthType, ProviderType
from codara.database.manager import DatabaseManager
from codara.telemetry import record_event, start_span

logger = logging.getLogger(__name__)


class UsageMonitor:
    """Background task that delegates provider usage collection to adapters."""

    def __init__(self, db_manager: DatabaseManager, interval_seconds: int = 60, settings: Settings | None = None):
        self.db = db_manager
        self.pool = AccountPool(db_manager)
        self.interval = interval_seconds
        self.settings = settings or get_settings()
        self._running = False
        self._refresh_locks: dict[str, asyncio.Lock] = {}
        self.adapters = {
            ProviderType.CODEX: CodexAdapter(db_manager),
            ProviderType.GEMINI: GeminiAdapter(),
            ProviderType.OPENCODE: OpenCodeAdapter(),
        }

    def _record_event(self, action: str, account_id: str, before: dict | None = None, after: dict | None = None):
        self.db.record_audit(
            actor="system:usage-monitor",
            action=action,
            target_type="account",
            target_id=account_id,
            before=before,
            after=after,
        )

    def _usage_summary(self, usage: dict | None) -> dict | None:
        if not usage:
            return None
        fields = (
            "status",
            "usage_source",
            "hourly_used_pct",
            "weekly_used_pct",
            "usage_hourly",
            "usage_weekly",
            "usage_tpm",
            "usage_rpd",
            "hourly_reset_after_seconds",
            "weekly_reset_after_seconds",
        )
        return {key: usage.get(key) for key in fields if key in usage}

    async def start(self):
        self._running = True
        asyncio.create_task(self._run_loop())
        logger.info("UsageMonitor started with interval %ss", self.interval)

    async def stop(self):
        self._running = False

    async def _run_loop(self):
        while self._running:
            try:
                await self.sync_all_accounts()
            except Exception as exc:
                logger.error("Error in UsageMonitor sync: %s", exc)
            await asyncio.sleep(self.interval)

    async def sync_all_accounts(self, max_concurrency: int | None = None):
        concurrency = max(1, int(max_concurrency or 5))
        sem = asyncio.Semaphore(concurrency)
        accounts = self.db.get_all_accounts()
        async with start_span(
            "usage_monitor.sync_all_accounts",
            component="usage.monitor",
            db=self.db,
            attributes={"account_count": len(accounts), "max_concurrency": concurrency},
        ):
            async def _sync_one(account):
                async with sem:
                    try:
                        await self._sync_with_adapter(account, account.provider)
                    except Exception as exc:
                        self._record_event(
                            "usage.sync.failed",
                            account.account_id,
                            before={"provider": account.provider.value, "status": account.status},
                            after={"error": str(exc)},
                        )
                        record_event(
                            "usage.sync.failed",
                            component="usage.monitor",
                            db=self.db,
                            level="ERROR",
                            status="error",
                            attributes={"account_id": account.account_id, "provider": account.provider.value, "error": str(exc)},
                        )
                        logger.warning("Failed to sync usage for %s: %s", account.account_id, exc)

            await asyncio.gather(*(_sync_one(a) for a in accounts))

    async def _sync_codex(self, account):
        await self._sync_with_adapter(account, ProviderType.CODEX)

    async def _sync_gemini(self, account):
        await self._sync_with_adapter(account, ProviderType.GEMINI)

    async def _sync_with_adapter(self, account, provider: ProviderType):
        adapter = self.adapters.get(provider)
        if not adapter:
            return
        record_event(
            "usage.fetch.started",
            component="usage.monitor",
            db=self.db,
            attributes={"account_id": account.account_id, "provider": provider.value, "status": account.status},
        )
        self._record_event(
            "usage.fetch.started",
            account.account_id,
            before={"provider": provider.value, "status": account.status},
        )
        if (
            provider == ProviderType.CODEX
            and account.auth_type == AuthType.OAUTH_SESSION
            and self._token_needs_refresh(account)
        ):
            await self._refresh_codex_oauth_session(account.account_id)
            account = self.db.get_account(account.account_id) or account
        credential = self.pool.get_credential(account.account_id)
        usage = await adapter.collect_usage(account, credential, self.settings)

        # OAuth refresh retry for Codex when adapter reports auth failure.
        if (
            provider == ProviderType.CODEX
            and account.auth_type == AuthType.OAUTH_SESSION
            and isinstance(usage, dict)
            and usage.get("auth_failed")
        ):
            refreshed = await self._refresh_codex_oauth_session(account.account_id)
            if refreshed:
                credential = self.pool.get_credential(account.account_id)
                usage = await adapter.collect_usage(account, credential, self.settings)

        if not usage:
            record_event(
                "usage.fetch.failed",
                component="usage.monitor",
                db=self.db,
                level="WARNING",
                status="error",
                attributes={"account_id": account.account_id, "provider": provider.value, "error": "adapter returned no data"},
            )
            self._record_event(
                "usage.fetch.failed",
                account.account_id,
                before={"provider": provider.value, "status": account.status},
                after={"error": "adapter returned no data"},
            )
            logger.warning("Usage sync skipped for %s: adapter returned no data", account.account_id)
            return
        self._apply_usage_result(account, usage)
        current = self.db.get_account(account.account_id) or account
        record_event(
            "usage.fetch.succeeded",
            component="usage.monitor",
            db=self.db,
            status="ok",
            attributes={
                "account_id": account.account_id,
                "provider": provider.value,
                "status": current.status,
                **(self._usage_summary(usage) or {}),
            },
        )
        self._record_event(
            "usage.fetch.succeeded",
            account.account_id,
            before={"provider": provider.value},
            after={
                **(self._usage_summary(usage) or {}),
                "status": current.status,
                "last_seen_at": current.last_seen_at.isoformat() if current.last_seen_at else None,
            },
        )

    def _apply_usage_result(self, account, usage: dict):
        now = datetime.now(timezone.utc)
        current = self.db.get_account(account.account_id) or account
        current.last_seen_at = now

        status = usage.get("status")
        if isinstance(status, str) and status:
            current.status = status
        if status == "cooldown":
            current.cooldown_until = now + timedelta(minutes=1)
        elif status in {"ready", "active"}:
            current.cooldown_until = None

        # Absolute counters
        if isinstance(usage.get("usage_hourly"), (int, float)):
            current.usage_hourly = int(usage["usage_hourly"])
        if isinstance(usage.get("usage_tpm"), (int, float)):
            current.usage_tpm = int(usage["usage_tpm"])
        if isinstance(usage.get("usage_weekly"), (int, float)):
            current.usage_weekly = int(usage["usage_weekly"])
        if isinstance(usage.get("usage_rpd"), (int, float)):
            current.usage_rpd = int(usage["usage_rpd"])

        # Provider-advertised limits
        if isinstance(usage.get("hourly_limit"), (int, float)) and usage["hourly_limit"] > 0:
            current.hourly_limit = int(usage["hourly_limit"])
        if isinstance(usage.get("weekly_limit"), (int, float)) and usage["weekly_limit"] > 0:
            current.weekly_limit = int(usage["weekly_limit"])

        # Percentage-based windows (e.g. Codex OAuth wham usage)
        if isinstance(usage.get("hourly_used_pct"), (int, float)):
            current.hourly_used_pct = float(usage["hourly_used_pct"])
            if current.hourly_limit > 0:
                current.usage_hourly = max(0, min(current.hourly_limit, int(round(current.hourly_limit * current.hourly_used_pct / 100.0))))
        if isinstance(usage.get("weekly_used_pct"), (int, float)):
            current.weekly_used_pct = float(usage["weekly_used_pct"])
            if current.weekly_limit > 0:
                current.usage_weekly = max(0, min(current.weekly_limit, int(round(current.weekly_limit * current.weekly_used_pct / 100.0))))

        if isinstance(usage.get("hourly_reset_after_seconds"), (int, float)):
            current.hourly_reset_after_seconds = int(usage["hourly_reset_after_seconds"])
        if isinstance(usage.get("weekly_reset_after_seconds"), (int, float)):
            current.weekly_reset_after_seconds = int(usage["weekly_reset_after_seconds"])
        if isinstance(usage.get("hourly_reset_at"), (int, float)):
            current.hourly_reset_at = datetime.fromtimestamp(int(usage["hourly_reset_at"]), tz=timezone.utc)
        if isinstance(usage.get("weekly_reset_at"), (int, float)):
            current.weekly_reset_at = datetime.fromtimestamp(int(usage["weekly_reset_at"]), tz=timezone.utc)

        if isinstance(usage.get("usage_source"), str):
            current.usage_source = usage["usage_source"]
        if isinstance(usage.get("plan_type"), str):
            current.plan_type = usage["plan_type"]
        if isinstance(usage.get("rate_limit_allowed"), bool):
            current.rate_limit_allowed = usage["rate_limit_allowed"]
        if isinstance(usage.get("rate_limit_reached"), bool):
            current.rate_limit_reached = usage["rate_limit_reached"]
        if isinstance(usage.get("credits_has_credits"), bool):
            current.credits_has_credits = usage["credits_has_credits"]
        if isinstance(usage.get("credits_unlimited"), bool):
            current.credits_unlimited = usage["credits_unlimited"]
        if isinstance(usage.get("credits_overage_limit_reached"), bool):
            current.credits_overage_limit_reached = usage["credits_overage_limit_reached"]
        if isinstance(usage.get("approx_local_messages_min"), (int, float)):
            current.approx_local_messages_min = int(usage["approx_local_messages_min"])
        if isinstance(usage.get("approx_local_messages_max"), (int, float)):
            current.approx_local_messages_max = int(usage["approx_local_messages_max"])
        if isinstance(usage.get("approx_cloud_messages_min"), (int, float)):
            current.approx_cloud_messages_min = int(usage["approx_cloud_messages_min"])
        if isinstance(usage.get("approx_cloud_messages_max"), (int, float)):
            current.approx_cloud_messages_max = int(usage["approx_cloud_messages_max"])

        if isinstance(usage.get("credits_balance"), (int, float)):
            current.remaining_compute_hours = float(usage["credits_balance"])

        self.db.save_account(current)

    def _token_needs_refresh(self, account, threshold_seconds: int = 300) -> bool:
        expiry = getattr(account, "access_token_expires_at", None)
        if not expiry:
            return False
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return expiry <= datetime.now(timezone.utc) + timedelta(seconds=threshold_seconds)

    def _resolve_billing_token(self, credential: str | None) -> str | None:
        """Backward-compatible token extractor kept for tests and simple providers."""
        if not credential:
            return None
        stripped = credential.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                return None

            candidates = {
                "OPENAI_API_KEY",
                "GEMINI_API_KEY",
                "api_key",
                "apiKey",
                "access_token",
                "accessToken",
                "token",
                "bearer_token",
                "bearerToken",
                "auth_token",
                "authToken",
            }

            def walk(value):
                if isinstance(value, dict):
                    for key, item in value.items():
                        if key in candidates and isinstance(item, str) and item.strip():
                            return item.strip()
                        found = walk(item)
                        if found:
                            return found
                elif isinstance(value, list):
                    for item in value:
                        found = walk(item)
                        if found:
                            return found
                return None

            return walk(payload)

        if " " in stripped or "\n" in stripped:
            return None
        return stripped

    async def _refresh_codex_oauth_session(self, account_id: str) -> bool:
        lock = self._refresh_locks.setdefault(account_id, asyncio.Lock())
        async with lock:
            return await self._refresh_codex_oauth_session_locked(account_id)

    async def _refresh_codex_oauth_session_locked(self, account_id: str) -> bool:
        raw = self.pool.get_credential(account_id)
        if not raw:
            self._record_event("account.auth.refresh.failed", account_id, after={"error": "missing credential"})
            return False
        account = self.db.get_account(account_id)
        self._record_event(
            "account.auth.refresh.started",
            account_id,
            before={"status": account.status if account else None},
        )
        try:
            payload = json.loads(raw)
        except Exception:
            if account:
                account.status = "error"
                self.db.save_account(account)
            self._record_event("account.auth.refresh.failed", account_id, after={"error": "invalid credential payload"})
            return False

        refresh_token = self._extract_refresh_token(payload)
        if not refresh_token:
            if account:
                account.status = "expired"
                self.db.save_account(account)
            self._record_event("account.auth.refresh.failed", account_id, after={"error": "missing refresh_token", "status": "expired"})
            return False

        oauth_url = getattr(self.settings, "codex_oauth_url", "https://auth0.openai.com/oauth/token")
        client_id = "pSBy7653hPjmN42D8pP2B45P8A456S"
        request_body = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(oauth_url, json=request_body)
            if resp.status_code != 200:
                if account:
                    account.status = "error"
                    self.db.save_account(account)
                self._record_event(
                    "account.auth.refresh.failed",
                    account_id,
                    after={"error": f"oauth refresh returned {resp.status_code}", "status": "error"},
                )
                return False
            data = resp.json()

        new_access = data.get("access_token")
        if not isinstance(new_access, str) or not new_access.strip():
            if account:
                account.status = "error"
                self.db.save_account(account)
            self._record_event("account.auth.refresh.failed", account_id, after={"error": "missing access_token", "status": "error"})
            return False

        new_id_token = data.get("id_token")

        if isinstance(payload.get("tokens"), dict):
            payload["tokens"]["access_token"] = new_access
            if new_id_token:
                payload["tokens"]["id_token"] = new_id_token
            if data.get("refresh_token"):
                payload["tokens"]["refresh_token"] = data["refresh_token"]
        else:
            payload["access_token"] = new_access
            if new_id_token:
                payload["id_token"] = new_id_token
            if data.get("refresh_token"):
                payload["refresh_token"] = data["refresh_token"]
        payload["last_refresh"] = datetime.now(timezone.utc).isoformat()

        updated = self.pool.update_credential(account_id, json.dumps(payload))
        if updated:
            updated.status = "ready"
            updated.cooldown_until = None
            self.db.save_account(updated)
            self._record_event(
                "account.auth.refresh.succeeded",
                account_id,
                after={
                    "status": "ready",
                    "access_token_expires_at": updated.access_token_expires_at.isoformat() if updated.access_token_expires_at else None,
                },
            )
        return True

    def _extract_refresh_token(self, payload: dict) -> str | None:
        candidates = {"refresh_token", "refreshToken"}

        def walk(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in candidates and isinstance(item, str) and item.strip():
                        return item.strip()
                    found = walk(item)
                    if found:
                        return found
            elif isinstance(value, list):
                for item in value:
                    found = walk(item)
                    if found:
                        return found
            return None

        return walk(payload)
