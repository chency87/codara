from datetime import datetime, timedelta, timezone

from amesh.core.models import ProviderType, Session, SessionStatus, User, UserStatus, Workspace
from amesh.database.manager import DatabaseManager


def test_delete_session_removes_turns_before_parent(tmp_path):
    db_path = tmp_path / "amesh.db"
    db = DatabaseManager(str(db_path))

    now = datetime.now(timezone.utc)
    user_id = "user-1"
    workspace_id = "wsk-1"
    db.save_user(User(
        user_id=user_id,
        email="test@example.com",
        display_name="Test User",
        status=UserStatus.ACTIVE,
        workspace_path=str(tmp_path / "user-1"),
        created_at=now,
        created_by="test",
        updated_at=now
    ))
    db.save_workspace(Workspace(
        workspace_id=workspace_id,
        name="default",
        path=str(tmp_path / "workspace"),
        user_id=user_id,
        created_at=now,
        updated_at=now
    ))

    session_id = "delete-session-e2e"
    db.save_session(
        Session(
            session_id=session_id,
            workspace_id=workspace_id,
            user_id=user_id,
            client_session_id=session_id,
            backend_id="backend-delete",
            provider=ProviderType.CODEX,
            cwd_path=str(tmp_path),
            status=SessionStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(hours=1),
        )
    )
    db.record_turn(
        turn_id="turn-delete-session",
        session_id=session_id,
        provider="codex",
        finish_reason="stop",
        duration_ms=12,
        diff=None,
        actions=[],
        user_id=user_id,
    )

    db.delete_session(session_id)

    assert db.get_session(session_id) is None
    assert db.get_session_turns(session_id) == []
