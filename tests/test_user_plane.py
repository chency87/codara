import asyncio
from pathlib import Path
import subprocess
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

import codara.gateway.app as gateway_app
from codara.accounts.pool import AccountPool
from codara.adapters.codex import CodexAdapter
from codara.database.manager import DatabaseManager
from codara.core.models import Account, AuthType, Message, ProviderType, Session, SessionStatus, TurnResult, UagOptions
from codara.orchestrator.engine import Orchestrator
from codara.workspace.engine import WorkspaceEngine
from tests.helpers import operator_headers


def test_user_provisioning_and_user_plane(tmp_path, monkeypatch):
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
            "email": "alice@example.com",
            "display_name": "Alice",
            "key_label": "laptop",
            "max_concurrency": 3,
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()["data"]
    raw_key = created["api_key"]["raw_key"]

    assert raw_key.startswith("uagk_live_")
    assert Path(created["workspace_path"]).exists()
    assert (Path(created["workspace_path"]) / ".git").exists()
    assert subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=created["workspace_path"],
        capture_output=True,
        text=True,
        check=False,
    ).returncode == 0

    user_headers = {"Authorization": f"Bearer {raw_key}"}

    me_resp = client.get("/v1/user/me", headers=user_headers)
    assert me_resp.status_code == 200
    assert me_resp.json()["data"]["email"] == "alice@example.com"

    keys_resp = client.get("/v1/user/keys", headers=user_headers)
    assert keys_resp.status_code == 200
    assert len(keys_resp.json()["data"]) == 1

    sessions_resp = client.get("/v1/user/sessions", headers=user_headers)
    assert sessions_resp.status_code == 200
    assert sessions_resp.json()["data"] == []

    usage_resp = client.get("/v1/user/usage", headers=user_headers)
    assert usage_resp.status_code == 200
    assert usage_resp.json()["data"]["summary"]["total_tokens"] == 0


def test_user_self_service_requires_api_key(tmp_path, monkeypatch):
    db_path = tmp_path / "codara.db"
    workspaces_root = tmp_path / "workspaces"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.settings.workspaces_root = str(workspaces_root)
    gateway_app.db_manager = DatabaseManager(str(db_path))
    gateway_app.orchestrator = Orchestrator(gateway_app.db_manager)

    client = TestClient(gateway_app.app)

    for path in ("/v1/user/me", "/v1/user/keys", "/v1/user/usage", "/v1/user/sessions"):
        resp = client.get(path)
        assert resp.status_code == 401

def test_chat_completions_keeps_http_401_for_revoked_key(tmp_path, monkeypatch):
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
            "email": "revoked@example.com",
            "display_name": "Revoked Key User",
            "key_label": "primary",
            "max_concurrency": 2,
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()["data"]
    user_id = created["user_id"]
    raw_key = created["api_key"]["raw_key"]
    key_id = created["api_key"]["key_id"]

    rotate_resp = client.post(
        f"/management/v1/users/{user_id}/keys/rotate",
        headers=headers,
        json={"label": "replacement"},
    )
    assert rotate_resp.status_code == 200

    chat_resp = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={
            "model": "uag-codex-v5",
            "messages": [{"role": "user", "content": "ping"}],
            "provider": "codex",
        },
    )

    assert chat_resp.status_code == 401
    assert chat_resp.json()["detail"] == "API key revoked"

    keys_resp = client.get(f"/management/v1/users/{user_id}/keys", headers=headers)
    assert keys_resp.status_code == 200
    keys = keys_resp.json()["data"]
    assert len(keys) == 1
    assert keys[0]["status"] == "active"
    assert keys[0]["key_id"] != key_id


def test_rotating_user_key_replaces_current_key_in_user_detail(tmp_path, monkeypatch):
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
            "email": "replace-key@example.com",
            "display_name": "Replace Key User",
            "key_label": "primary",
            "max_concurrency": 2,
        },
    )
    created = create_resp.json()["data"]
    user_id = created["user_id"]
    old_key_id = created["api_key"]["key_id"]

    rotate_resp = client.post(
        f"/management/v1/users/{user_id}/keys/rotate",
        headers=headers,
        json={"label": "primary"},
    )
    assert rotate_resp.status_code == 200
    new_key_id = rotate_resp.json()["data"]["key_id"]
    assert new_key_id != old_key_id

    detail_resp = client.get(f"/management/v1/users/{user_id}", headers=headers)
    assert detail_resp.status_code == 200
    detail = detail_resp.json()["data"]
    assert len(detail["api_keys"]) == 1
    assert detail["api_keys"][0]["key_id"] == new_key_id
    assert detail["api_keys"][0]["status"] == "active"

    history = gateway_app.db_manager.list_api_keys(user_id)
    assert len(history) == 2
    assert history[0].key_id == new_key_id
    assert history[0].status == "active"
    assert any(key.key_id == old_key_id and key.status == "revoked" for key in history)

def test_chat_completions_returns_503_when_no_available_account(tmp_path, monkeypatch):
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
            "email": "no-account@example.com",
            "display_name": "No Account User",
            "key_label": "primary",
            "max_concurrency": 2,
        },
    )
    assert create_resp.status_code == 200
    raw_key = create_resp.json()["data"]["api_key"]["raw_key"]

    chat_resp = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={
            "model": "uag-codex-v5",
            "messages": [{"role": "user", "content": "ping"}],
            "uag_options": {"provider": "codex"},
        },
    )

    assert chat_resp.status_code == 503
    assert "No available account for provider" in chat_resp.json()["detail"]


def test_chat_completions_returns_quota_exhaustion_message(tmp_path, monkeypatch):
    db_path = tmp_path / "codara.db"
    workspaces_root = tmp_path / "workspaces"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.settings.workspaces_root = str(workspaces_root)
    gateway_app.db_manager = DatabaseManager(str(db_path))
    gateway_app.orchestrator = Orchestrator(gateway_app.db_manager)

    async def fake_handle_request(options, messages, provider_model=None):
        raise RuntimeError("Exhausted your capacity on this model. Your quota will reset at 5:00 PM.")

    monkeypatch.setattr(gateway_app.orchestrator, "handle_request", fake_handle_request)

    client = TestClient(gateway_app.app)
    headers = operator_headers(client)

    create_resp = client.post(
        "/management/v1/users",
        headers=headers,
        json={
            "email": "quota@example.com",
            "display_name": "Quota User",
            "key_label": "primary",
            "max_concurrency": 2,
        },
    )
    assert create_resp.status_code == 200
    raw_key = create_resp.json()["data"]["api_key"]["raw_key"]

    chat_resp = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={
            "model": "uag-codex-v5",
            "messages": [{"role": "user", "content": "ping"}],
            "uag_options": {"provider": "codex"},
        },
    )

    assert chat_resp.status_code == 429
    assert "Exhausted your capacity on this model" in chat_resp.json()["detail"]


def test_chat_completions_returns_provider_runtime_detail(tmp_path, monkeypatch):
    db_path = tmp_path / "codara.db"
    workspaces_root = tmp_path / "workspaces"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.settings.workspaces_root = str(workspaces_root)
    gateway_app.db_manager = DatabaseManager(str(db_path))
    gateway_app.orchestrator = Orchestrator(gateway_app.db_manager)

    async def fake_handle_request(options, messages, provider_model=None):
        raise RuntimeError("Codex exec failed: missing field `id_token` at line 1 column 68")

    monkeypatch.setattr(gateway_app.orchestrator, "handle_request", fake_handle_request)

    client = TestClient(gateway_app.app)
    headers = operator_headers(client)

    create_resp = client.post(
        "/management/v1/users",
        headers=headers,
        json={
            "email": "provider-error@example.com",
            "display_name": "Provider Error User",
            "key_label": "primary",
            "max_concurrency": 2,
        },
    )
    assert create_resp.status_code == 200
    raw_key = create_resp.json()["data"]["api_key"]["raw_key"]

    chat_resp = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={
            "model": "uag-codex-v5",
            "messages": [{"role": "user", "content": "ping"}],
            "uag_options": {"provider": "codex"},
        },
    )

    assert chat_resp.status_code == 400
    assert "missing field `id_token`" in chat_resp.json()["detail"]


def test_user_workspace_id_resolves_inside_base_workspace(tmp_path, monkeypatch):
    db_path = tmp_path / "codara.db"
    workspaces_root = tmp_path / "workspaces"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.settings.workspaces_root = str(workspaces_root)
    gateway_app.db_manager = DatabaseManager(str(db_path))
    gateway_app.orchestrator = Orchestrator(gateway_app.db_manager)

    observed = {}

    async def fake_handle_request(options, messages, provider_model=None):
        observed["workspace_root"] = options.workspace_root
        observed["workspace_id"] = options.workspace_id
        observed["client_session_id"] = options.client_session_id
        observed["provider_model"] = provider_model
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

    create_resp = client.post(
        "/management/v1/users",
        headers=headers,
        json={
            "email": "workspace@example.com",
            "display_name": "Workspace User",
            "key_label": "primary",
            "max_concurrency": 2,
        },
    )
    created = create_resp.json()["data"]
    raw_key = created["api_key"]["raw_key"]
    base_workspace = Path(created["workspace_path"]).resolve()

    chat_resp = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={
            "model": "uag-codex-v5",
            "messages": [{"role": "user", "content": "ping"}],
            "uag_options": {
                "provider": "codex",
                "workspace_id": "project-a/feature-x",
                "client_session_id": "thread-1",
            },
        },
    )

    assert chat_resp.status_code == 200
    resolved_workspace = Path(observed["workspace_root"]).resolve()
    assert observed["workspace_id"] == "project-a/feature-x"
    assert observed["client_session_id"].endswith("::thread-1")
    assert observed["provider_model"] == gateway_app.settings.codex_default_model
    assert resolved_workspace == base_workspace / "project-a" / "feature-x"
    assert resolved_workspace.is_dir()
    assert (resolved_workspace / ".git").exists()
    assert subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=resolved_workspace,
        capture_output=True,
        text=True,
        check=False,
    ).returncode == 0


def test_chat_completions_passes_explicit_provider_model(tmp_path, monkeypatch):
    db_path = tmp_path / "codara.db"
    workspaces_root = tmp_path / "workspaces"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.settings.workspaces_root = str(workspaces_root)
    gateway_app.db_manager = DatabaseManager(str(db_path))
    gateway_app.orchestrator = Orchestrator(gateway_app.db_manager)

    observed = {}

    async def fake_handle_request(options, messages, provider_model=None):
        observed["provider_model"] = provider_model
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

    create_resp = client.post(
        "/management/v1/users",
        headers=headers,
        json={
            "email": "provider-model@example.com",
            "display_name": "Provider Model User",
            "key_label": "primary",
            "max_concurrency": 2,
        },
    )
    created = create_resp.json()["data"]
    raw_key = created["api_key"]["raw_key"]

    chat_resp = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={
            "model": "gemini-2.5-flash",
            "messages": [{"role": "user", "content": "ping"}],
            "uag_options": {"provider": "gemini", "client_session_id": "thread-1"},
        },
    )

    assert chat_resp.status_code == 200
    assert observed["provider_model"] == "gemini-2.5-flash"


def test_chat_completions_multipart_uploads_are_staged_into_workspace(tmp_path, monkeypatch):
    db_path = tmp_path / "codara.db"
    workspaces_root = tmp_path / "workspaces"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.settings.workspaces_root = str(workspaces_root)
    gateway_app.db_manager = DatabaseManager(str(db_path))
    gateway_app.orchestrator = Orchestrator(gateway_app.db_manager)

    observed = {}

    async def fake_handle_request(options, messages, provider_model=None):
        observed["workspace_root"] = options.workspace_root
        observed["messages"] = list(messages)
        observed["provider_model"] = provider_model
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

    create_resp = client.post(
        "/management/v1/users",
        headers=headers,
        json={
            "email": "upload@example.com",
            "display_name": "Upload User",
            "key_label": "primary",
            "max_concurrency": 2,
        },
    )
    created = create_resp.json()["data"]
    raw_key = created["api_key"]["raw_key"]

    payload = {
        "model": "uag-gemini",
        "messages": [{"role": "user", "content": "summarize the attached file"}],
        "uag_options": {"provider": "gemini", "workspace_id": "project-a", "client_session_id": "thread-1"},
    }
    chat_resp = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw_key}"},
        data={"payload": gateway_app.json.dumps(payload)},
        files={"files": ("notes.txt", b"hello from upload", "text/plain")},
    )

    assert chat_resp.status_code == 200
    body = chat_resp.json()
    attachments = body["extensions"]["attachments"]
    assert len(attachments) == 1
    assert attachments[0]["original_name"] == "notes.txt"
    assert attachments[0]["content_type"] == "text/plain"
    assert attachments[0]["path"].startswith(".uag/uploads/")
    assert attachments[0]["path"].endswith("/notes.txt")
    assert "thread-1" in attachments[0]["path"]

    uploaded_path = Path(observed["workspace_root"]) / attachments[0]["path"]
    assert uploaded_path.read_text() == "hello from upload"
    assert observed["provider_model"] == gateway_app.settings.gemini_default_model
    assert observed["messages"][0].role == "system"
    assert "notes.txt" in observed["messages"][0].content
    assert attachments[0]["path"] in observed["messages"][0].content
    assert observed["messages"][1].role == "user"


def test_manual_mode_response_contains_exact_atr_actions(tmp_path, monkeypatch):
    db_path = tmp_path / "codara.db"
    workspaces_root = tmp_path / "workspaces"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.settings.workspaces_root = str(workspaces_root)
    gateway_app.db_manager = DatabaseManager(str(db_path))
    gateway_app.orchestrator = Orchestrator(gateway_app.db_manager)

    class FakeAdapter:
        async def send_turn(self, session, messages, provider_model):
            return TurnResult(
                output="""### src/app.py
<<<<<<< SEARCH
old_value = 1
=======
old_value = 2
>>>>>>> REPLACE
""",
                backend_id="backend-1",
                finish_reason="stop",
                modified_files=[],
                diff=None,
                actions=[],
                dirty=False,
            )

    monkeypatch.setattr(gateway_app.orchestrator, "_get_adapter", lambda provider: FakeAdapter())

    from codara.accounts.pool import AccountPool
    from codara.core.models import Account, AuthType

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
            "email": "manual@example.com",
            "display_name": "Manual User",
            "key_label": "primary",
            "max_concurrency": 2,
        },
    )
    raw_key = create_resp.json()["data"]["api_key"]["raw_key"]

    chat_resp = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={
            "model": "uag-codex-v5",
            "messages": [{"role": "user", "content": "prepare the edit"}],
            "uag_options": {
                "provider": "codex",
                "client_session_id": "thread-1",
                "manual_mode": True,
            },
        },
    )

    assert chat_resp.status_code == 200
    action = chat_resp.json()["extensions"]["actions"][0]
    assert action["type"] == "patch"
    assert action["format"] == "search_replace"
    assert action["path"] == "src/app.py"
    assert action["search"] == "old_value = 1"
    assert action["replace"] == "old_value = 2"
    assert action["exact"] is True
    assert chat_resp.json()["extensions"]["diff"] is None
    assert chat_resp.json()["extensions"]["modified_files"] == []


def test_user_workspace_id_rejects_path_traversal(tmp_path, monkeypatch):
    db_path = tmp_path / "codara.db"
    workspaces_root = tmp_path / "workspaces"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.settings.workspaces_root = str(workspaces_root)
    gateway_app.db_manager = DatabaseManager(str(db_path))
    gateway_app.orchestrator = Orchestrator(gateway_app.db_manager)

    async def fake_handle_request(options, messages, provider_model=None):
        raise AssertionError("orchestrator should not be called for invalid workspace ids")

    monkeypatch.setattr(gateway_app.orchestrator, "handle_request", fake_handle_request)

    client = TestClient(gateway_app.app)
    headers = operator_headers(client)

    create_resp = client.post(
        "/management/v1/users",
        headers=headers,
        json={
            "email": "invalid-workspace@example.com",
            "display_name": "Invalid Workspace User",
            "key_label": "primary",
            "max_concurrency": 2,
        },
    )
    raw_key = create_resp.json()["data"]["api_key"]["raw_key"]

    chat_resp = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={
            "model": "uag-codex-v5",
            "messages": [{"role": "user", "content": "ping"}],
            "uag_options": {"provider": "codex", "workspace_id": "../escape"},
        },
    )

    assert chat_resp.status_code == 400
    assert chat_resp.json()["detail"] == "Invalid workspace_id"


def test_orchestrator_preserves_gemini_backend_id_between_turns(tmp_path):
    db = DatabaseManager(str(tmp_path / "codara.db"))
    orchestrator = Orchestrator(db)
    observed = []

    class FakeAdapter:
        async def send_turn(self, session, messages, provider_model):
            observed.append((session.backend_id, provider_model))
            return TurnResult(
                output="ok",
                backend_id=session.backend_id or "gem-backend-1",
                finish_reason="stop",
                modified_files=[],
                diff=None,
                actions=[],
                dirty=False,
                context_tokens=5,
            )

    orchestrator._get_adapter = lambda provider: FakeAdapter()
    gateway_app.orchestrator = orchestrator

    options = UagOptions(
        provider=ProviderType.GEMINI,
        workspace_root=str(tmp_path),
        client_session_id="gem-thread",
    )
    asyncio.run(
        orchestrator.handle_request(
            options,
            [Message(role="user", content="first")],
            provider_model="gemini-2.5-flash",
        )
    )
    asyncio.run(
        orchestrator.handle_request(
            options,
            [Message(role="user", content="second")],
            provider_model="gemini-2.5-flash",
        )
    )

    assert observed == [("", "gemini-2.5-flash"), ("gem-backend-1", "gemini-2.5-flash")]


def test_orchestrator_converts_workspace_diff_into_atr_actions(tmp_path):
    db = DatabaseManager(str(tmp_path / "codara.db"))
    orchestrator = Orchestrator(db)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    WorkspaceEngine(str(workspace)).ensure_git_repository()

    class FakeAdapter:
        async def send_turn(self, session, messages, provider_model):
            (Path(session.cwd_path) / "main.py").write_text("print('hello')\n", encoding="utf-8")
            return TurnResult(
                output="Done.",
                backend_id="codex-backend-1",
                finish_reason="stop",
                modified_files=[],
                diff=None,
                actions=[],
                dirty=False,
                context_tokens=5,
            )

    orchestrator._get_adapter = lambda provider: FakeAdapter()

    result = asyncio.run(
        orchestrator.handle_request(
            UagOptions(
                provider=ProviderType.GEMINI,
                workspace_root=str(workspace),
                client_session_id="diff-thread",
            ),
            [Message(role="user", content="write a file")],
            provider_model="gemini-2.5-flash",
        )
    )

    assert result.diff is not None
    assert "diff --git a/main.py b/main.py" in result.diff
    assert result.actions
    action = result.actions[0]
    assert action["format"] == "unified_diff"
    assert action["path"] == "main.py"
    assert "print('hello')" in action["patch"]


def test_orchestrator_reuses_adapter_instances(tmp_path):
    db = DatabaseManager(str(tmp_path / "codara.db"))
    orchestrator = Orchestrator(db)

    first = orchestrator._get_adapter(ProviderType.CODEX)
    second = orchestrator._get_adapter(ProviderType.CODEX)

    assert first is second


def test_orchestrator_syncs_codex_session_state_when_failing_over_accounts(tmp_path, monkeypatch):
    db = DatabaseManager(str(tmp_path / "codara.db"))
    orchestrator = Orchestrator(db)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    WorkspaceEngine(str(workspace)).ensure_git_repository()

    pool = AccountPool(db)
    account_a = Account(
        account_id="codex-a",
        provider=ProviderType.CODEX,
        auth_type=AuthType.API_KEY,
        label="Codex A",
    )
    account_b = Account(
        account_id="codex-b",
        provider=ProviderType.CODEX,
        auth_type=AuthType.API_KEY,
        label="Codex B",
    )
    pool.register_account(account_a, "sk-a")
    pool.register_account(account_b, "sk-b")

    now = datetime.now()
    db.save_session(
        Session(
            client_session_id="codex-thread",
            backend_id="backend-1",
            provider=ProviderType.CODEX,
            account_id="codex-a",
            cwd_path=str(workspace),
            prefix_hash="prefix",
            status=SessionStatus.IDLE,
            fence_token=0,
            last_context_tokens=0,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(hours=1),
        )
    )

    def fake_acquire_account(provider):
        return db.get_account("codex-b")

    monkeypatch.setattr(orchestrator.account_pool, "acquire_account", fake_acquire_account)

    class FakeCodexAdapter(CodexAdapter):
        def __init__(self):
            super().__init__(db_manager=db)
            self.send_calls = []
            self.synced = []

        async def send_turn(self, session, messages, provider_model):
            self.send_calls.append((session.account_id, session.backend_id, provider_model))
            if len(self.send_calls) == 1:
                raise RuntimeError("Codex Rate Limit: 429 rate limit exceeded")
            return TurnResult(
                output="ok",
                backend_id="backend-1",
                finish_reason="stop",
                modified_files=[],
                diff=None,
                actions=[],
                dirty=False,
                context_tokens=9,
            )

        def sync_account_session_state(self, source_account_id: str, target_account_id: str) -> bool:
            self.synced.append((source_account_id, target_account_id))
            return True

    adapter = FakeCodexAdapter()
    orchestrator._adapters[ProviderType.CODEX] = adapter

    result = asyncio.run(
        orchestrator.handle_request(
            UagOptions(
                provider=ProviderType.CODEX,
                workspace_root=str(workspace),
                client_session_id="codex-thread",
            ),
            [Message(role="user", content="continue")],
            provider_model="gpt-5-codex",
        )
    )

    assert result.output == "ok"
    assert adapter.synced == [("codex-a", "codex-b")]
    assert adapter.send_calls == [
        ("codex-a", "backend-1", "gpt-5-codex"),
        ("codex-b", "backend-1", "gpt-5-codex"),
    ]


def test_user_provider_models_endpoint_returns_adapter_listings(tmp_path, monkeypatch):
    db_path = tmp_path / "codara.db"
    workspaces_root = tmp_path / "workspaces"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.settings.workspaces_root = str(workspaces_root)
    gateway_app.db_manager = DatabaseManager(str(db_path))
    gateway_app.orchestrator = Orchestrator(gateway_app.db_manager)

    class FakeAdapter:
        async def send_turn(self, session, messages, provider_model):
            raise AssertionError("send_turn should not be called")

        async def collect_usage(self, account, credential, settings):
            return None

        async def list_models(self, settings):
            return {
                "provider": "codex",
                "default_model": "gpt-5-codex",
                "models": ["gpt-5-codex", "gpt-5"],
                "source": "cli",
                "status": "ok",
                "runtime_available": True,
                "detail": None,
                "cached": False,
            }

    monkeypatch.setattr(gateway_app.orchestrator, "_get_adapter", lambda provider: FakeAdapter())

    client = TestClient(gateway_app.app)
    headers = operator_headers(client)
    create_resp = client.post(
        "/management/v1/users",
        headers=headers,
        json={
            "email": "models@example.com",
            "display_name": "Models User",
            "key_label": "primary",
            "max_concurrency": 2,
        },
    )
    raw_key = create_resp.json()["data"]["api_key"]["raw_key"]

    resp = client.get("/v1/user/providers/models?provider=codex", headers={"Authorization": f"Bearer {raw_key}"})

    assert resp.status_code == 200
    payload = resp.json()["data"]
    assert payload[0]["provider"] == "codex"
    assert payload[0]["models"] == ["gpt-5-codex", "gpt-5"]
