from codara.core.models import Session, ProviderType, SessionStatus
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import codara.gateway.app as gateway_app
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

    workspaces = gateway_app.db_manager.list_workspaces_v2(user_id=user_id)
    workspace_id = workspaces[0].workspace_id

    now = datetime.now(timezone.utc)
    client_session_id = f"{user_id}::root::active-thread"
    gateway_app.db_manager.save_session(
        Session(
            session_id=client_session_id,
            workspace_id=workspace_id,
            client_session_id=client_session_id,
            backend_id="backend-1",
            provider=ProviderType.CODEX,
            user_id=user_id,
            cwd_path=created["workspace_path"],
            status=SessionStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(hours=1),
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
