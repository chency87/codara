from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import amesh.gateway.app as gateway_app
from amesh.core.models import ProviderType, Session, SessionStatus, Task, TurnResult, User, UserStatus, Workspace
from amesh.database.manager import DatabaseManager
from tests.helpers import operator_headers


def test_management_session_tasks_lists_prompt_and_result(tmp_path, monkeypatch):
    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()

    gateway_app.db_manager = DatabaseManager(str(tmp_path / "amesh.db"))

    now = datetime.now(timezone.utc)
    user = User(
        user_id="user_test",
        email="user_test@example.com",
        display_name="User Test",
        status=UserStatus.ACTIVE,
        workspace_path=str(tmp_path / "workspaces" / "user_test"),
        created_at=now,
        created_by="unit-test",
        updated_at=now,
    )
    gateway_app.db_manager.save_user(user)

    workspace = Workspace(
        workspace_id="ws_test",
        name="default",
        path=str(tmp_path / "workspaces" / "user_test" / "default"),
        user_id=user.user_id,
        template="default",
        default_provider=ProviderType.CODEX,
        created_at=now,
        updated_at=now,
    )
    gateway_app.db_manager.save_workspace(workspace)

    session = Session(
        session_id="ses_test",
        workspace_id=workspace.workspace_id,
        client_session_id="thread_test",
        backend_id="backend_1",
        provider=ProviderType.CODEX,
        user_id=user.user_id,
        api_key_id=None,
        cwd_path=str(tmp_path),
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=24),
    )
    gateway_app.db_manager.save_session(session)

    task = Task(
        task_id="task_test",
        session_id=session.session_id,
        workspace_id=workspace.workspace_id,
        user_id=user.user_id,
        prompt="please list files",
        status="completed",
        result=TurnResult(output="here are the files", backend_id="backend_1", finish_reason="stop"),
        created_at=now,
        updated_at=now,
    )
    gateway_app.db_manager.save_task(task)

    client = TestClient(gateway_app.app)
    headers = operator_headers(client, secret="unit-test-secret")

    resp = client.get(f"/management/v1/sessions/{session.session_id}/tasks", headers=headers)
    assert resp.status_code == 200
    payload = resp.json()["data"]
    assert len(payload) == 1
    assert payload[0]["task_id"] == "task_test"
    assert payload[0]["prompt"] == "please list files"
    assert payload[0]["result"]["output"] == "here are the files"
