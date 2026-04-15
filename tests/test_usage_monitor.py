import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import codara.accounts.monitor as monitor_module
import codara.adapters.codex as codex_adapter_module
import codara.gateway.app as gateway_app
from codara.accounts.monitor import UsageMonitor
from codara.accounts.pool import AccountPool
from codara.core.models import Account, AuthType, ProviderType
from codara.database.manager import DatabaseManager


def test_usage_monitor_syncs_oauth_and_api_key_accounts(tmp_path, monkeypatch):
    db_path = tmp_path / "codara.db"

    gateway_app.db_manager = DatabaseManager(str(db_path))
    pool = AccountPool(gateway_app.db_manager)
    pool.register_account(
        Account(
            account_id="codex-oauth",
            provider=ProviderType.CODEX,
            auth_type=AuthType.OAUTH_SESSION,
            label="Codex OAuth",
        ),
        '{"tokens":{"access_token":"oauth-token"}}',
    )
    pool.register_account(
        Account(
            account_id="codex-prod-01",
            provider=ProviderType.CODEX,
            auth_type=AuthType.API_KEY,
            label="Prod Codex",
        ),
        "sk-prod-credential",
    )

    gateway_app.usage_monitor = UsageMonitor(gateway_app.db_manager)
    monitor = gateway_app.usage_monitor
    touched = []

    async def fake_sync_with_adapter(account, provider):
        touched.append(account.account_id)

    monkeypatch.setattr(monitor, "_sync_with_adapter", fake_sync_with_adapter)

    import asyncio

    asyncio.run(monitor.sync_all_accounts())

    assert touched == ["codex-oauth", "codex-prod-01"]


def test_usage_monitor_extracts_billing_token_from_json_blob():
    monitor = UsageMonitor(DatabaseManager(":memory:"))

    blob = """
    {
      "auth_mode": "chatgpt",
      "OPENAI_API_KEY": "sk-test-123",
      "nested": {
        "token": "ignored"
      }
    }
    """

    assert monitor._resolve_billing_token(blob) == "sk-test-123"
    assert monitor._resolve_billing_token("sk-raw-token") == "sk-raw-token"
    assert monitor._resolve_billing_token("{\"OPENAI_API_KEY\": null}") is None
    assert monitor._resolve_billing_token(
        """
        {
          "tokens": {
            "access_token": "sk-access-456",
            "refresh_token": "refresh-only"
          }
        }
        """
    ) == "sk-access-456"


def test_usage_monitor_uses_configured_codex_billing_key_for_oauth_accounts(tmp_path, monkeypatch):
    db_path = tmp_path / "codara.db"
    db = DatabaseManager(str(db_path))
    account = Account(
        account_id="codex-oauth",
        provider=ProviderType.CODEX,
        auth_type=AuthType.OAUTH_SESSION,
        label="Codex OAuth",
    )
    db.save_account(account)
    pool = AccountPool(db)
    pool.register_account(account, '{"auth_mode":"chatgpt","OPENAI_API_KEY":null}')

    captured_headers = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"data": [{"results": [{"input_tokens": 3, "request_count": 1}]}]}

    class FakeClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None, params=None):
            captured_headers.append(headers["Authorization"])
            return FakeResponse()

    monkeypatch.setattr(codex_adapter_module.httpx, "AsyncClient", FakeClient)

    monitor = UsageMonitor(
        db,
        settings=SimpleNamespace(codex_billing_api_key="sk-billing-123", gemini_billing_api_key=None),
    )

    import asyncio

    asyncio.run(monitor.sync_all_accounts())

    assert captured_headers == ["Bearer sk-billing-123", "Bearer sk-billing-123"]

def test_usage_monitor_uses_api_key_credential_when_no_codex_billing_key(tmp_path, monkeypatch):
    db_path = tmp_path / "codara.db"
    db = DatabaseManager(str(db_path))
    account = Account(
        account_id="codex-api",
        provider=ProviderType.CODEX,
        auth_type=AuthType.API_KEY,
        label="Codex API",
    )
    db.save_account(account)
    pool = AccountPool(db)
    pool.register_account(account, "sk-account-999")

    captured_headers = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"data": [{"results": [{"input_tokens": 2, "request_count": 1}]}]}

    class FakeClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None, params=None):
            captured_headers.append(headers["Authorization"])
            return FakeResponse()

    monkeypatch.setattr(codex_adapter_module.httpx, "AsyncClient", FakeClient)

    monitor = UsageMonitor(
        db,
        settings=SimpleNamespace(codex_billing_api_key=None, gemini_billing_api_key=None),
    )

    import asyncio

    asyncio.run(monitor.sync_all_accounts())

    assert captured_headers == ["Bearer sk-account-999", "Bearer sk-account-999"]


def test_usage_monitor_refreshes_codex_oauth_on_auth_failure(tmp_path, monkeypatch):
    db_path = tmp_path / "codara.db"
    db = DatabaseManager(str(db_path))
    account = Account(
        account_id="codex-oauth",
        provider=ProviderType.CODEX,
        auth_type=AuthType.OAUTH_SESSION,
        label="Codex OAuth",
    )
    db.save_account(account)
    pool = AccountPool(db)
    pool.register_account(
        account,
        """
        {
          "auth_mode": "chatgpt",
          "tokens": {
            "access_token": "old-access",
            "refresh_token": "refresh-token"
          }
        }
        """,
    )

    class FakeResponse:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self._payload = payload or {}

        def json(self):
            return self._payload

    class FakeClient:
        get_count = 0
        post_count = 0
        captured_headers = []

        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None, params=None):
            FakeClient.get_count += 1
            FakeClient.captured_headers.append(headers.get("Authorization", ""))
            if "backend-api/wham/usage" in url:
                if FakeClient.get_count == 1:
                    return FakeResponse(401, {})
                return FakeResponse(
                    200,
                    {
                        "rate_limit": {
                            "primary_window": {"used_percent": 20, "reset_after_seconds": 3600, "reset_at": 1776114377},
                            "secondary_window": {"used_percent": 40, "reset_after_seconds": 70000, "reset_at": 1776366877},
                        },
                        "credits": {"balance": "1113.7940425000"},
                    },
                )
            return FakeResponse(404, {})

        async def post(self, url, json=None):
            FakeClient.post_count += 1
            if "oauth/token" in url:
                return FakeResponse(200, {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600})
            return FakeResponse(400, {})

    monkeypatch.setattr(monitor_module.httpx, "AsyncClient", FakeClient)

    monitor = UsageMonitor(
        db,
        settings=SimpleNamespace(codex_billing_api_key=None, gemini_billing_api_key=None),
    )

    import asyncio

    asyncio.run(monitor.sync_all_accounts())

    updated_credential = pool.get_credential("codex-oauth")
    assert updated_credential is not None
    payload = json.loads(updated_credential)
    assert payload["tokens"]["access_token"] == "new-access"
    assert payload["tokens"]["refresh_token"] == "new-refresh"
    assert FakeClient.post_count == 1


def test_usage_monitor_proactively_refreshes_near_expiry_token(tmp_path, monkeypatch):
    db_path = tmp_path / "codara.db"
    db = DatabaseManager(str(db_path))
    account = Account(
        account_id="codex-oauth-proactive",
        provider=ProviderType.CODEX,
        auth_type=AuthType.OAUTH_SESSION,
        label="Codex OAuth Proactive",
    )
    db.save_account(account)
    pool = AccountPool(db)
    pool.register_account(
        account,
        json.dumps(
            {
                "tokens": {"access_token": "old-access", "refresh_token": "refresh-token"},
                "expires_in": 60,
            }
        ),
    )

    monitor = UsageMonitor(
        db,
        settings=SimpleNamespace(codex_billing_api_key=None, gemini_billing_api_key=None),
    )

    called = {"refresh": 0}

    async def fake_refresh(account_id: str):
        called["refresh"] += 1
        return True

    async def fake_collect_usage(acc, credential, settings):
        return {"status": "ready", "usage_hourly": 10, "usage_weekly": 100}

    monkeypatch.setattr(monitor, "_refresh_codex_oauth_session", fake_refresh)
    monkeypatch.setattr(monitor.adapters[ProviderType.CODEX], "collect_usage", fake_collect_usage)

    import asyncio

    asyncio.run(monitor.sync_all_accounts())
    assert called["refresh"] == 1


def test_wham_percentage_usage_does_not_overwrite_request_counters(tmp_path):
    db_path = tmp_path / "codara.db"
    db = DatabaseManager(str(db_path))
    account = Account(
        account_id="codex-wham-counters",
        provider=ProviderType.CODEX,
        auth_type=AuthType.OAUTH_SESSION,
        label="Codex WHAM Counters",
    )
    AccountPool(db).register_account(account, '{"tokens":{"access_token":"oauth-token"}}')
    monitor = UsageMonitor(db, settings=SimpleNamespace(codex_billing_api_key=None, gemini_billing_api_key=None))

    monitor._apply_usage_result(
        account,
        {
            "status": "ready",
            "usage_source": "wham",
            "hourly_used_pct": 10.0,
            "weekly_used_pct": 20.0,
        },
    )

    stored = db.get_account("codex-wham-counters")
    assert stored is not None
    assert stored.usage_hourly == int(round(stored.hourly_limit * 0.10))
    assert stored.usage_weekly == int(round(stored.weekly_limit * 0.20))
    assert stored.usage_tpm == 0
    assert stored.usage_rpd == 0


def test_usage_summary_uses_provider_reset_timestamps(tmp_path):
    db_path = tmp_path / "codara.db"
    db = DatabaseManager(str(db_path))
    now = datetime.now(timezone.utc)
    account = Account(
        account_id="codex-reset-metadata",
        provider=ProviderType.CODEX,
        auth_type=AuthType.OAUTH_SESSION,
        label="Codex Reset Metadata",
        hourly_limit=1000,
        weekly_limit=10000,
        usage_hourly=470,
        usage_weekly=10000,
        hourly_used_pct=47.0,
        weekly_used_pct=100.0,
        hourly_reset_after_seconds=13503,
        weekly_reset_after_seconds=266003,
        hourly_reset_at=now + timedelta(seconds=13503),
        weekly_reset_at=now + timedelta(seconds=266003),
        remaining_compute_hours=1113.7940425,
    )
    AccountPool(db).register_account(account, '{"tokens":{"access_token":"oauth-token"}}')

    summary = db.get_usage_summary()
    row = next(item for item in summary["providers"] if item["account_id"] == "codex-reset-metadata")

    assert row["hourly_used_pct"] == 47.0
    assert row["weekly_used_pct"] == 100.0
    assert row["hourly_reset_after_seconds"] == 13503
    assert row["weekly_reset_after_seconds"] == 266003
    assert row["hourly_reset_at"] == account.hourly_reset_at.replace(microsecond=0).isoformat()
    assert row["weekly_reset_at"] == account.weekly_reset_at.replace(microsecond=0).isoformat()


def test_usage_monitor_logs_fetch_success(tmp_path):
    db_path = tmp_path / "codara.db"
    db = DatabaseManager(str(db_path))
    account = Account(
        account_id="codex-success",
        provider=ProviderType.CODEX,
        auth_type=AuthType.OAUTH_SESSION,
        label="Codex Success",
    )
    AccountPool(db).register_account(
        account,
        json.dumps({"tokens": {"access_token": "oauth-access-token", "refresh_token": "refresh-token"}}),
    )

    monitor = UsageMonitor(
        db,
        settings=SimpleNamespace(codex_billing_api_key=None, gemini_billing_api_key=None),
    )

    async def fake_collect_usage(account, credential, settings):
        return {
            "status": "ready",
            "usage_source": "wham",
            "hourly_used_pct": 12.0,
            "weekly_used_pct": 18.0,
        }

    monitor.adapters[ProviderType.CODEX].collect_usage = fake_collect_usage

    import asyncio

    asyncio.run(monitor.sync_all_accounts())

    actions = [log["action"] for log in db.get_audit_logs()]
    assert "usage.fetch.started" in actions
    assert "usage.fetch.succeeded" in actions


def test_usage_monitor_logs_failed_refresh_without_refresh_token(tmp_path):
    db_path = tmp_path / "codara.db"
    db = DatabaseManager(str(db_path))
    account = Account(
        account_id="codex-expired",
        provider=ProviderType.CODEX,
        auth_type=AuthType.OAUTH_SESSION,
        label="Codex Expired",
    )
    AccountPool(db).register_account(account, json.dumps({"tokens": {"access_token": "oauth-access-token"}}))
    monitor = UsageMonitor(db)

    import asyncio

    refreshed = asyncio.run(monitor._refresh_codex_oauth_session("codex-expired"))

    assert refreshed is False
    stored = db.get_account("codex-expired")
    assert stored is not None
    assert stored.status == "expired"
    failure_log = next(log for log in db.get_audit_logs() if log["action"] == "account.auth.refresh.failed")
    assert "missing refresh_token" in (failure_log["after_state"] or "")
