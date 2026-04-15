import json

from fastapi.testclient import TestClient

import codara.gateway.app as gateway_app
from codara.accounts.pool import AccountPool
from codara.core.models import Account, AuthType, ProviderType
from codara.database.manager import DatabaseManager
from tests.helpers import operator_headers


def test_usage_refresh_endpoint_triggers_sync(tmp_path, monkeypatch):
    db_path = tmp_path / "codara.db"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.db_manager = DatabaseManager(str(db_path))

    called = {"value": False}

    class DummyUsageMonitor:
        async def sync_all_accounts(self, max_concurrency=None):
            called["value"] = True
            called["max_concurrency"] = max_concurrency

    gateway_app.usage_monitor = DummyUsageMonitor()

    client = TestClient(gateway_app.app)
    headers = operator_headers(client)

    resp = client.post("/management/v1/usage/refresh", headers=headers)
    assert resp.status_code == 200
    assert called["value"] is True
    assert called["max_concurrency"] == 12
    logs = gateway_app.db_manager.get_audit_logs()
    actions = [log["action"] for log in logs]
    assert "usage.refresh.started" in actions
    assert "usage.refresh.completed" in actions


def test_account_upload_endpoint_accepts_credential_text(tmp_path, monkeypatch):
    db_path = tmp_path / "codara.db"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.db_manager = DatabaseManager(str(db_path))

    client = TestClient(gateway_app.app)
    headers = operator_headers(client)

    resp = client.post(
        "/management/v1/accounts/upload",
        headers=headers,
        data={
            "account_id": "codex-upload-1",
            "provider": "codex",
            "auth_type": "OAUTH_SESSION",
            "label": "Codex Upload",
            "credential_text": '{"auth_mode":"chatgpt","tokens":{"access_token":"atk","refresh_token":"rtk"}}',
        },
    )
    assert resp.status_code == 200
    stored = gateway_app.db_manager.get_account("codex-upload-1")
    assert stored is not None
    assert stored.auth_type == AuthType.OAUTH_SESSION
    pool = AccountPool(gateway_app.db_manager)
    decrypted = pool.get_credential("codex-upload-1")
    assert decrypted is not None
    payload = json.loads(decrypted)
    assert payload["tokens"]["access_token"] == "atk"


def test_account_upload_endpoint_rejects_gemini_credentials(tmp_path, monkeypatch):
    db_path = tmp_path / "codara.db"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.db_manager = DatabaseManager(str(db_path))

    client = TestClient(gateway_app.app)
    headers = operator_headers(client)

    resp = client.post(
        "/management/v1/accounts/upload",
        headers=headers,
        data={
            "account_id": "gemini-upload-1",
            "provider": "gemini",
            "auth_type": "OAUTH_SESSION",
            "label": "Gemini Upload",
            "credential_text": '{"tokens":{"access_token":"atk"}}',
        },
    )

    assert resp.status_code == 400
    assert "Codex" in resp.json()["detail"]
    assert gateway_app.db_manager.get_account("gemini-upload-1") is None


def test_accounts_endpoint_leaves_unsynced_limits_unknown(tmp_path, monkeypatch):
    db_path = tmp_path / "codara.db"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.db_manager = DatabaseManager(str(db_path))
    AccountPool(gateway_app.db_manager).register_account(
        Account(
            account_id="codex-unsynced",
            provider=ProviderType.CODEX,
            auth_type=AuthType.API_KEY,
            label="Unsynced",
        ),
        "sk-unsynced",
    )

    client = TestClient(gateway_app.app)
    headers = operator_headers(client)

    resp = client.get("/management/v1/accounts", headers=headers)
    assert resp.status_code == 200
    row = next(item for item in resp.json()["data"] if item["account_id"] == "codex-unsynced")

    assert row["usage_observed"] is False
    assert row["hourly_limit"] is None
    assert row["weekly_limit"] is None
    assert row["hourly_reset_at"] is None
    assert row["weekly_reset_at"] is None


def test_accounts_endpoint_hides_legacy_system_import_rows(tmp_path, monkeypatch):
    db_path = tmp_path / "codara.db"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.db_manager = DatabaseManager(str(db_path))

    system_account = Account(
        account_id="codex-oauth",
        provider=ProviderType.CODEX,
        auth_type=AuthType.OAUTH_SESSION,
        label="Codex OAuth Account",
        inventory_source="system",
    )
    explicit_account = Account(
        account_id="codex-vault",
        provider=ProviderType.CODEX,
        auth_type=AuthType.OAUTH_SESSION,
        label="Codex Vault",
    )
    pool = AccountPool(gateway_app.db_manager)
    pool.register_account(system_account, '{"tokens":{"access_token":"system-only"}}')
    pool.register_account(explicit_account, '{"tokens":{"access_token":"vault","refresh_token":"rtk","id_token":"id"}}')

    client = TestClient(gateway_app.app)
    headers = operator_headers(client)

    resp = client.get("/management/v1/accounts", headers=headers)
    assert resp.status_code == 200
    account_ids = [item["account_id"] for item in resp.json()["data"]]
    assert "codex-vault" in account_ids
    assert "codex-oauth" not in account_ids


def test_audit_endpoint_supports_search(tmp_path, monkeypatch):
    db_path = tmp_path / "codara.db"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.db_manager = DatabaseManager(str(db_path))
    gateway_app.db_manager.record_audit(
        actor="system:usage-monitor",
        action="usage.fetch.failed",
        target_type="account",
        target_id="codex-a",
        after={"error": "missing refresh_token"},
    )
    gateway_app.db_manager.record_audit(
        actor="operator:alice",
        action="user.updated",
        target_type="user",
        target_id="uag_usr_1",
        after={"display_name": "Alice"},
    )

    client = TestClient(gateway_app.app)
    headers = operator_headers(client)

    resp = client.get("/management/v1/audit", headers=headers, params={"search": "missing refresh_token"})
    assert resp.status_code == 200
    logs = resp.json()["data"]
    assert len(logs) == 1
    assert logs[0]["action"] == "usage.fetch.failed"
