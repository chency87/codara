import os
import asyncio
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import codara.adapters.base as base_adapter_module
import codara.adapters.codex as codex_adapter_module
from codara.adapters.codex import CodexAdapter
from codara.adapters.gemini import GeminiAdapter
from codara.adapters.opencode import OpenCodeAdapter
import codara.adapters.gemini as gemini_adapter_module
import codara.adapters.opencode as opencode_adapter_module
from codara.config import get_settings
from codara.core.models import Message, Session, TurnResult, ProviderType, SessionStatus
from codara.core.models import Account, AuthType
from codara.database.manager import DatabaseManager
from codara.accounts.pool import AccountPool

class SilentReader:
    async def read(self, size=-1):
        await asyncio.sleep(30)
        return b""


class FakeStalledProc:
    def __init__(self):
        self.returncode = None
        self.stdout = SilentReader()
        self.stderr = SilentReader()
        self.stdin = MagicMock()
        async def drain():
            return None
        async def wait_closed():
            return None
        self.stdin.drain = drain
        self.stdin.wait_closed = wait_closed
        self._wait_future = asyncio.get_running_loop().create_future()
        self.terminated = False

    async def wait(self):
        return await self._wait_future

    def terminate(self):
        self.terminated = True
        self.returncode = 143
        if not self._wait_future.done():
            self._wait_future.set_result(self.returncode)

    def kill(self):
        self.returncode = 137
        if not self._wait_future.done():
            self._wait_future.set_result(self.returncode)


def test_codex_adapter_extracts_all_usage_fields():
    adapter = CodexAdapter()
    payload = {
        "rate_limit": {
            "allowed": True,
            "limit_reached": False,
            "primary_window": {
                "used_percent": 10.5,
                "reset_after_seconds": 3600,
                "reset_at": 1713000000,
            },
            "secondary_window": {
                "used_percent": 20.5,
                "reset_after_seconds": 604800,
                "reset_at": 1713600000,
            },
        },
        "credits": {
            "has_credits": True,
            "unlimited": False,
            "overage_limit_reached": False,
            "balance": "42.42",
            "approx_local_messages": [100, 200],
            "approx_cloud_messages": [50, 100],
        },
        "plan_type": "team",
    }

    result = adapter._extract_wham_usage(payload)
    assert result == {
        "usage_source": "wham",
        "plan_type": "team",
        "rate_limit_allowed": True,
        "rate_limit_reached": False,
        "hourly_used_pct": 10.5,
        "weekly_used_pct": 20.5,
        "hourly_reset_after_seconds": 3600,
        "weekly_reset_after_seconds": 604800,
        "hourly_reset_at": 1713000000,
        "weekly_reset_at": 1713600000,
        "credits_has_credits": True,
        "credits_unlimited": False,
        "credits_overage_limit_reached": False,
        "credits_balance": 42.42,
        "approx_local_messages_min": 100,
        "approx_local_messages_max": 200,
        "approx_cloud_messages_min": 50,
        "approx_cloud_messages_max": 100,
    }


def test_codex_adapter_syncs_session_state_between_account_homes(monkeypatch, tmp_path):
    monkeypatch.setattr(
        base_adapter_module,
        "get_settings",
        lambda: SimpleNamespace(isolated_envs_root=str(tmp_path)),
    )
    adapter = CodexAdapter()

    source_home = adapter._resolve_account_scoped_home("codex", "acct-a")
    target_home = adapter._resolve_account_scoped_home("codex", "acct-b")
    source_config = source_home / ".codex"
    target_config = target_home / ".codex"
    (source_config / "sessions" / "2026").mkdir(parents=True, exist_ok=True)
    target_config.mkdir(parents=True, exist_ok=True)
    (source_config / "sessions" / "2026" / "session.json").write_text('{"id":"abc"}', encoding="utf-8")
    (source_config / "state_5.sqlite").write_text("state", encoding="utf-8")
    (source_config / "history.jsonl").write_text("history", encoding="utf-8")
    (target_config / "auth.json").write_text('{"tokens":{"access_token":"new"}}', encoding="utf-8")

    copied = adapter.sync_account_session_state("acct-a", "acct-b")

    assert copied is True
    assert (target_config / "sessions" / "2026" / "session.json").read_text(encoding="utf-8") == '{"id":"abc"}'
    assert (target_config / "state_5.sqlite").read_text(encoding="utf-8") == "state"
    assert (target_config / "history.jsonl").read_text(encoding="utf-8") == "history"
    assert (target_config / "auth.json").read_text(encoding="utf-8") == '{"tokens":{"access_token":"new"}}'

def test_gemini_adapter_extracts_cli_stats_usage():
    adapter = GeminiAdapter()
    session_output = """
        Google AI Credits: 42.0
        25% used (Limit resets in 2h)
        Usage limit: 1,000
    """
    model_output = """
        Model           Requests   Input Tokens   Output Tokens
        gemini-pro      2          1000           500
        gemini-flash    1          50             100
    """
    result = adapter._extract_cli_stats_usage(session_output, model_output)
    # The new architecture disables detailed CLI stats collection, so we only parse the session panel
    assert result == {
        "status": "ready",
        "usage_source": "cli_stats",
        "credits_balance": 42.0,
        "hourly_used_pct": 25.0,
        "hourly_reset_after_seconds": 7200,
        "hourly_limit": 1000,
        "usage_hourly": 250,
    }

def test_gemini_adapter_collect_usage_uses_system_cli_stats(monkeypatch):
    adapter = GeminiAdapter()
    monkeypatch.setattr(adapter, "_run_system_cli_stats", lambda: None)
    usage = asyncio.run(
        adapter.collect_usage(
            type("AccountRef", (), {"account_id": "gem-cli"})(),
            '{"access_token":"token"}',
            None,
        )
    )
    assert usage is None

def test_gemini_adapter_extracts_wham_usage():
    adapter = GeminiAdapter()
    payload = {
        "rate_limit": {"allowed": False, "limit_reached": True},
        "plan_type": "free",
    }
    result = adapter._extract_wham_usage(payload)
    assert result["plan_type"] == "free"
    assert result["rate_limit_allowed"] is False
    assert result["rate_limit_reached"] is True

def test_gemini_adapter_send_turn_uses_local_cli(monkeypatch, tmp_path):
    adapter = GeminiAdapter()
    captured = {}

    class FakeProc:
        def __init__(self):
            self.returncode = 0
        async def communicate(self, input_data=None):
            captured["stdin"] = input_data
            return (
                json.dumps({
                    "session_id": "gem-session-456",
                    "response": "done from gemini",
                    "stats": {"models": {"gemini-2.5-pro": {"tokens": {"input": 9, "candidates": 4}}}},
                }).encode(),
                b"",
            )
        def terminate(self): captured["terminated"] = True
        async def wait(self): captured["waited"] = True; return self.returncode

    async def fake_create_subprocess_exec(*command, **kwargs):
        captured["command"] = list(command)
        captured["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr(gemini_adapter_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    now = datetime.now(timezone.utc)
    session = Session(
        client_session_id="client-1",
        backend_id="existing-gemini-session",
        provider=ProviderType.GEMINI,
        account_id="gem-account",
        cwd_path=str(tmp_path),
        prefix_hash="prefix",
        status=SessionStatus.IDLE,
        fence_token=0,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=1),
        last_context_tokens=0,
    )

    result = asyncio.run(
        adapter.send_turn(
            session,
            [Message(role="system", content="You are helpful."), Message(role="user", content="Say hi")],
            "gemini-2.5-flash",
        )
    )

    assert captured["command"][:5] == [adapter._resolve_executable("gemini"), "--yolo", "--sandbox=false", "--output-format", "json"]
    assert "--sandbox" not in captured["command"]
    assert captured["command"][5:7] == ["--model", "gemini-2.5-flash"]
    assert "--resume" in captured["command"]
    assert "existing-gemini-session" in captured["command"]
    assert "--prompt" in captured["command"]
    assert captured["command"][captured["command"].index("--prompt") + 1] == ""
    assert captured["stdin"] == b"SYSTEM:\nYou are helpful.\n\nUSER:\nSay hi"
    assert captured["kwargs"]["cwd"] == str(tmp_path)
    assert captured["kwargs"]["env"]["HOME"] == os.environ["HOME"]
    assert result.output == "done from gemini"
    assert result.context_tokens == 13

def test_gemini_adapter_send_turn_maps_rate_limit_errors(monkeypatch, tmp_path):
    adapter = GeminiAdapter()

    class FakeProc:
        def __init__(self): self.returncode = 1
        async def communicate(self, input_data=None): return (b"", b"429 rate limit exceeded")
        def terminate(self): return None
        async def wait(self): return self.returncode

    async def fake_create_subprocess_exec(*command, **kwargs): return FakeProc()

    monkeypatch.setattr(gemini_adapter_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    now = datetime.now(timezone.utc)
    session = Session(
        client_session_id="client-2",
        backend_id="",
        provider=ProviderType.GEMINI,
        account_id="gem-account",
        cwd_path=str(tmp_path),
        prefix_hash="prefix",
        status=SessionStatus.IDLE,
        fence_token=0,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=1),
        last_context_tokens=0,
    )

    try:
        asyncio.run(adapter.send_turn(session, [Message(role="user", content="Say hi")], "gemini-2.5-pro"))
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "Gemini Rate Limit" in str(exc)

def test_gemini_adapter_retries_without_resume_when_session_id_is_invalid(monkeypatch, tmp_path):
    adapter = GeminiAdapter()
    commands: list[list[str]] = []

    class FakeProc:
        def __init__(self, returncode, stdout, stderr):
            self.returncode = returncode
            self._stdout = stdout
            self._stderr = stderr

        async def communicate(self, input_data=None):
            return self._stdout, self._stderr

        def terminate(self):
            return None

        async def wait(self):
            return self.returncode

    responses = [
        FakeProc(
            1,
            b"",
            (
                'YOLO mode is enabled. All tool calls will be automatically approved.\n'
                'Error resuming session: Invalid session identifier "stale-gemini-session".'
            ).encode(),
        ),
        FakeProc(
            0,
            json.dumps(
                {
                    "session_id": "fresh-gemini-session",
                    "response": "recovered",
                    "stats": {"models": {"gemini-2.5-pro": {"tokens": {"input": 2, "candidates": 1}}}},
                }
            ).encode(),
            b"",
        ),
    ]

    async def fake_create_subprocess_exec(*command, **kwargs):
        commands.append(list(command))
        return responses.pop(0)

    monkeypatch.setattr(gemini_adapter_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    now = datetime.now(timezone.utc)
    session = Session(
        client_session_id="client-gemini-retry",
        backend_id="stale-gemini-session",
        provider=ProviderType.GEMINI,
        account_id="gemini-system",
        cwd_path=str(tmp_path),
        prefix_hash="prefix",
        status=SessionStatus.IDLE,
        fence_token=0,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=1),
        last_context_tokens=0,
    )

    result = asyncio.run(adapter.send_turn(session, [Message(role="user", content="Recover")], "gemini-2.5-pro"))

    assert "--resume" in commands[0]
    assert "--resume" not in commands[1]
    assert result.backend_id == "fresh-gemini-session"
    assert result.output == "recovered"
    assert result.context_tokens == 3


def test_gemini_adapter_terminates_stalled_cli(monkeypatch, tmp_path):
    adapter = GeminiAdapter(stall_timeout_seconds=1)
    observed = {}

    async def fake_create_subprocess_exec(*command, **kwargs):
        proc = FakeStalledProc()
        observed["proc"] = proc
        return proc

    monkeypatch.setattr(gemini_adapter_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    now = datetime.now(timezone.utc)
    session = Session(
        client_session_id="client-gemini-stalled",
        backend_id="",
        provider=ProviderType.GEMINI,
        account_id="gemini-system",
        cwd_path=str(tmp_path),
        prefix_hash="prefix",
        status=SessionStatus.IDLE,
        fence_token=0,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=1),
        last_context_tokens=0,
    )

    with pytest.raises(RuntimeError, match="Gemini CLI stalled: no stdout/stderr output for 1s"):
        asyncio.run(adapter.send_turn(session, [Message(role="user", content="Recover")], "gemini-2.5-pro"))
    assert observed["proc"].terminated is True


def test_codex_adapter_parses_exec_output(tmp_path):
    adapter = CodexAdapter()
    output_path = tmp_path / "output.txt"
    output_path.write_text("Hello from file")
    stdout = """
    {"type": "thread.started", "thread_id": "thread-123"}
    {"type": "item.started", "item": {"type": "agent_message"}}
    {"type": "item.completed", "item": {"type": "agent_message", "text": "Hello from event"}}
    {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}}
    """
    backend_id, context_tokens, output = adapter._parse_exec_output(stdout, output_path, "")
    assert backend_id == "thread-123"
    assert context_tokens == 15
    assert output == "Hello from file"


def test_codex_adapter_send_turn_passes_provider_model(monkeypatch, tmp_path):
    adapter = CodexAdapter()
    captured = {}

    temp_home = tmp_path / "isolated"
    temp_home.mkdir()
    output_path = temp_home / "last-message.txt"
    output_path.write_text("hello from codex", encoding="utf-8")

    monkeypatch.setattr(
        adapter,
        "setup_isolated_env",
        lambda provider_name, account_id, session=None: (str(temp_home), dict(os.environ)),
    )
    monkeypatch.setattr(adapter, "cleanup_isolated_env", lambda temp_dir: None)

    class FakeProc:
        def __init__(self):
            self.returncode = 0

        async def communicate(self, input_data=None):
            captured["stdin"] = input_data
            payload = "\n".join(
                [
                    json.dumps({"type": "thread.started", "thread_id": "codex-thread-1"}),
                    json.dumps({"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 4}}),
                ]
            )
            return payload.encode(), b""

        def terminate(self):
            return None

        async def wait(self):
            return self.returncode

    async def fake_create_subprocess_exec(*command, **kwargs):
        captured["command"] = list(command)
        captured["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr(codex_adapter_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    now = datetime.now(timezone.utc)
    session = Session(
        client_session_id="client-codex-1",
        backend_id="",
        provider=ProviderType.CODEX,
        account_id="codex-account",
        cwd_path=str(tmp_path),
        prefix_hash="prefix",
        status=SessionStatus.IDLE,
        fence_token=0,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=1),
        last_context_tokens=0,
    )

    result = asyncio.run(adapter.send_turn(session, [Message(role="user", content="Say hi")], "gpt-5-codex"))

    assert captured["command"][:8] == [
        adapter._resolve_executable("codex"),
        "--search",
        "exec",
        "--skip-git-repo-check",
        "--dangerously-bypass-approvals-and-sandbox",
        "--json",
        "--model",
        "gpt-5-codex",
    ]
    assert "--full-auto" not in captured["command"]
    assert captured["command"][-1] == "-"
    assert captured["stdin"] == b"USER:\nSay hi"
    assert captured["kwargs"]["cwd"] == str(tmp_path)
    assert result.output == "hello from codex"
    assert result.backend_id == "codex-thread-1"
    assert result.context_tokens == 9


def test_codex_adapter_syncs_updated_isolated_auth_after_success(monkeypatch, tmp_path):
    db = DatabaseManager(str(tmp_path / "codara.db"))
    pool = AccountPool(db)
    pool.register_account(
        Account(
            account_id="codex-account",
            provider=ProviderType.CODEX,
            auth_type=AuthType.OAUTH_SESSION,
            label="Codex Account",
        ),
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {
                    "access_token": "old-access",
                    "refresh_token": "old-refresh",
                    "id_token": "old-id",
                },
            }
        ),
    )
    adapter = CodexAdapter(db)
    captured = {}

    temp_home = tmp_path / "isolated"
    auth_path = temp_home / ".codex" / "auth.json"
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {
                    "access_token": "new-access",
                    "refresh_token": "new-refresh",
                    "id_token": "new-id",
                },
            }
        ),
        encoding="utf-8",
    )
    output_path = temp_home / "last-message.txt"
    output_path.write_text("hello from codex", encoding="utf-8")

    monkeypatch.setattr(
        adapter,
        "setup_isolated_env",
        lambda provider_name, account_id, session=None: (str(temp_home), dict(os.environ)),
    )

    class FakeProc:
        returncode = 0

        async def communicate(self, input_data=None):
            captured["stdin"] = input_data
            payload = "\n".join(
                [
                    json.dumps({"type": "thread.started", "thread_id": "codex-thread-1"}),
                    json.dumps({"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 4}}),
                ]
            )
            return payload.encode(), b""

        def terminate(self):
            return None

        async def wait(self):
            return self.returncode

    async def fake_create_subprocess_exec(*command, **kwargs):
        return FakeProc()

    monkeypatch.setattr(codex_adapter_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    now = datetime.now(timezone.utc)
    session = Session(
        client_session_id="client-codex-sync",
        backend_id="",
        provider=ProviderType.CODEX,
        account_id="codex-account",
        cwd_path=str(tmp_path),
        prefix_hash="prefix",
        status=SessionStatus.IDLE,
        fence_token=0,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=1),
        last_context_tokens=0,
    )

    result = asyncio.run(adapter.send_turn(session, [Message(role="user", content="Say hi")], "gpt-5-codex"))
    stored = json.loads(pool.get_credential("codex-account"))

    assert result.output == "hello from codex"
    assert stored["tokens"]["access_token"] == "new-access"
    assert stored["tokens"]["refresh_token"] == "new-refresh"
    assert stored["tokens"]["id_token"] == "new-id"


def test_codex_adapter_does_not_sync_isolated_auth_after_failure(monkeypatch, tmp_path):
    db = DatabaseManager(str(tmp_path / "codara.db"))
    pool = AccountPool(db)
    original = json.dumps(
        {
            "auth_mode": "chatgpt",
            "tokens": {
                "access_token": "old-access",
                "refresh_token": "old-refresh",
                "id_token": "old-id",
            },
        }
    )
    pool.register_account(
        Account(
            account_id="codex-account",
            provider=ProviderType.CODEX,
            auth_type=AuthType.OAUTH_SESSION,
            label="Codex Account",
        ),
        original,
    )
    adapter = CodexAdapter(db)

    temp_home = tmp_path / "isolated"
    auth_path = temp_home / ".codex" / "auth.json"
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {
                    "access_token": "new-access",
                    "refresh_token": "new-refresh",
                    "id_token": "new-id",
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        adapter,
        "setup_isolated_env",
        lambda provider_name, account_id, session=None: (str(temp_home), dict(os.environ)),
    )

    class FakeProc:
        returncode = 1

        async def communicate(self, input_data=None):
            return b"", b"auth failed"

        def terminate(self):
            return None

        async def wait(self):
            return self.returncode

    async def fake_create_subprocess_exec(*command, **kwargs):
        return FakeProc()

    monkeypatch.setattr(codex_adapter_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    now = datetime.now(timezone.utc)
    session = Session(
        client_session_id="client-codex-fail",
        backend_id="",
        provider=ProviderType.CODEX,
        account_id="codex-account",
        cwd_path=str(tmp_path),
        prefix_hash="prefix",
        status=SessionStatus.IDLE,
        fence_token=0,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=1),
        last_context_tokens=0,
    )

    with pytest.raises(RuntimeError, match="auth failed"):
        asyncio.run(adapter.send_turn(session, [Message(role="user", content="Say hi")], "gpt-5-codex"))

    stored = json.loads(pool.get_credential("codex-account"))
    assert stored["tokens"]["access_token"] == "old-access"
    assert stored["tokens"]["refresh_token"] == "old-refresh"
    assert stored["tokens"]["id_token"] == "old-id"


def test_codex_adapter_terminates_stalled_cli(monkeypatch, tmp_path):
    adapter = CodexAdapter(stall_timeout_seconds=1)
    observed = {}
    temp_home = tmp_path / "isolated"
    temp_home.mkdir()

    monkeypatch.setattr(
        adapter,
        "setup_isolated_env",
        lambda provider_name, account_id, session=None: (str(temp_home), dict(os.environ)),
    )

    async def fake_create_subprocess_exec(*command, **kwargs):
        proc = FakeStalledProc()
        observed["proc"] = proc
        return proc

    monkeypatch.setattr(codex_adapter_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    now = datetime.now(timezone.utc)
    session = Session(
        client_session_id="client-codex-stalled",
        backend_id="",
        provider=ProviderType.CODEX,
        account_id="codex-account",
        cwd_path=str(tmp_path),
        prefix_hash="prefix",
        status=SessionStatus.IDLE,
        fence_token=0,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=1),
        last_context_tokens=0,
    )

    with pytest.raises(RuntimeError, match="Codex CLI stalled: no stdout/stderr output for 1s"):
        asyncio.run(adapter.send_turn(session, [Message(role="user", content="Recover")], "gpt-5-codex"))
    assert observed["proc"].terminated is True


def test_setup_isolated_env_reuses_account_scoped_home_across_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("UAG_ISOLATED_ENVS_ROOT", str(tmp_path / "shared-isolated-envs"))
    get_settings(force_reload=True)

    adapter = CodexAdapter()
    now = datetime.now(timezone.utc)
    session_one = Session(
        client_session_id="client-codex-workspace-a",
        backend_id="",
        provider=ProviderType.CODEX,
        account_id="codex-account",
        cwd_path=str(tmp_path),
        prefix_hash="prefix",
        status=SessionStatus.IDLE,
        fence_token=0,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=1),
        last_context_tokens=0,
    )
    session_two = Session(
        client_session_id="client-codex-workspace-b",
        backend_id="",
        provider=ProviderType.CODEX,
        account_id="codex-account",
        cwd_path=str(tmp_path / "other-workspace"),
        prefix_hash="prefix",
        status=SessionStatus.IDLE,
        fence_token=0,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=1),
        last_context_tokens=0,
    )
    Path(session_two.cwd_path).mkdir(parents=True, exist_ok=True)

    isolated_home_one, env_one = adapter.setup_isolated_env("codex", "codex-account", session=session_one)
    isolated_home_two, env_two = adapter.setup_isolated_env("codex", "codex-account", session=session_two)

    expected_root = (tmp_path / "shared-isolated-envs" / "codex").resolve()
    assert isolated_home_one.startswith(str(expected_root))
    assert isolated_home_one == isolated_home_two
    assert env_one["HOME"] == isolated_home_one
    assert env_two["HOME"] == isolated_home_two
    assert Path(isolated_home_one).exists()


def test_setup_isolated_env_rejects_incomplete_codex_oauth_credential(tmp_path):
    db = DatabaseManager(str(tmp_path / "codara.db"))
    adapter = CodexAdapter(db)
    account = Account(
        account_id="codex-oauth",
        provider=ProviderType.CODEX,
        auth_type=AuthType.OAUTH_SESSION,
        label="Codex OAuth",
    )
    adapter.pool.register_account(
        account,
        json.dumps({"auth_mode": "chatgpt", "tokens": {"access_token": "access-only"}}),
    )

    with pytest.raises(RuntimeError, match="missing id_token"):
        adapter.setup_isolated_env("codex", "codex-oauth")


def test_codex_adapter_prefers_stdout_json_error_when_stderr_empty():
    adapter = CodexAdapter()

    detail = adapter._extract_exec_error(
        "\n".join(
            [
                json.dumps({"type": "error", "message": "Codex auth failed"}),
                json.dumps({"type": "turn.error", "error": {"message": "missing field `id_token`"}}),
            ]
        ),
        "",
    )

    assert "Codex auth failed" in detail
    assert "missing field `id_token`" in detail


def test_opencode_model_listing_parses_cli_output(monkeypatch):
    adapter = OpenCodeAdapter()

    class FakeProc:
        def __init__(self):
            self.returncode = 0

        async def communicate(self):
            return (b"opencode/big-pickle\nanthropic/claude-sonnet-4.5\n", b"")

    async def fake_create_subprocess_exec(*command, **kwargs):
        return FakeProc()

    monkeypatch.setattr(opencode_adapter_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    payload = asyncio.run(adapter.list_models(type("SettingsRef", (), {"opencode_default_model": "opencode/big-pickle"})()))

    assert payload["provider"] == "opencode"
    assert payload["status"] == "ok"
    assert payload["models"][:2] == ["opencode/big-pickle", "anthropic/claude-sonnet-4.5"]


def test_opencode_adapter_send_turn_uses_local_cli(monkeypatch, tmp_path):
    adapter = OpenCodeAdapter()
    captured = {}

    class FakeProc:
        def __init__(self):
            self.returncode = 0

        async def communicate(self):
            payload = "\n".join(
                [
                    json.dumps({"type": "session.created", "session_id": "oc-session-1"}),
                    json.dumps({"type": "message.delta", "text": "hello "}),
                    json.dumps({"type": "message.completed", "text": "hello from opencode"}),
                    json.dumps({"type": "turn.done", "usage": {"input_tokens": 7, "output_tokens": 3}}),
                ]
            )
            return payload.encode(), b""

        def terminate(self):
            return None

        async def wait(self):
            return self.returncode

    async def fake_create_subprocess_exec(*command, **kwargs):
        captured["command"] = list(command)
        captured["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr(opencode_adapter_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    now = datetime.now(timezone.utc)
    session = Session(
        client_session_id="client-opencode-1",
        backend_id="existing-oc-session",
        provider=ProviderType.OPENCODE,
        account_id="opencode-system",
        cwd_path=str(tmp_path),
        prefix_hash="prefix",
        status=SessionStatus.IDLE,
        fence_token=0,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=1),
        last_context_tokens=0,
    )

    result = asyncio.run(adapter.send_turn(session, [Message(role="user", content="Say hi")], "anthropic/claude-sonnet-4.5"))

    assert captured["command"][:3] == [adapter._resolve_executable("opencode"), "run", "--format"]
    assert "--model" in captured["command"]
    assert "anthropic/claude-sonnet-4.5" in captured["command"]
    assert "--session" in captured["command"]
    assert "existing-oc-session" in captured["command"]
    assert "--dir" in captured["command"]
    assert "--dangerously-skip-permissions" in captured["command"]
    assert captured["kwargs"]["cwd"] == str(tmp_path)
    assert captured["kwargs"]["env"]["HOME"] == os.environ["HOME"]
    assert result.output == "hello from opencode"
    assert result.backend_id == "oc-session-1"
    assert result.context_tokens == 10


def test_opencode_adapter_surfaces_json_error_events(monkeypatch, tmp_path):
    adapter = OpenCodeAdapter()

    class FakeProc:
        def __init__(self):
            self.returncode = 0

        async def communicate(self):
            payload = json.dumps(
                {
                    "type": "error",
                    "sessionID": "ses_error_1",
                    "error": {
                        "name": "UnknownError",
                        "data": {"message": "Model not found: openai/gpt-5."},
                    },
                }
            )
            return payload.encode(), b""

        def terminate(self):
            return None

        async def wait(self):
            return self.returncode

    async def fake_create_subprocess_exec(*command, **kwargs):
        return FakeProc()

    monkeypatch.setattr(opencode_adapter_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    now = datetime.now(timezone.utc)
    session = Session(
        client_session_id="client-opencode-error",
        backend_id="",
        provider=ProviderType.OPENCODE,
        account_id="opencode-system",
        cwd_path=str(tmp_path),
        prefix_hash="prefix",
        status=SessionStatus.IDLE,
        fence_token=0,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=1),
        last_context_tokens=0,
    )

    with pytest.raises(RuntimeError, match="OpenCode CLI error: Model not found: openai/gpt-5\\."):
        asyncio.run(adapter.send_turn(session, [Message(role="user", content="Recover")], "openai/gpt-5"))


def test_opencode_adapter_terminates_stalled_cli(monkeypatch, tmp_path):
    adapter = OpenCodeAdapter(stall_timeout_seconds=1)
    observed = {}

    async def fake_create_subprocess_exec(*command, **kwargs):
        proc = FakeStalledProc()
        observed["proc"] = proc
        return proc

    monkeypatch.setattr(opencode_adapter_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    now = datetime.now(timezone.utc)
    session = Session(
        client_session_id="client-opencode-stalled",
        backend_id="",
        provider=ProviderType.OPENCODE,
        account_id="opencode-system",
        cwd_path=str(tmp_path),
        prefix_hash="prefix",
        status=SessionStatus.IDLE,
        fence_token=0,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=1),
        last_context_tokens=0,
    )

    with pytest.raises(RuntimeError, match="OpenCode CLI stalled: no stdout/stderr output for 1s"):
        asyncio.run(adapter.send_turn(session, [Message(role="user", content="Recover")], "opencode/big-pickle"))
    assert observed["proc"].terminated is True


def test_opencode_adapter_retries_without_stale_session_and_parses_nested_assistant_message(monkeypatch, tmp_path):
    adapter = OpenCodeAdapter()
    commands: list[list[str]] = []

    class FakeProc:
        def __init__(self, returncode, stdout, stderr):
            self.returncode = returncode
            self._stdout = stdout
            self._stderr = stderr

        async def communicate(self):
            return self._stdout, self._stderr

        def terminate(self):
            return None

        async def wait(self):
            return self.returncode

    responses = [
        FakeProc(1, b"", b"session not found: stale-opencode-session"),
        FakeProc(
            0,
            "\n".join(
                [
                    json.dumps({"type": "session.created", "session_id": "fresh-oc-session"}),
                    json.dumps(
                        {
                            "type": "assistant.message",
                            "message": {
                                "role": "assistant",
                                "content": [{"type": "text", "text": "hello from nested payload"}],
                            },
                        }
                    ),
                ]
            ).encode(),
            b"",
        ),
    ]

    async def fake_create_subprocess_exec(*command, **kwargs):
        commands.append(list(command))
        return responses.pop(0)

    monkeypatch.setattr(opencode_adapter_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    now = datetime.now(timezone.utc)
    session = Session(
        client_session_id="client-opencode-retry",
        backend_id="stale-opencode-session",
        provider=ProviderType.OPENCODE,
        account_id="opencode-system",
        cwd_path=str(tmp_path),
        prefix_hash="prefix",
        status=SessionStatus.IDLE,
        fence_token=0,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=1),
        last_context_tokens=0,
    )

    result = asyncio.run(adapter.send_turn(session, [Message(role="user", content="Recover")], "opencode/big-pickle"))

    assert "--session" in commands[0]
    assert "--session" not in commands[1]
    assert result.backend_id == "fresh-oc-session"
    assert result.output == "hello from nested payload"


def test_opencode_adapter_parses_real_text_and_step_finish_events(monkeypatch, tmp_path):
    adapter = OpenCodeAdapter()

    class FakeProc:
        def __init__(self):
            self.returncode = 0

        async def communicate(self):
            payload = "\n".join(
                [
                    json.dumps(
                        {
                            "type": "step_start",
                            "sessionID": "ses_real_1",
                            "part": {"type": "step-start"},
                        }
                    ),
                    json.dumps(
                        {
                            "type": "text",
                            "sessionID": "ses_real_1",
                            "part": {"type": "text", "text": "hello from opencode test"},
                        }
                    ),
                    json.dumps(
                        {
                            "type": "step_finish",
                            "sessionID": "ses_real_1",
                            "part": {
                                "type": "step-finish",
                                "tokens": {"input": 17022, "output": 2, "reasoning": 44},
                            },
                        }
                    ),
                ]
            )
            return payload.encode(), b""

        def terminate(self):
            return None

        async def wait(self):
            return self.returncode

    async def fake_create_subprocess_exec(*command, **kwargs):
        return FakeProc()

    monkeypatch.setattr(opencode_adapter_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    now = datetime.now(timezone.utc)
    session = Session(
        client_session_id="client-opencode-real",
        backend_id="",
        provider=ProviderType.OPENCODE,
        account_id="opencode-system",
        cwd_path=str(tmp_path),
        prefix_hash="prefix",
        status=SessionStatus.IDLE,
        fence_token=0,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=1),
        last_context_tokens=0,
    )

    result = asyncio.run(adapter.send_turn(session, [Message(role="user", content="Recover")], "opencode/nemotron-3-super-free"))

    assert result.backend_id == "ses_real_1"
    assert result.output == "hello from opencode test"
    assert result.context_tokens == 17024
