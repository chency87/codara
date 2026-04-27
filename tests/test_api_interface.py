from fastapi.testclient import TestClient

import amesh.gateway.app as gateway_app
from amesh.core.models import TurnResult
from amesh.database.manager import DatabaseManager
from amesh.orchestrator.engine import Orchestrator


def test_openapi_groups_routes_by_module(tmp_path, monkeypatch):
    db_path = tmp_path / "amesh.db"
    workspaces_root = tmp_path / "workspaces"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.settings.workspaces_root = str(workspaces_root)
    gateway_app.db_manager = DatabaseManager(str(db_path))
    gateway_app.orchestrator = Orchestrator(gateway_app.db_manager)
    gateway_app.app.openapi_schema = None

    client = TestClient(gateway_app.app)
    schema = client.get("/openapi.json").json()

    tag_names = {tag["name"] for tag in schema["tags"]}
    assert "Inference" in tag_names
    assert "User Self-Service" in tag_names
    
    assert "Management Sessions" in tag_names

    assert schema["paths"]["/v1/chat/completions"]["post"]["tags"] == ["Inference"]
    assert schema["paths"]["/v1/chat/completions"]["post"]["security"] == [{"User API Key": []}]
    assert schema["paths"]["/v1/user/me"]["get"]["tags"] == ["User Self-Service"]
    assert schema["paths"]["/v1/user/me"]["get"]["security"] == [{"User API Key": []}]
    assert schema["paths"]["/v1/user/keys"]["get"]["security"] == [{"User API Key": []}]
    assert schema["paths"]["/v1/user/sessions"]["get"]["security"] == [{"User API Key": []}]
def test_chat_completions_requires_authorization(tmp_path, monkeypatch):
    db_path = tmp_path / "amesh.db"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.db_manager = DatabaseManager(str(db_path))
    gateway_app.orchestrator = Orchestrator(gateway_app.db_manager)

    client = TestClient(gateway_app.app)
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-5-codex",
            "messages": [{"role": "user", "content": "ping"}],
            "provider": "codex",
        },
    )

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Authentication token missing"


def test_chat_completions_allows_direct_workspace_inside_safe_zone(tmp_path, monkeypatch):
    db_path = tmp_path / "amesh.db"
    workspaces_root = tmp_path / "workspaces"
    workspace = workspaces_root / "project-a"
    workspace.mkdir(parents=True)

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.settings.workspaces_root = str(workspaces_root)
    gateway_app.db_manager = DatabaseManager(str(db_path))
    gateway_app.orchestrator = Orchestrator(gateway_app.db_manager)

    observed = {}

    async def fake_handle_request(options, messages, provider_model=None):
        observed["workspace_root"] = options.workspace_root
        return TurnResult(output="ok", backend_id="sess-1", finish_reason="stop")

    monkeypatch.setattr(gateway_app.orchestrator, "handle_request", fake_handle_request)

    client = TestClient(gateway_app.app)
    resp = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer unit-test-secret"},
        json={
            "model": "gemini-2.5-pro",
            "messages": [{"role": "user", "content": "ping"}],
            "provider": "gemini",
            "workspace_root": str(workspace),
            "client_session_id": "thread-1",
        },
    )

    assert resp.status_code == 200
    assert observed["workspace_root"] == str(workspace.resolve())


def test_chat_completions_rejects_direct_workspace_outside_safe_zone(tmp_path, monkeypatch):
    db_path = tmp_path / "amesh.db"
    workspaces_root = tmp_path / "workspaces"
    outside = tmp_path / "outside"
    outside.mkdir()

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.settings.workspaces_root = str(workspaces_root)
    gateway_app.db_manager = DatabaseManager(str(db_path))
    gateway_app.orchestrator = Orchestrator(gateway_app.db_manager)

    client = TestClient(gateway_app.app)
    resp = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer unit-test-secret"},
        json={
            "model": "gemini-2.5-pro",
            "messages": [{"role": "user", "content": "ping"}],
            "provider": "gemini",
            "workspace_root": str(outside),
        },
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "workspace_access_denied"


def test_chat_completions_rejects_operator_workspace_traversal_outside_safe_zone(tmp_path, monkeypatch):
    db_path = tmp_path / "amesh.db"
    workspaces_root = tmp_path / "workspaces"
    allowed_parent = workspaces_root / "team-a"
    allowed_parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    traversal = allowed_parent / ".." / ".." / "outside"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.settings.workspaces_root = str(workspaces_root)
    gateway_app.db_manager = DatabaseManager(str(db_path))
    gateway_app.orchestrator = Orchestrator(gateway_app.db_manager)

    client = TestClient(gateway_app.app)
    resp = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer unit-test-secret"},
        json={
            "model": "gemini-2.5-pro",
            "messages": [{"role": "user", "content": "ping"}],
            "provider": "gemini",
            "workspace_root": str(traversal),
        },
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "workspace_access_denied"


def test_chat_completions_still_accepts_legacy_uag_options_wrapper(tmp_path, monkeypatch):
    db_path = tmp_path / "amesh.db"
    workspaces_root = tmp_path / "workspaces"
    workspace = workspaces_root / "project-a"
    workspace.mkdir(parents=True)

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.settings.workspaces_root = str(workspaces_root)
    gateway_app.db_manager = DatabaseManager(str(db_path))
    gateway_app.orchestrator = Orchestrator(gateway_app.db_manager)

    observed = {}

    async def fake_handle_request(options, messages, provider_model=None):
        observed["workspace_root"] = options.workspace_root
        observed["provider"] = options.provider.value
        observed["client_session_id"] = options.client_session_id
        return TurnResult(output="ok", backend_id="sess-1", finish_reason="stop")

    monkeypatch.setattr(gateway_app.orchestrator, "handle_request", fake_handle_request)

    client = TestClient(gateway_app.app)
    resp = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer unit-test-secret"},
        json={
            "model": "gemini-2.5-pro",
            "messages": [{"role": "user", "content": "ping"}],
            "uag_options": {
                "provider": "gemini",
                "workspace_root": str(workspace),
                "client_session_id": "thread-legacy",
            },
        },
    )

    assert resp.status_code == 200
    assert observed == {
        "workspace_root": str(workspace.resolve()),
        "provider": "gemini",
        "client_session_id": "thread-legacy",
    }
