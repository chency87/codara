from amesh.core.models import TurnResult
from fastapi.testclient import TestClient

import amesh.gateway.app as gateway_app
from amesh.database.manager import DatabaseManager
from amesh.orchestrator.engine import Orchestrator
from tests.helpers import operator_headers


class _FakeAdapter:
    async def send_turn(self, session, messages, provider_model):
        return TurnResult(
            output="ok",
            backend_id="backend-1",
            finish_reason="stop",
            modified_files=[],
            diff=None,
            actions=[],
            dirty=False,
        )

def _provision_user(client: TestClient, headers: dict, email: str, display_name: str):
    create_resp = client.post(
        "/management/v1/users",
        headers=headers,
        json={
            "email": email,
            "display_name": display_name,
            "key_label": "primary",
            "max_concurrency": 3,
        },
    )
    assert create_resp.status_code == 200
    return create_resp.json()["data"]


def test_user_key_request_persists_session_owner_and_key_binding(tmp_path, monkeypatch):
    db_path = tmp_path / "amesh.db"
    workspaces_root = tmp_path / "workspaces"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.settings.workspaces_root = str(workspaces_root)
    gateway_app.db_manager = DatabaseManager(str(db_path))
    gateway_app.orchestrator = Orchestrator(gateway_app.db_manager)

    monkeypatch.setattr(gateway_app.orchestrator, "_get_adapter", lambda provider: _FakeAdapter())

    client = TestClient(gateway_app.app)
    headers = operator_headers(client)
    created = _provision_user(client, headers, "binding@example.com", "Binding User")
    raw_key = created["api_key"]["raw_key"]
    key_id = created["api_key"]["key_id"]
    user_id = created["user_id"]

    chat_resp = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={
            "model": "uag-codex-v5",
            "messages": [{"role": "user", "content": "ping"}],
            "uag_options": {"provider": "codex", "client_session_id": "thread-1"},
        },
    )

    assert chat_resp.status_code == 200
    session_id = chat_resp.json()["extensions"]["client_session_id"]
    session = gateway_app.db_manager.get_session(session_id)
    assert session is not None
    assert session.user_id == user_id


def test_management_views_expose_bound_user_sessions_and_activity(tmp_path, monkeypatch):
    db_path = tmp_path / "amesh.db"
    workspaces_root = tmp_path / "workspaces"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.settings.workspaces_root = str(workspaces_root)
    gateway_app.db_manager = DatabaseManager(str(db_path))
    gateway_app.orchestrator = Orchestrator(gateway_app.db_manager)

    monkeypatch.setattr(gateway_app.orchestrator, "_get_adapter", lambda provider: _FakeAdapter())

    client = TestClient(gateway_app.app)
    headers = operator_headers(client)
    created = _provision_user(client, headers, "owner@example.com", "Owner User")
    raw_key = created["api_key"]["raw_key"]
    user_id = created["user_id"]

    chat_resp = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={
            "model": "uag-codex-v5",
            "messages": [{"role": "user", "content": "ping"}],
            "uag_options": {"provider": "codex", "workspace_id": "project-a", "client_session_id": "thread-1"},
        },
    )
    assert chat_resp.status_code == 200
    session_id = chat_resp.json()["extensions"]["client_session_id"]

    sessions_resp = client.get("/management/v1/sessions", headers=headers)
    assert sessions_resp.status_code == 200
    session_row = next(item for item in sessions_resp.json()["data"] if item["client_session_id"] == session_id)
    assert session_row["user_id"] == user_id
    assert session_row["user_display_name"] == "Owner User"
    assert session_row["api_key_label"] == "primary"

    detail_resp = client.get(f"/management/v1/users/{user_id}", headers=headers)
    assert detail_resp.status_code == 200
    detail = detail_resp.json()["data"]
    assert detail["sessions"][0]["client_session_id"] == session_id
    assert detail["sessions"][0]["api_key_label"] == "primary"
    assert detail["recent_activity"][0]["client_session_id"] == session_id
    assert detail["recent_activity"][0]["api_key_label"] == "primary"
    assert detail["recent_activity"][0]["duration_ms"] >= 0
