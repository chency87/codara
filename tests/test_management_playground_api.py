from amesh.core.models import TurnResult, ProviderType, Session, SessionStatus
from pathlib import Path

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

    async def list_models(self, settings):
        return {
            "provider": "codex",
            "default_model": "gpt-5-codex",
            "models": ["gpt-5-codex"],
            "source": "config",
            "status": "fallback",
            "runtime_available": True,
            "detail": None,
            "cached": False,
        }

def test_management_playground_binds_turns_to_dashboard_admin_user(tmp_path, monkeypatch):
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

    resp = client.post(
        "/management/v1/playground/chat",
        headers=headers,
        json={
            "model": "uag-codex",
            "messages": [{"role": "user", "content": "hello"}],
            "uag_options": {
                "provider": "codex",
                "workspace_id": "project-a",
                "client_session_id": "thread-1",
            },
        },
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["extensions"]["bound_user_display_name"] == "Dashboard Admin"

    admin_user = gateway_app.db_manager.get_user_by_email("dashboard-admin@amesh.local")
    assert admin_user is not None

    session_id = payload["extensions"]["client_session_id"]
    session = gateway_app.db_manager.get_session(session_id)
    assert session is not None
    assert session.user_id == admin_user.user_id
    active_keys = gateway_app.db_manager.list_active_api_keys(admin_user.user_id)
    assert len(active_keys) == 1
    assert session.api_key_id == active_keys[0].key_id
    assert session.cwd_path.endswith(f"{admin_user.user_id}/project-a") or session.cwd_path.endswith("project-a")
    actions = [row["action"] for row in gateway_app.db_manager.get_audit_logs()]
    assert "playground.turn.executed" in actions

    users_resp = client.get("/management/v1/users", headers=headers)
    assert users_resp.status_code == 200
    assert any(item["email"] == "dashboard-admin@amesh.local" for item in users_resp.json()["data"])


def test_management_playground_accepts_multipart_uploads(tmp_path, monkeypatch):
    db_path = tmp_path / "amesh.db"
    workspaces_root = tmp_path / "workspaces"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.settings.workspaces_root = str(workspaces_root)
    gateway_app.db_manager = DatabaseManager(str(db_path))
    gateway_app.orchestrator = Orchestrator(gateway_app.db_manager)

    observed = {}

    async def fake_handle_request(options, messages, provider_model=None, workspace_id=None):
        observed["workspace_root"] = options.workspace_root
        observed["messages"] = list(messages)
        return TurnResult(
            output="ok",
            backend_id="backend-1",
            finish_reason="stop",
            modified_files=[],
            diff=None,
            actions=[],
            dirty=False,
        )

    monkeypatch.setattr(gateway_app.orchestrator, "handle_request", fake_handle_request)

    client = TestClient(gateway_app.app)
    headers = operator_headers(client)

    payload = {
        "model": "uag-gemini",
        "messages": [{"role": "user", "content": "inspect the uploaded file"}],
        "uag_options": {"provider": "gemini", "workspace_id": "project-a", "client_session_id": "thread-1"},
    }
    resp = client.post(
        "/management/v1/playground/chat",
        headers=headers,
        data={"payload": gateway_app.json.dumps(payload)},
        files={"files": ("playground.txt", b"playground upload", "text/plain")},
    )

    assert resp.status_code == 200
    body = resp.json()
    attachments = body["extensions"]["attachments"]
    assert len(attachments) == 1
    assert attachments[0]["original_name"] == "playground.txt"
    assert attachments[0]["path"].startswith(".uag/uploads/")
    assert attachments[0]["path"].endswith("/playground.txt")
    assert "thread-1" in attachments[0]["path"]
    assert (Path(observed["workspace_root"]) / attachments[0]["path"]).read_text() == "playground upload"
    assert observed["messages"][0].role == "system"
    assert "playground.txt" in observed["messages"][0].content
    assert attachments[0]["path"] in observed["messages"][0].content


def test_management_playground_uses_system_gemini_without_bootstrap_account(tmp_path, monkeypatch):
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

    resp = client.post(
        "/management/v1/playground/chat",
        headers=headers,
        json={
            "model": "uag-gemini",
            "messages": [{"role": "user", "content": "hello"}],
            "uag_options": {
                "provider": "gemini",
                "workspace_id": "project-a",
                "client_session_id": "thread-1",
            },
        },
    )

    assert resp.status_code == 200
    session_id = resp.json()["extensions"]["client_session_id"]
    session = gateway_app.db_manager.get_session(session_id)
    assert session is not None
    assert session.provider == ProviderType.GEMINI


def test_management_playground_returns_provider_runtime_detail(tmp_path, monkeypatch):
    db_path = tmp_path / "amesh.db"
    workspaces_root = tmp_path / "workspaces"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.settings.workspaces_root = str(workspaces_root)
    gateway_app.db_manager = DatabaseManager(str(db_path))
    gateway_app.orchestrator = Orchestrator(gateway_app.db_manager)

    async def fake_handle_request(options, messages, provider_model=None, workspace_id=None):
        raise RuntimeError("Codex exec failed: missing field `id_token` at line 1 column 68")

    monkeypatch.setattr(gateway_app.orchestrator, "handle_request", fake_handle_request)

    client = TestClient(gateway_app.app)
    headers = operator_headers(client)

    resp = client.post(
        "/management/v1/playground/chat",
        headers=headers,
        json={
            "model": "uag-codex",
            "messages": [{"role": "user", "content": "hello"}],
            "uag_options": {
                "provider": "codex",
                "workspace_id": "project-a",
                "client_session_id": "thread-1",
            },
        },
    )

    assert resp.status_code == 400
    assert "missing field `id_token`" in resp.json()["detail"]


def test_management_playground_records_failed_adapter_execution(tmp_path, monkeypatch):
    db_path = tmp_path / "amesh.db"
    workspaces_root = tmp_path / "workspaces"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.settings.workspaces_root = str(workspaces_root)
    gateway_app.db_manager = DatabaseManager(str(db_path))
    gateway_app.orchestrator = Orchestrator(gateway_app.db_manager)

    class FailingAdapter(_FakeAdapter):
        async def send_turn(self, session, messages, provider_model):
            raise RuntimeError("Gemini CLI failed")

        async def list_models(self, settings):
            return {
                "provider": "gemini",
                "default_model": "gemini-2.5-pro",
                "models": ["gemini-2.5-pro"],
                "source": "config",
                "status": "fallback",
                "runtime_available": True,
                "detail": None,
                "cached": False,
            }

    monkeypatch.setattr(gateway_app.orchestrator, "_get_adapter", lambda provider: FailingAdapter())

    client = TestClient(gateway_app.app)
    headers = operator_headers(client)

    resp = client.post(
        "/management/v1/playground/chat",
        headers=headers,
        json={
            "model": "uag-gemini",
            "messages": [{"role": "user", "content": "hello"}],
            "uag_options": {
                "provider": "gemini",
                "workspace_id": "project-a",
                "client_session_id": "thread-1",
            },
        },
    )

    assert resp.status_code == 429


def test_management_playground_resets_provider_state_when_session_label_changes_provider(tmp_path, monkeypatch):
    db_path = tmp_path / "amesh.db"
    workspaces_root = tmp_path / "workspaces"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.settings.workspaces_root = str(workspaces_root)
    gateway_app.db_manager = DatabaseManager(str(db_path))
    gateway_app.orchestrator = Orchestrator(gateway_app.db_manager)

    observed: dict[str, list[dict]] = {"codex": [], "gemini": []}

    class ProviderAdapter:
        def __init__(self, provider_name: str):
            self.provider_name = provider_name

        async def send_turn(self, session, messages, provider_model):
            observed[self.provider_name].append(
                {
                    "backend_id": session.backend_id,
                    "provider_model": provider_model,
                }
            )
            return TurnResult(
                output=f"{self.provider_name} ok",
                backend_id=f"{self.provider_name}-backend",
                finish_reason="stop",
                modified_files=[],
                diff=None,
                actions=[],
                dirty=False,
            )

        async def list_models(self, settings):
            return {
                "provider": self.provider_name,
                "default_model": f"{self.provider_name}-default",
                "models": [f"{self.provider_name}-default"],
                "source": "config",
                "status": "fallback",
                "runtime_available": True,
                "detail": None,
                "cached": False,
            }

    adapters = {
        ProviderType.CODEX: ProviderAdapter("codex"),
        ProviderType.GEMINI: ProviderAdapter("gemini"),
    }
    monkeypatch.setattr(gateway_app.orchestrator, "_get_adapter", lambda provider: adapters[provider])

    client = TestClient(gateway_app.app)
    headers = operator_headers(client)

    first = client.post(
        "/management/v1/playground/chat",
        headers=headers,
        json={
            "model": "uag-codex",
            "messages": [{"role": "user", "content": "hello"}],
            "uag_options": {
                "provider": "codex",
                "workspace_id": "project-a",
                "client_session_id": "thread-1",
            },
        },
    )
    assert first.status_code == 200

    second = client.post(
        "/management/v1/playground/chat",
        headers=headers,
        json={
            "model": "uag-gemini",
            "messages": [{"role": "user", "content": "hello"}],
            "uag_options": {
                "provider": "gemini",
                "workspace_id": "project-a",
                "client_session_id": "thread-1",
            },
        },
    )
    assert second.status_code == 200

    assert observed["gemini"][0]["backend_id"] == ""

    session_id = second.json()["extensions"]["client_session_id"]
    session = gateway_app.db_manager.get_session(session_id)
    assert session is not None
    assert session.provider == ProviderType.GEMINI
    assert session.backend_id == "gemini-backend"


def test_management_provider_models_endpoint_returns_all_providers(tmp_path, monkeypatch):
    db_path = tmp_path / "amesh.db"
    workspaces_root = tmp_path / "workspaces"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.settings.workspaces_root = str(workspaces_root)
    gateway_app.db_manager = DatabaseManager(str(db_path))
    gateway_app.orchestrator = Orchestrator(gateway_app.db_manager)

    class FakeAdapter:
        def __init__(self, provider_name: str):
            self.provider_name = provider_name

        async def send_turn(self, session, messages, provider_model):
            raise AssertionError("send_turn should not be called")

        async def list_models(self, settings):
            return {
                "provider": self.provider_name,
                "default_model": f"{self.provider_name}-default",
                "models": [f"{self.provider_name}-default"],
                "source": "config",
                "status": "fallback",
                "runtime_available": True,
                "detail": None,
                "cached": False,
            }

    monkeypatch.setattr(
        gateway_app.orchestrator,
        "_get_adapter",
        lambda provider: FakeAdapter(provider.value),
    )

    client = TestClient(gateway_app.app)
    headers = operator_headers(client)

    resp = client.get("/management/v1/providers/models", headers=headers)

    assert resp.status_code == 200
    providers = [item["provider"] for item in resp.json()["data"]]
    assert providers == ["codex", "gemini", "opencode"]


def test_management_provider_health_marks_local_clis_ok_without_accounts(tmp_path, monkeypatch):
    db_path = tmp_path / "amesh.db"
    workspaces_root = tmp_path / "workspaces"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.settings.workspaces_root = str(workspaces_root)
    gateway_app.db_manager = DatabaseManager(str(db_path))
    gateway_app.orchestrator = Orchestrator(gateway_app.db_manager)

    class FakeAdapter:
        def __init__(self, provider_name: str):
            self.provider_name = provider_name

        async def send_turn(self, session, messages, provider_model):
            raise AssertionError("send_turn should not be called")

        async def list_models(self, settings):
            return {
                "provider": self.provider_name,
                "default_model": f"{self.provider_name}-default",
                "models": [f"{self.provider_name}-default"],
                "source": "cli" if self.provider_name != "codex" else "config",
                "status": "ok" if self.provider_name != "codex" else "fallback",
                "runtime_available": self.provider_name != "codex",
                "detail": None,
                "cached": False,
            }

    monkeypatch.setattr(gateway_app.orchestrator, "_get_adapter", lambda provider: FakeAdapter(provider.value))

    client = TestClient(gateway_app.app)
    headers = operator_headers(client)

    resp = client.get("/management/v1/health/providers", headers=headers)

    assert resp.status_code == 200
    rows = {item["provider"]: item for item in resp.json()["data"]}
    assert rows["gemini"]["status"] == "ready"
    assert rows["gemini"]["runtime_available"] is True
    assert rows["opencode"]["status"] == "ready"
    assert rows["opencode"]["runtime_available"] is True
    assert rows["codex"]["status"] == "unavailable"
