from click.testing import CliRunner
from fastapi.testclient import TestClient

import codara.cli.main as cli_main
import codara.gateway.app as gateway_app
from codara.database.manager import DatabaseManager
from codara.workspace.manager import WorkspaceManager
from codara.workspace.project import ProjectService, normalize_project_name
from tests.helpers import operator_headers


def test_project_service_creates_default_project_layout(tmp_path):
    manager = WorkspaceManager(
        DatabaseManager(str(tmp_path / "codara.db")),
        workspaces_root=str(tmp_path / "workspaces"),
        isolated_envs_root=str(tmp_path / "workspaces" / "isolated_envs"),
    )
    service = ProjectService(manager)

    result = service.create_project("news-pulse", default_provider="codex")
    project_path = tmp_path / "workspaces" / "news-pulse"

    assert result.created is True
    assert project_path.is_dir()
    assert (project_path / "README.md").exists()
    assert (project_path / "docs").is_dir()
    assert (project_path / "src").is_dir()
    assert (project_path / "scripts").is_dir()
    assert (project_path / "tests").is_dir()
    assert (project_path / ".git").is_dir()
    metadata = (project_path / ".codara" / "project.toml").read_text(encoding="utf-8")
    assert 'name = "news-pulse"' in metadata
    assert 'default_provider = "codex"' in metadata

    projects = service.list_projects()
    assert len(projects) == 1
    assert projects[0]["project"]["name"] == "news-pulse"


def test_project_service_rejects_unsafe_names():
    for name in ["../escape", "/absolute", ".hidden", "bad/name"]:
        try:
            normalize_project_name(name)
        except ValueError:
            continue
        raise AssertionError(f"Expected {name!r} to be rejected")


def test_project_service_python_template(tmp_path):
    manager = WorkspaceManager(DatabaseManager(str(tmp_path / "codara.db")), workspaces_root=str(tmp_path / "workspaces"))
    service = ProjectService(manager)

    service.create_project("agent-lab", template="python")

    project_path = tmp_path / "workspaces" / "agent-lab"
    assert (project_path / "src" / "agent_lab" / "__init__.py").exists()
    assert (project_path / "tests" / "test_agent_lab.py").exists()


def test_project_cli_create_list_and_info(tmp_path, monkeypatch):
    runner = CliRunner()
    monkeypatch.setattr(cli_main.settings, "workspaces_root", str(tmp_path / "workspaces"))
    monkeypatch.setattr(cli_main.settings, "isolated_envs_root", str(tmp_path / "workspaces" / "isolated_envs"))
    monkeypatch.setattr(cli_main, "db_manager", DatabaseManager(str(tmp_path / "codara.db")))

    create = runner.invoke(cli_main.cli, ["project", "create", "news-pulse", "--template", "docs"])
    listing = runner.invoke(cli_main.cli, ["project", "list"])
    info = runner.invoke(cli_main.cli, ["project", "info", "news-pulse"])

    assert create.exit_code == 0
    assert "Created project: news-pulse" in create.output
    assert listing.exit_code == 0
    assert "news-pulse" in listing.output
    assert "docs" in listing.output
    assert info.exit_code == 0
    assert "Template: docs" in info.output


def test_management_projects_api_create_list_and_detail(tmp_path, monkeypatch):
    db_path = tmp_path / "codara.db"
    workspaces_root = tmp_path / "workspaces"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    monkeypatch.setattr(gateway_app.settings, "secret_key", "unit-test-secret")
    monkeypatch.setattr(gateway_app.settings, "workspaces_root", str(workspaces_root))
    monkeypatch.setattr(gateway_app.settings, "isolated_envs_root", str(workspaces_root / "isolated_envs"))
    gateway_app.clear_auth_caches()
    gateway_app.db_manager = DatabaseManager(str(db_path))

    client = TestClient(gateway_app.app)
    headers = operator_headers(client)

    created = client.post(
        "/management/v1/projects",
        headers=headers,
        json={"name": "news-pulse", "template": "default", "default_provider": "codex"},
    )

    assert created.status_code == 200
    payload = created.json()["data"]
    assert payload["project"]["name"] == "news-pulse"
    assert payload["project"]["default_provider"] == "codex"

    listing = client.get("/management/v1/projects", headers=headers)
    assert listing.status_code == 200
    rows = listing.json()["data"]
    assert [row["project"]["name"] for row in rows] == ["news-pulse"]

    detail = client.get(f"/management/v1/projects/{rows[0]['workspace_id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["data"]["path"] == str((workspaces_root / "news-pulse").resolve())

