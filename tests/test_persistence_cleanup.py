from datetime import datetime, timedelta, timezone

from codara.core.models import Account, AuthType, ProviderType, Session, SessionStatus
from codara.database.manager import DatabaseManager


def test_delete_session_removes_turns_before_parent(tmp_path):
    db_path = tmp_path / "codara.db"
    db = DatabaseManager(str(db_path))
    account = Account(
        account_id="codex-delete-session",
        provider=ProviderType.CODEX,
        auth_type=AuthType.API_KEY,
        label="Delete Session",
    )
    db.save_account(account)

    now = datetime.now(timezone.utc)
    session_id = "delete-session-e2e"
    db.save_session(
        Session(
            client_session_id=session_id,
            backend_id="backend-delete",
            provider=ProviderType.CODEX,
            account_id=account.account_id,
            cwd_path=str(tmp_path),
            prefix_hash="prefix",
            status=SessionStatus.ACTIVE,
            fence_token=1,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(hours=1),
        )
    )
    db.record_turn(
        turn_id="turn-delete-session",
        session_id=session_id,
        user_id=None,
        provider="codex",
        account_id=account.account_id,
        input_tokens=10,
        output_tokens=5,
        finish_reason="stop",
        duration_ms=12,
        diff=None,
        actions=[],
    )

    db.delete_session(session_id)

    assert db.get_session(session_id) is None
    assert db.get_session_turns(session_id) == []


def test_delete_account_removes_linked_sessions_and_turns(tmp_path):
    db_path = tmp_path / "codara.db"
    db = DatabaseManager(str(db_path))
    account = Account(
        account_id="codex-delete-account",
        provider=ProviderType.CODEX,
        auth_type=AuthType.API_KEY,
        label="Delete Account",
    )
    db.save_account(account)

    now = datetime.now(timezone.utc)
    session_id = "delete-account-session"
    db.save_session(
        Session(
            client_session_id=session_id,
            backend_id="backend-account",
            provider=ProviderType.CODEX,
            account_id=account.account_id,
            cwd_path=str(tmp_path),
            prefix_hash="prefix",
            status=SessionStatus.ACTIVE,
            fence_token=1,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(hours=1),
        )
    )
    db.record_turn(
        turn_id="turn-delete-account",
        session_id=session_id,
        user_id=None,
        provider="codex",
        account_id=account.account_id,
        input_tokens=20,
        output_tokens=7,
        finish_reason="stop",
        duration_ms=24,
        diff=None,
        actions=[],
    )

    db.delete_account(account.account_id)

    assert db.get_account(account.account_id) is None
    assert db.get_session(session_id) is None
    assert db.get_session_turns(session_id) == []
