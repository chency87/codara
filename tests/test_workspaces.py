from click.testing import CliRunner
from fastapi.testclient import TestClient
from pathlib import Path
from datetime import datetime, timezone

import amesh.cli.main as cli_main
import amesh.gateway.app as gateway_app
from amesh.database.manager import DatabaseManager
from amesh.workspace.manager import WorkspaceManager
from amesh.workspace.service import (
    WorkspaceService,
    WORKSPACE_TEMPLATES,
    WORKSPACE_METADATA_DIR,
    WORKSPACE_METADATA_FILE,
    normalize_workspace_name,
)
from amesh.core.models import User, UserStatus
from tests.helpers import operator_headers


def _setup_user(db_manager: DatabaseManager, workspaces_root: Path, user_id: str = "user-1"):
    db_manager.save_user(User(
        user_id=user_id,
        email=f"{user_id}@example.com",
        display_name=f"Test User {user_id}",
        status=UserStatus.ACTIVE,
        workspace_path=str(workspaces_root / user_id),
        created_at=datetime.now(timezone.utc),
        created_by="test",
        updated_at=datetime.now(timezone.utc)
    ))

def test_workspace_service_creates_default_layout(tmp_path):
    db_path = tmp_path / "amesh.db"
    db_manager = DatabaseManager(str(db_path))
    workspaces_root = tmp_path / "workspaces"
    workspaces_root.mkdir()
    
    _setup_user(db_manager, workspaces_root)
    
    manager = WorkspaceManager(
        db_manager,
        workspaces_root=str(workspaces_root),
    )
    service = WorkspaceService(manager, db_manager)

    workspace = service.create_workspace("news-pulse", "user-1", default_provider="codex")
    workspace_path = Path(workspace.path)

    assert workspace_path.is_dir()
    assert (workspace_path / "README.md").exists()
    assert (workspace_path / "docs").is_dir()
    assert (workspace_path / "src").is_dir()
    assert (workspace_path / "tests").is_dir()
    assert (workspace_path / ".git").is_dir()
    
    metadata_path = workspace_path / WORKSPACE_METADATA_DIR / WORKSPACE_METADATA_FILE
    metadata = metadata_path.read_text(encoding="utf-8")
    assert 'name = "news-pulse"' in metadata
    assert 'default_provider = "codex"' in metadata

    workspaces = service.list_workspaces_v2()
    assert len(workspaces) == 1
    assert workspaces[0].name == "news-pulse"


def test_workspace_service_rejects_unsafe_names():
    for name in ["../escape", "/absolute", ".hidden", "bad/name"]:
        try:
            normalize_workspace_name(name)
        except ValueError:
            continue
        raise AssertionError(f"Expected {name!r} to be rejected")


def test_workspace_service_python_template(tmp_path):
    db_path = tmp_path / "amesh.db"
    db_manager = DatabaseManager(str(db_path))
    workspaces_root = tmp_path / "workspaces"
    workspaces_root.mkdir()
    
    _setup_user(db_manager, workspaces_root)
    
    manager = WorkspaceManager(db_manager, workspaces_root=str(workspaces_root))
    service = WorkspaceService(manager, db_manager)

    service.create_workspace("agent-lab", "user-1", template="python")

    workspace_path = tmp_path / "workspaces" / "user-1" / "agent-lab"
    assert (workspace_path / "src" / "main.py").exists()
    assert (workspace_path / "tests" / "test_main.py").exists()


def test_workspace_cli_create_list_and_info(tmp_path, monkeypatch):
    db_path = tmp_path / "amesh.db"
    db_manager = DatabaseManager(str(db_path))
    workspaces_root = tmp_path / "workspaces"
    workspaces_root.mkdir()
    
    _setup_user(db_manager, workspaces_root)
    
    runner = CliRunner()
    monkeypatch.setattr(cli_main.settings, "workspaces_root", str(workspaces_root))
    monkeypatch.setattr(cli_main, "db_manager", db_manager)

    create = runner.invoke(cli_main.cli, ["workspace", "create", "news-pulse", "--template", "docs", "--user-id", "user-1"])
    listing = runner.invoke(cli_main.cli, ["workspace", "list"])
    
    assert create.exit_code == 0
    assert "Successfully created workspace: news-pulse" in create.output
    
    workspace_id = ""
    for line in create.output.splitlines():
        if line.startswith("ID:"):
            workspace_id = line.split(":", 1)[1].strip()
            break
            
    info = runner.invoke(cli_main.cli, ["workspace", "info", workspace_id])

    assert listing.exit_code == 0
    assert "news-pulse" in listing.output
    assert "docs" in listing.output
    assert info.exit_code == 0
    assert "Template: docs" in info.output


def test_management_workspaces_v2_api_create_list_and_detail(tmp_path, monkeypatch):
    db_path = tmp_path / "amesh.db"
    workspaces_root = tmp_path / "workspaces"
    workspaces_root.mkdir()

    db_manager = DatabaseManager(str(db_path))
    # We need to setup an operator user too
    db_manager.save_user(User(
        user_id="operator:operator",
        email="op@example.com",
        display_name="Operator",
        status=UserStatus.ACTIVE,
        workspace_path=str(workspaces_root / "op-1"),
        created_at=datetime.now(timezone.utc),
        created_by="test",
        updated_at=datetime.now(timezone.utc)
    ))

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    monkeypatch.setattr(gateway_app.settings, "secret_key", "unit-test-secret")
    monkeypatch.setattr(gateway_app.settings, "workspaces_root", str(workspaces_root))
    gateway_app.clear_auth_caches()
    gateway_app.db_manager = db_manager

    client = TestClient(gateway_app.app)
    # The helper operator_headers might need to know the user_id or secret
    headers = operator_headers(client, secret="unit-test-secret")

    created = client.post(
        "/management/v1/workspaces/v2",
        headers=headers,
        json={"name": "news-pulse", "template": "default", "default_provider": "codex"},
    )

    if created.status_code != 200:
        print(f"DEBUG: {created.json()}")

    assert created.status_code == 200
    payload = created.json()["data"]
    assert payload["name"] == "news-pulse"
    assert payload["default_provider"] == "codex"

    listing = client.get("/management/v1/workspaces/v2", headers=headers)
    if listing.status_code != 200:
        print(f"DEBUG LISTING: {listing.json()}")
    assert listing.status_code == 200
    rows = listing.json()["data"]
    assert [row["name"] for row in rows] == ["news-pulse"]

    workspace_id = rows[0]['workspace_id']
    detail = client.get(f"/management/v1/workspaces/v2/{workspace_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["data"]["path"] == str((workspaces_root / "operator:operator" / "news-pulse").resolve())
