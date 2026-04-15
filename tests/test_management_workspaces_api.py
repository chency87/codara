from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

import codara.gateway.app as gateway_app
from codara.accounts.pool import AccountPool
from codara.core.models import Account, AuthType, ProviderType, Session, SessionStatus
from codara.database.manager import DatabaseManager
from codara.workspace.engine import WorkspaceEngine
from tests.helpers import operator_headers


def _setup_workspace_api(tmp_path, monkeypatch):
    db_path = tmp_path / "codara.db"
    workspaces_root = tmp_path / "workspaces"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.settings.workspaces_root = str(workspaces_root)
    gateway_app.db_manager = DatabaseManager(str(db_path))

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
            "email": "workspace-owner@example.com",
            "display_name": "Workspace Owner",
            "key_label": "primary",
            "max_concurrency": 2,
        },
    )
    created = create_resp.json()["data"]
    base_workspace = Path(created["workspace_path"])
    user_id = created["user_id"]

    subworkspace = base_workspace / "project-a"
    subworkspace.mkdir(parents=True, exist_ok=True)
    WorkspaceEngine(str(subworkspace)).ensure_git_repository()
    (subworkspace / "notes.txt").write_text("hello workspace", encoding="utf-8")

    now = datetime.now()
    gateway_app.db_manager.save_session(
        Session(
            client_session_id=f"{user_id}::default::base-thread",
            backend_id="backend-base",
            provider=ProviderType.CODEX,
            account_id="codex-ready",
            user_id=user_id,
            cwd_path=str(base_workspace),
            prefix_hash="base",
            status=SessionStatus.IDLE,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(hours=1),
        )
    )
    gateway_app.db_manager.save_session(
        Session(
            client_session_id=f"{user_id}::project-a::sub-thread",
            backend_id="backend-sub",
            provider=ProviderType.CODEX,
            account_id="codex-ready",
            user_id=user_id,
            cwd_path=str(subworkspace),
            prefix_hash="sub",
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
    assert str(base_workspace) in by_path
    assert str(subworkspace) in by_path

    base_row = by_path[str(base_workspace)]
    sub_row = by_path[str(subworkspace)]

    assert base_row["scope"] == "base"
    assert base_row["bound_users_count"] == 1
    assert base_row["bound_sessions_count"] == 2
    assert base_row["git"]["is_git_repo"] is True
    assert base_row["git"]["branch"] is not None

    assert sub_row["scope"] == "subworkspace"
    assert sub_row["bound_users_count"] == 1
    assert sub_row["bound_sessions_count"] == 1
    assert sub_row["git"]["head_commit"] is not None

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
