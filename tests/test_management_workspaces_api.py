from amesh.core.models import Session, ProviderType, SessionStatus
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

import amesh.gateway.app as gateway_app
from amesh.database.manager import DatabaseManager
from amesh.workspace.engine import WorkspaceEngine
from tests.helpers import operator_headers


def _setup_workspace_api(tmp_path, monkeypatch):
    db_path = tmp_path / "amesh.db"
    workspaces_root = tmp_path / "workspaces"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.settings.workspaces_root = str(workspaces_root)
    gateway_app.db_manager = DatabaseManager(str(db_path))


    client = TestClient(gateway_app.app)
    headers = operator_headers(client)
    create_resp = client.post(
        "/management/v1/users",
        headers=headers,
        json={
            "email": "workspace-owner@example.com",
            "display_name": "Workspace Owner",
            "key_label": "primary",
            "max_concurrency": 2,
        },
    )
    created = create_resp.json()["data"]
    base_workspace = Path(created["workspace_path"])
    user_id = created["user_id"]

    # Retrieve the automatically created workspace for the user
    workspaces = gateway_app.db_manager.list_workspaces_v2(user_id=user_id)
    if not workspaces:
        # If not found (e.g. if the API didn't create a DB record for it yet in v2 sense, 
        # though create_user should have), we can create one manually for the test.
        from amesh.core.models import Workspace
        workspace_id = "wsk-base"
        gateway_app.db_manager.save_workspace(Workspace(
            workspace_id=workspace_id,
            name="default",
            path=str(base_workspace),
            user_id=user_id,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        ))
    else:
        workspace_id = workspaces[0].workspace_id

    subworkspace = base_workspace / "project-a"
    subworkspace.mkdir(parents=True, exist_ok=True)
    WorkspaceEngine(str(subworkspace)).ensure_git_repository()
    (subworkspace / "notes.txt").write_text("hello workspace", encoding="utf-8")

    # For the subworkspace, we also need a Workspace record in DB
    sub_workspace_id = "wsk-sub"
    from amesh.core.models import Workspace
    gateway_app.db_manager.save_workspace(Workspace(
        workspace_id=sub_workspace_id,
        name="project-a",
        path=str(subworkspace),
        user_id=user_id,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc)
    ))

    now = datetime.now(timezone.utc)
    gateway_app.db_manager.save_session(
        Session(
            session_id=f"{user_id}::default::base-thread",
            workspace_id=workspace_id,
            client_session_id=f"{user_id}::default::base-thread",
            backend_id="backend-base",
            provider=ProviderType.CODEX,
            user_id=user_id,
            cwd_path=str(base_workspace),
            status=SessionStatus.IDLE,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(hours=1),
        )
    )
    gateway_app.db_manager.save_session(
        Session(
            session_id=f"{user_id}::project-a::sub-thread",
            workspace_id=sub_workspace_id,
            client_session_id=f"{user_id}::project-a::sub-thread",
            backend_id="backend-sub",
            provider=ProviderType.CODEX,
            user_id=user_id,
            cwd_path=str(subworkspace),
            status=SessionStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(hours=1),
        )
    )

    return client, headers, user_id, base_workspace, subworkspace


def test_management_workspaces_lists_git_metadata_and_bindings(tmp_path, monkeypatch):
    client, headers, user_id, base_workspace, subworkspace = _setup_workspace_api(tmp_path, monkeypatch)

    resp = client.get("/management/v1/workspaces", headers=headers)

    assert resp.status_code == 200
    rows = resp.json()["data"]
    by_path = {row["path"]: row for row in rows}
    if str(base_workspace) not in by_path:
        print(f"DEBUG BY_PATH: {list(by_path.keys())}")
        print(f"DEBUG BASE: {str(base_workspace)}")
    assert str(base_workspace) in by_path
    assert str(subworkspace) in by_path

    base_row = by_path[str(base_workspace)]
    sub_row = by_path[str(subworkspace)]

    assert base_row["scope"] == "user"
    assert base_row["bound_users_count"] == 1
    assert base_row["bound_sessions_count"] == 1
    assert base_row["git"]["is_git_repo"] is True

    assert sub_row["scope"] == "user"
    assert sub_row["bound_users_count"] == 1
    assert sub_row["bound_sessions_count"] == 1

    detail_resp = client.get(f"/management/v1/workspaces/{sub_row['workspace_id']}", headers=headers)
    assert detail_resp.status_code == 200
    detail = detail_resp.json()["data"]
    assert detail["path"] == str(subworkspace)
    assert detail["users"][0]["user_id"] == user_id
    assert detail["users"][0]["owner"] is True
    assert len(detail["sessions"]) == 1
    assert detail["sessions"][0]["cwd_path"] == str(subworkspace)


def test_management_workspace_reset_wipes_sessions_only(tmp_path, monkeypatch):
    client, headers, _user_id, _base_workspace, subworkspace = _setup_workspace_api(tmp_path, monkeypatch)

    listing = client.get("/management/v1/workspaces", headers=headers).json()["data"]
    target = next(row for row in listing if row["path"] == str(subworkspace))

    resp = client.post(f"/management/v1/workspaces/{target['workspace_id']}/reset", headers=headers)

    assert resp.status_code == 200
    payload = resp.json()["data"]
    assert payload["sessions_wiped"] == 1
    assert payload["files_preserved"] is True
    assert subworkspace.exists()
    assert (subworkspace / "notes.txt").read_text(encoding="utf-8") == "hello workspace"
    assert gateway_app.db_manager.get_workspace_sessions(str(subworkspace)) == []


def test_management_workspace_delete_removes_directory_and_sessions(tmp_path, monkeypatch):
    client, headers, _user_id, _base_workspace, subworkspace = _setup_workspace_api(tmp_path, monkeypatch)

    listing = client.get("/management/v1/workspaces", headers=headers).json()["data"]
    target = next(row for row in listing if row["path"] == str(subworkspace))

    resp = client.delete(f"/management/v1/workspaces/{target['workspace_id']}", headers=headers)

    assert resp.status_code == 200
    payload = resp.json()["data"]
    assert payload["sessions_wiped"] == 1
    assert payload["workspace_deleted"] is True
    assert not subworkspace.exists()
    assert gateway_app.db_manager.get_workspace_sessions(str(subworkspace)) == []
