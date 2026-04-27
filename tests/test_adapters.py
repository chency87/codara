import os
import asyncio
import json
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import amesh.adapters.base as base_adapter_module
import amesh.adapters.codex as codex_adapter_module
from amesh.adapters.codex import CodexAdapter
from amesh.adapters.gemini import GeminiAdapter
from amesh.adapters.opencode import OpenCodeAdapter
import amesh.adapters.gemini as gemini_adapter_module
import amesh.adapters.opencode as opencode_adapter_module
from amesh.config import get_settings
from amesh.core.models import Message, Session, TurnResult, ProviderType, SessionStatus
from amesh.database.manager import DatabaseManager

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
        session_id="client-1",
        workspace_id="default",
        user_id="default-user",
        client_session_id="client-1",
        backend_id="existing-gemini-session",
        provider=ProviderType.GEMINI,
        cwd_path=str(tmp_path),
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=1),
    )

    result = asyncio.run(
        adapter.send_turn(
            session,
            [Message(role="system", content="You are helpful."), Message(role="user", content="Say hi")],
            "gemini-2.5-flash",
        )
    )

    assert captured["command"] == ["gemini", "exec", "--model", "gemini-2.5-flash", "--yolo", "--resume", "existing-gemini-session"]
    assert captured["stdin"] == b"Say hi"
    assert captured["kwargs"]["cwd"] == str(tmp_path)
    assert result.output == "done from gemini"
    assert result.backend_id == "gem-session-456"

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
        session_id="client-2",
        workspace_id="default",
        user_id="default-user",
        client_session_id="client-2",
        backend_id="",
        provider=ProviderType.GEMINI,
        cwd_path=str(tmp_path),
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=1),
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
        session_id="client-gemini-retry",
        workspace_id="default",
        user_id="default-user",
        client_session_id="client-gemini-retry",
        backend_id="stale-gemini-session",
        provider=ProviderType.GEMINI,
        cwd_path=str(tmp_path),
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=1),
    )

    result = asyncio.run(adapter.send_turn(session, [Message(role="user", content="Recover")], "gemini-2.5-pro"))

    assert "--resume" in commands[0]
    assert "--resume" not in commands[1]
    assert result.backend_id == "fresh-gemini-session"
    assert result.output == "recovered"

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
        session_id="client-gemini-stalled",
        workspace_id="default",
        user_id="default-user",
        client_session_id="client-gemini-stalled",
        backend_id="",
        provider=ProviderType.GEMINI,
        cwd_path=str(tmp_path),
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=1),
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
    {"type": "turn.completed"}
    """
    backend_id, output = adapter._parse_exec_output(stdout, output_path, "")
    assert backend_id == "thread-123"
    assert output == "Hello from file"


def test_codex_adapter_send_turn_passes_provider_model(monkeypatch, tmp_path):
    adapter = CodexAdapter()
    captured = {}

    output_path = Path(tempfile.gettempdir()) / "uag-codex-client-codex-1.txt"
    output_path.write_text("hello from codex", encoding="utf-8")

    class FakeProc:
        def __init__(self):
            self.returncode = 0

        async def communicate(self, input_data=None):
            captured["stdin"] = input_data
            payload = "\n".join(
                [
                    json.dumps({"type": "thread.started", "thread_id": "codex-thread-1"}),
                    json.dumps({"type": "turn.completed"}),
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
        session_id="client-codex-1",
        workspace_id="default",
        user_id="default-user",
        client_session_id="client-codex-1",
        backend_id="",
        provider=ProviderType.CODEX,
        cwd_path=str(tmp_path),
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=1),
    )

    try:
        result = asyncio.run(adapter.send_turn(session, [Message(role="user", content="Say hi")], "gpt-5-codex"))
    finally:
        if output_path.exists():
            output_path.unlink()

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
    assert "-o" in captured["command"]
    assert str(output_path) in captured["command"]
    assert "-C" in captured["command"]
    assert str(tmp_path) in captured["command"]
    assert captured["stdin"] == b"USER:\nSay hi"
    assert result.output == "hello from codex"
    assert result.backend_id == "codex-thread-1"

def test_codex_adapter_terminates_stalled_cli(monkeypatch, tmp_path):
    adapter = CodexAdapter(stall_timeout_seconds=1)
    observed = {}

    async def fake_create_subprocess_exec(*command, **kwargs):
        proc = FakeStalledProc()
        observed["proc"] = proc
        return proc

    monkeypatch.setattr(codex_adapter_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    now = datetime.now(timezone.utc)
    session = Session(
        session_id="client-codex-stalled",
        workspace_id="default",
        user_id="default-user",
        client_session_id="client-codex-stalled",
        backend_id="",
        provider=ProviderType.CODEX,
        cwd_path=str(tmp_path),
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=1),
    )

    with pytest.raises(RuntimeError, match="Codex CLI stalled: no stdout/stderr output for 1s"):
        asyncio.run(adapter.send_turn(session, [Message(role="user", content="Recover")], "gpt-5-codex"))
    assert observed["proc"].terminated is True


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
                    json.dumps({"type": "turn.done"}),
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
        session_id="client-opencode-1",
        workspace_id="default",
        user_id="default-user",
        client_session_id="client-opencode-1",
        backend_id="existing-oc-session",
        provider=ProviderType.OPENCODE,
        cwd_path=str(tmp_path),
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=1),
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
    assert result.output == "hello from opencode"
    assert result.backend_id == "oc-session-1"


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
        session_id="client-opencode-error",
        workspace_id="default",
        user_id="default-user",
        client_session_id="client-opencode-error",
        backend_id="",
        provider=ProviderType.OPENCODE,
        cwd_path=str(tmp_path),
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=1),
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
        session_id="client-opencode-stalled",
        workspace_id="default",
        user_id="default-user",
        client_session_id="client-opencode-stalled",
        backend_id="",
        provider=ProviderType.OPENCODE,
        cwd_path=str(tmp_path),
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=1),
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
        session_id="client-opencode-retry",
        workspace_id="default",
        user_id="default-user",
        client_session_id="client-opencode-retry",
        backend_id="stale-opencode-session",
        provider=ProviderType.OPENCODE,
        cwd_path=str(tmp_path),
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=1),
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
        session_id="client-opencode-real",
        workspace_id="default",
        user_id="default-user",
        client_session_id="client-opencode-real",
        backend_id="",
        provider=ProviderType.OPENCODE,
        cwd_path=str(tmp_path),
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=1),
    )

    result = asyncio.run(adapter.send_turn(session, [Message(role="user", content="Recover")], "opencode/nemotron-3-super-free"))

    assert result.backend_id == "ses_real_1"
    assert result.output == "hello from opencode test"
