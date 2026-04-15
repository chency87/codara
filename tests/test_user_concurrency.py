from datetime import datetime, timedelta

from fastapi.testclient import TestClient

import codara.gateway.app as gateway_app
from codara.accounts.pool import AccountPool
from codara.core.models import Account, AuthType, ProviderType, Session, SessionStatus
from codara.database.manager import DatabaseManager
from codara.orchestrator.engine import Orchestrator
from tests.helpers import operator_headers


def test_chat_completions_reject_when_user_hits_concurrency_limit(tmp_path, monkeypatch):
    db_path = tmp_path / "codara.db"
    workspaces_root = tmp_path / "workspaces"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.settings.workspaces_root = str(workspaces_root)
    gateway_app.db_manager = DatabaseManager(str(db_path))
    gateway_app.orchestrator = Orchestrator(gateway_app.db_manager)

    AccountPool(gateway_app.db_manager).register_account(
        Account(
            account_id="codex-ready",
            provider=ProviderType.CODEX,
            auth_type=AuthType.API_KEY,
            label="Codex Ready",
        ),
        "sk-ready",
    )

    client = TestClient(gateway_app.app)
    headers = operator_headers(client)

    create_resp = client.post(
        "/management/v1/users",
        headers=headers,
        json={
            "email": "concurrency@example.com",
            "display_name": "Concurrency User",
            "key_label": "primary",
            "max_concurrency": 1,
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()["data"]
    raw_key = created["api_key"]["raw_key"]
    user_id = created["user_id"]

    gateway_app.db_manager.save_session(
        Session(
            client_session_id=f"{user_id}::root::active-thread",
            backend_id="backend-1",
            provider=ProviderType.CODEX,
            account_id="codex-ready",
            user_id=user_id,
            api_key_id=created["api_key"]["key_id"],
            cwd_path=created["workspace_path"],
            prefix_hash="abc",
            status=SessionStatus.ACTIVE,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            expires_at=datetime.now() + timedelta(hours=1),
        )
    )

    resp = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={
            "model": "uag-codex-v5",
            "messages": [{"role": "user", "content": "ping"}],
            "uag_options": {"provider": "codex", "client_session_id": "thread-2"},
        },
    )

    assert resp.status_code == 429
    assert "User concurrency limit reached" in resp.json()["detail"]
