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
    assert payload["providers"][0]["provider"] == "codex"
    assert payload["providers"][0]["accounts_total"] == 1
