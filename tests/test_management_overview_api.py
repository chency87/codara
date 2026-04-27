from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
import amesh.gateway.app as gateway_app
from amesh.core.models import ProviderType, Session, SessionStatus, User, UserStatus, Workspace
from amesh.database.manager import DatabaseManager
from tests.helpers import operator_headers

def test_management_overview_summarizes_visible_runtime_state(tmp_path, monkeypatch):
    db_path = tmp_path / "amesh.db"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    monkeypatch.setattr(gateway_app.settings, "release_check_enabled", False)
    gateway_app.clear_auth_caches()
    gateway_app.db_manager = DatabaseManager(str(db_path))

    now = datetime.now(timezone.utc)
    gateway_app.db_manager.save_user(User(
        user_id="user-1",
        email="test@example.com",
        display_name="Test User",
        status=UserStatus.ACTIVE,
        workspace_path=str(tmp_path / "user-1"),
        created_at=now,
        created_by="test",
        updated_at=now
    ))
    gateway_app.db_manager.save_workspace(Workspace(
        workspace_id="wsk-1",
        name="default",
        path=str(tmp_path / "workspace"),
        user_id="user-1",
        created_at=now,
        updated_at=now
    ))

    gateway_app.db_manager.save_session(
        Session(
            session_id="sess-active",
            workspace_id="wsk-1",
            user_id="user-1",
            client_session_id="sess-active",
            backend_id="backend-1",
            provider=ProviderType.CODEX,
            cwd_path=str(tmp_path),
            status=SessionStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(hours=1),
        )
    )
    gateway_app.db_manager.save_session(
        Session(
            session_id="sess-dirty",
            workspace_id="wsk-1",
            user_id="user-1",
            client_session_id="sess-dirty",
            backend_id="backend-2",
            provider=ProviderType.CODEX,
            cwd_path=str(tmp_path),
            status=SessionStatus.DIRTY,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(hours=1),
        )
    )

    client = TestClient(gateway_app.app)
    headers = operator_headers(client)

    resp = client.get("/management/v1/overview", headers=headers)

    assert resp.status_code == 200
    payload = resp.json()["data"]
    assert payload["summary"]["sessions_total"] == 2
    assert payload["summary"]["active_sessions"] == 1
    assert payload["summary"]["dirty_sessions"] == 1
    assert payload["health"]["components"]["gateway"]["latency_ms"] is not None
    assert payload["health"]["components"]["orchestrator"]["latency_ms"] is not None
    assert payload["health"]["components"]["state_store"]["latency_ms"] is not None
    assert payload["version"]["name"] == "amesh"
    assert payload["version"]["version"]
    assert payload["version"]["release_check"]["enabled"] is False
    

def test_management_version_endpoint_can_check_updates(tmp_path, monkeypatch):
    db_path = tmp_path / "amesh.db"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    monkeypatch.setattr(gateway_app.settings, "secret_key", "unit-test-secret")
    monkeypatch.setattr(gateway_app.settings, "release_check_enabled", True)
    monkeypatch.setattr(gateway_app.settings, "release_repository", "amesh/amesh")
    monkeypatch.setattr(gateway_app.settings, "release_api_base_url", "https://api.github.test")
    monkeypatch.setattr(gateway_app.settings, "release_check_timeout_seconds", 1)
    gateway_app.clear_auth_caches()
    gateway_app.db_manager = DatabaseManager(str(db_path))

    class Result:
        def to_dict(self):
            return {
                "current_version": "1.0.0",
                "latest_version": "1.1.0",
                "update_available": True,
                "status": "ok",
                "repository": "amesh/amesh",
                "release_url": "https://github.test/release",
                "checked_url": "https://api.github.test/repos/amesh/amesh/releases/latest",
                "error": None,
            }

    monkeypatch.setattr(gateway_app, "get_version", lambda: "1.0.0")
    monkeypatch.setattr(gateway_app, "check_for_update", lambda **kwargs: Result())

    client = TestClient(gateway_app.app)
    headers = operator_headers(client)

    resp = client.get("/management/v1/version", headers=headers, params={"check_updates": "true"})

    assert resp.status_code == 200
    payload = resp.json()["data"]
    assert payload["version"] == "1.0.0"
    assert payload["release_check"]["latest_version"] == "1.1.0"
    assert payload["release_check"]["update_available"] is True
