from datetime import datetime, timedelta

from fastapi.testclient import TestClient

import codara.gateway.app as gateway_app
from codara.accounts.pool import AccountPool
from codara.core.models import Account, AccountStatus, AuthType, ProviderType, Session, SessionStatus
from codara.database.manager import DatabaseManager
from tests.helpers import operator_headers


def test_management_overview_summarizes_visible_runtime_state(tmp_path, monkeypatch):
    db_path = tmp_path / "codara.db"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    monkeypatch.setattr(gateway_app.settings, "release_check_enabled", False)
    gateway_app.clear_auth_caches()
    gateway_app.db_manager = DatabaseManager(str(db_path))

    pool = AccountPool(gateway_app.db_manager)
    pool.register_account(
        Account(
            account_id="codex-visible",
            provider=ProviderType.CODEX,
            auth_type=AuthType.API_KEY,
            label="Codex Visible",
            status=AccountStatus.COOLDOWN.value,
            cooldown_until=datetime.now() + timedelta(minutes=10),
        ),
        "sk-visible",
    )
    pool.register_account(
        Account(
            account_id="codex-system",
            provider=ProviderType.CODEX,
            auth_type=AuthType.OAUTH_SESSION,
            label="Codex System",
            inventory_source="system",
        ),
        '{"tokens":{"access_token":"system"}}',
    )

    gateway_app.db_manager.save_session(
        Session(
            client_session_id="sess-active",
            backend_id="backend-1",
            provider=ProviderType.CODEX,
            account_id="codex-visible",
            cwd_path=str(tmp_path),
            prefix_hash="abc",
            status=SessionStatus.ACTIVE,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            expires_at=datetime.now() + timedelta(hours=1),
        )
    )
    gateway_app.db_manager.save_session(
        Session(
            client_session_id="sess-dirty",
            backend_id="backend-2",
            provider=ProviderType.CODEX,
            account_id="codex-visible",
            cwd_path=str(tmp_path),
            prefix_hash="def",
            status=SessionStatus.DIRTY,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            expires_at=datetime.now() + timedelta(hours=1),
        )
    )

    client = TestClient(gateway_app.app)
    headers = operator_headers(client)

    resp = client.get("/management/v1/overview", headers=headers)

    assert resp.status_code == 200
    payload = resp.json()["data"]
    assert payload["health"]["status"] == "degraded"
    assert payload["summary"]["sessions_total"] == 2
    assert payload["summary"]["active_sessions"] == 1
    assert payload["summary"]["dirty_sessions"] == 1
    assert payload["summary"]["accounts_total"] == 1
    assert payload["summary"]["cooldown_accounts"] == 1
    assert payload["summary"]["accounts_available"] == 0
    assert payload["summary"]["expired_accounts"] == 0
    assert payload["health"]["components"]["gateway"]["latency_ms"] is not None
    assert payload["health"]["components"]["orchestrator"]["latency_ms"] is not None
    assert payload["health"]["components"]["state_store"]["latency_ms"] is not None
    assert payload["version"]["name"] == "codara"
    assert payload["version"]["version"]
    assert payload["version"]["release_check"]["enabled"] is False
    assert payload["providers"][0]["provider"] == "codex"
    assert payload["providers"][0]["accounts_total"] == 1


def test_management_version_endpoint_can_check_updates(tmp_path, monkeypatch):
    db_path = tmp_path / "codara.db"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    monkeypatch.setattr(gateway_app.settings, "secret_key", "unit-test-secret")
    monkeypatch.setattr(gateway_app.settings, "release_check_enabled", True)
    monkeypatch.setattr(gateway_app.settings, "release_repository", "codara/codara")
    monkeypatch.setattr(gateway_app.settings, "release_api_base_url", "https://api.github.test")
    monkeypatch.setattr(gateway_app.settings, "release_check_timeout_seconds", 1)
    gateway_app.clear_auth_caches()
    gateway_app.db_manager = DatabaseManager(str(db_path))

    class Result:
        def to_dict(self):
            return {
                "current_version": "1.0.0",
                "latest_version": "1.1.0",
                "update_available": True,
                "status": "ok",
                "repository": "codara/codara",
                "release_url": "https://github.test/release",
                "checked_url": "https://api.github.test/repos/codara/codara/releases/latest",
                "error": None,
            }

    monkeypatch.setattr(gateway_app, "get_version", lambda: "1.0.0")
    monkeypatch.setattr(gateway_app, "check_for_update", lambda **kwargs: Result())

    client = TestClient(gateway_app.app)
    headers = operator_headers(client)

    resp = client.get("/management/v1/version", headers=headers, params={"check_updates": "true"})

    assert resp.status_code == 200
    payload = resp.json()["data"]
    assert payload["version"] == "1.0.0"
    assert payload["release_check"]["latest_version"] == "1.1.0"
    assert payload["release_check"]["update_available"] is True
