from fastapi.testclient import TestClient

import codara.gateway.app as gateway_app
from codara.database.manager import DatabaseManager
from codara.orchestrator.engine import Orchestrator


def test_openapi_groups_routes_by_module(tmp_path, monkeypatch):
    db_path = tmp_path / "codara.db"
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
    assert "Management Accounts" in tag_names
    assert "Management Sessions" in tag_names

    assert schema["paths"]["/v1/chat/completions"]["post"]["tags"] == ["Inference"]
    assert schema["paths"]["/v1/chat/completions"]["post"]["security"] == [{"User API Key": []}]
    assert schema["paths"]["/v1/user/me"]["get"]["tags"] == ["User Self-Service"]
    assert schema["paths"]["/v1/user/me"]["get"]["security"] == [{"User API Key": []}]
    assert schema["paths"]["/v1/user/keys"]["get"]["security"] == [{"User API Key": []}]
    assert schema["paths"]["/v1/user/usage"]["get"]["security"] == [{"User API Key": []}]
    assert schema["paths"]["/v1/user/sessions"]["get"]["security"] == [{"User API Key": []}]
    assert schema["paths"]["/management/v1/accounts"]["get"]["tags"] == ["Management Accounts"]
