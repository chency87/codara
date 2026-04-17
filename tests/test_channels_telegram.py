import asyncio
import io
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError

from fastapi.testclient import TestClient
import pytest

import codara.gateway.app as gateway_app
from codara.channels.service import ChannelService, ChannelTurnResult
from codara.channels.telegram import (
    DEFAULT_TELEGRAM_COMMANDS,
    TELEGRAM_TEXT_CHUNK_LIMIT,
    TelegramApiError,
    TelegramChannelAdapter,
    TelegramPollingManager,
)
from codara.config import ChannelsSettings
from codara.core.models import Account, AccountStatus, AuthType, ProviderType, Session, SessionStatus, TurnResult
from codara.database.manager import DatabaseManager
from codara.orchestrator.engine import Orchestrator
from tests.helpers import operator_headers


def _setup_app(tmp_path, monkeypatch, *, receive_mode="webhook"):
    db_path = tmp_path / "codara.db"
    workspaces_root = tmp_path / "workspaces"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.settings.workspaces_root = str(workspaces_root)
    gateway_app.settings.isolated_envs_root = str(workspaces_root / "isolated_envs")
    gateway_app.settings.channels = ChannelsSettings.model_validate(
        {
            "telegram": {
                "enabled": True,
                "receive_mode": receive_mode,
                "mention_only": False,
                "bots": [
                    {
                        "name": "engineering-bot",
                        "enabled": True,
                        "token": "telegram-test-token",
                        "webhook_secret": "telegram-secret",
                        "username": "engineering_bot",
                    },
                    {
                        "name": "ops-bot",
                        "enabled": True,
                        "token": "telegram-test-token-2",
                        "webhook_secret": "telegram-secret-2",
                        "username": "ops_bot",
                    }
                ],
            }
        }
    )
    gateway_app.db_manager = DatabaseManager(str(db_path))
    gateway_app.orchestrator = Orchestrator(gateway_app.db_manager)
    return TestClient(gateway_app.app)


def _create_user(client: TestClient):
    headers = operator_headers(client)
    resp = client.post(
        "/management/v1/users",
        headers=headers,
        json={
            "email": "telegram-user@example.com",
            "display_name": "Telegram User",
            "key_label": "primary",
            "max_concurrency": 2,
        },
    )
    assert resp.status_code == 200
    return headers, resp.json()["data"]


def test_management_can_create_channel_link_token(tmp_path, monkeypatch):
    client = _setup_app(tmp_path, monkeypatch)
    headers, created = _create_user(client)

    resp = client.post(
        f"/management/v1/users/{created['user_id']}/channels/link-token",
        headers=headers,
        json={"channel": "telegram", "bot_name": "engineering-bot", "expires_in_minutes": 15},
    )

    assert resp.status_code == 200
    payload = resp.json()["data"]
    assert payload["channel"] == "telegram"
    assert payload["bot_name"] == "engineering-bot"
    assert payload["user_id"] == created["user_id"]
    assert payload["raw_token"]


def test_database_persists_channel_polling_offset(tmp_path, monkeypatch):
    client = _setup_app(tmp_path, monkeypatch)
    gateway_app.db_manager.save_channel_polling_offset("telegram", "engineering-bot", 123)

    assert gateway_app.db_manager.get_channel_polling_offset("telegram", "engineering-bot") == 123
    assert gateway_app.db_manager.get_channel_polling_offset("telegram", "ops-bot") == 0


def test_telegram_reaction_ack_posts_expected_payload(tmp_path, monkeypatch):
    client = _setup_app(tmp_path, monkeypatch)
    adapter = gateway_app._telegram_adapter("engineering-bot")
    observed = {}

    def fake_post_json(self, method, payload):
        observed["method"] = method
        observed["payload"] = payload
        return {"ok": True}

    monkeypatch.setattr(TelegramChannelAdapter, "_post_json", fake_post_json)

    adapter.set_message_reaction("1001", 77)

    assert observed["method"] == "setMessageReaction"
    assert observed["payload"]["chat_id"] == "1001"
    assert observed["payload"]["message_id"] == 77
    assert observed["payload"]["reaction"] == [{"type": "emoji", "emoji": "👀"}]


def test_telegram_get_updates_uses_socket_timeout_larger_than_long_poll(tmp_path, monkeypatch):
    _setup_app(tmp_path, monkeypatch)
    adapter = gateway_app._telegram_adapter("engineering-bot")
    observed = {}

    def fake_get(self, method, query, timeout=15):
        observed["method"] = method
        observed["query"] = query
        observed["timeout"] = timeout
        return {"ok": True, "result": []}

    monkeypatch.setattr(TelegramChannelAdapter, "_telegram_get", fake_get)

    adapter.get_updates(offset=12, timeout=20)

    assert observed["method"] == "getUpdates"
    assert observed["query"]["offset"] == "12"
    assert observed["query"]["timeout"] == "20"
    assert observed["timeout"] >= 30


def test_telegram_set_my_commands_posts_expected_payload(tmp_path, monkeypatch):
    _setup_app(tmp_path, monkeypatch)
    adapter = gateway_app._telegram_adapter("engineering-bot")
    observed = {}

    def fake_post_json(self, method, payload):
        observed["method"] = method
        observed["payload"] = payload
        return {"ok": True}

    monkeypatch.setattr(TelegramChannelAdapter, "_post_json", fake_post_json)

    adapter.set_my_commands()

    assert observed["method"] == "setMyCommands"
    assert observed["payload"]["commands"] == DEFAULT_TELEGRAM_COMMANDS
    commands = [item["command"] for item in observed["payload"]["commands"]]
    assert "start" in commands
    assert "help" in commands
    assert "commands" in commands
    assert "whoami" in commands


def test_telegram_post_json_includes_api_error_details(tmp_path, monkeypatch):
    _setup_app(tmp_path, monkeypatch)
    adapter = gateway_app._telegram_adapter("engineering-bot")

    def fake_urlopen(req, timeout=15):
        body = b'{"ok":false,"error_code":400,"description":"Bad Request: message is too long"}'
        raise HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(body))

    monkeypatch.setattr("codara.channels.telegram.request.urlopen", fake_urlopen)

    with pytest.raises(TelegramApiError) as exc_info:
        adapter._post_json("sendMessage", {"chat_id": "1001", "text": "hello"})

    assert exc_info.value.method == "sendMessage"
    assert exc_info.value.error_code == 400
    assert "message is too long" in str(exc_info.value)


def test_telegram_send_text_splits_long_messages(tmp_path, monkeypatch):
    _setup_app(tmp_path, monkeypatch)
    adapter = gateway_app._telegram_adapter("engineering-bot")
    calls = []

    def fake_post_json(self, method, payload):
        calls.append((method, payload))
        return {"ok": True}

    monkeypatch.setattr(TelegramChannelAdapter, "_post_json", fake_post_json)

    adapter.send_text("1001", "a" * (TELEGRAM_TEXT_CHUNK_LIMIT + 25))

    assert [method for method, _payload in calls] == ["sendMessage", "sendMessage"]
    assert all(len(payload["text"]) <= TELEGRAM_TEXT_CHUNK_LIMIT for _method, payload in calls)
    assert "".join(payload["text"] for _method, payload in calls) == "a" * (TELEGRAM_TEXT_CHUNK_LIMIT + 25)


def test_telegram_turn_reply_reports_no_detected_workspace_changes(tmp_path, monkeypatch):
    _setup_app(tmp_path, monkeypatch)
    adapter = gateway_app._telegram_adapter("engineering-bot")

    reply = adapter._render_turn_reply(
        ChannelTurnResult(
            text="I inspected the project.",
            workspace_id="project-a",
            provider="codex",
            client_session_id="session-a",
            attachments=[],
            modified_files=[],
            diff=None,
        )
    )

    assert "I inspected the project." in reply
    assert "No workspace file changes were detected" in reply


def test_telegram_acknowledge_falls_back_to_chat_action(tmp_path, monkeypatch):
    client = _setup_app(tmp_path, monkeypatch)
    adapter = gateway_app._telegram_adapter("engineering-bot")
    calls = []

    def fake_reaction(self, chat_id, message_id, emoji="👀", is_big=False):
        raise URLError("reactions unavailable")

    def fake_chat_action(self, chat_id, action="typing", thread_id=None):
        calls.append((chat_id, action, thread_id))

    monkeypatch.setattr(TelegramChannelAdapter, "set_message_reaction", fake_reaction)
    monkeypatch.setattr(TelegramChannelAdapter, "send_chat_action", fake_chat_action)

    adapter.acknowledge_inbound_message({"message_id": 55}, chat_id="1001", thread_id="42")

    assert calls == [("1001", "typing", "42")]


def test_telegram_webhook_links_user_and_updates_conversation_state(tmp_path, monkeypatch):
    client = _setup_app(tmp_path, monkeypatch)
    sent = []
    monkeypatch.setattr(TelegramChannelAdapter, "send_text", lambda self, chat_id, text, thread_id=None: sent.append((chat_id, text, thread_id)))

    headers, created = _create_user(client)
    token_resp = client.post(
        f"/management/v1/users/{created['user_id']}/channels/link-token",
        headers=headers,
        json={"channel": "telegram", "bot_name": "engineering-bot"},
    )
    raw_token = token_resp.json()["data"]["raw_token"]

    link_resp = client.post(
        "/channels/telegram/engineering-bot/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        json={
            "message": {
                "chat": {"id": 1001},
                "from": {"id": 2002},
                "text": f"/link {raw_token}",
            }
        },
    )
    assert link_resp.status_code == 200
    link = gateway_app.db_manager.get_channel_user_link("telegram", "engineering-bot", "2002")
    assert link is not None
    assert link["user_id"] == created["user_id"]

    workspace_resp = client.post(
        "/channels/telegram/engineering-bot/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        json={
            "message": {
                "chat": {"id": 1001},
                "from": {"id": 2002},
                "text": "/workspace project-a",
            }
        },
    )
    assert workspace_resp.status_code == 200
    conversation = gateway_app.db_manager.get_channel_conversation("telegram", "engineering-bot", "telegram:engineering-bot:1001:0")
    assert conversation is not None
    assert conversation["workspace_id"] == "project-a"
    assert sent[-1][1] == "Workspace set to project-a."


def test_telegram_project_commands_create_list_info_and_select(tmp_path, monkeypatch):
    client = _setup_app(tmp_path, monkeypatch)
    sent = []
    monkeypatch.setattr(TelegramChannelAdapter, "send_text", lambda self, chat_id, text, thread_id=None: sent.append((chat_id, text, thread_id)))

    headers, created = _create_user(client)
    token_resp = client.post(
        f"/management/v1/users/{created['user_id']}/channels/link-token",
        headers=headers,
        json={"channel": "telegram", "bot_name": "engineering-bot"},
    )
    raw_token = token_resp.json()["data"]["raw_token"]

    client.post(
        "/channels/telegram/engineering-bot/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        json={"message": {"chat": {"id": 1001}, "from": {"id": 2002}, "text": f"/link {raw_token}"}},
    )

    create_resp = client.post(
        "/channels/telegram/engineering-bot/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        json={"message": {"chat": {"id": 1001}, "from": {"id": 2002}, "text": "/project_create news-pulse python"}},
    )
    assert create_resp.status_code == 200
    assert "Project created: news-pulse" in sent[-1][1]
    assert "Workspace set to news-pulse." in sent[-1][1]

    user_workspace = Path(created["workspace_path"])
    assert (user_workspace / "news-pulse" / ".codara" / "project.toml").exists()
    assert (user_workspace / "news-pulse" / "src" / "news_pulse" / "__init__.py").exists()

    list_resp = client.post(
        "/channels/telegram/engineering-bot/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        json={"message": {"chat": {"id": 1001}, "from": {"id": 2002}, "text": "/projects"}},
    )
    assert list_resp.status_code == 200
    assert "news-pulse" in sent[-1][1]
    assert "python" in sent[-1][1]

    info_resp = client.post(
        "/channels/telegram/engineering-bot/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        json={"message": {"chat": {"id": 1001}, "from": {"id": 2002}, "text": "/project_info news-pulse"}},
    )
    assert info_resp.status_code == 200
    assert "Project: news-pulse" in sent[-1][1]
    assert "Template: python" in sent[-1][1]

    select_resp = client.post(
        "/channels/telegram/engineering-bot/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        json={"message": {"chat": {"id": 1001}, "from": {"id": 2002}, "text": "/project news-pulse"}},
    )
    assert select_resp.status_code == 200
    conversation = gateway_app.db_manager.get_channel_conversation("telegram", "engineering-bot", "telegram:engineering-bot:1001:0")
    assert conversation["workspace_id"] == "news-pulse"
    assert sent[-1][1] == "Project set to news-pulse."


def test_telegram_help_and_commands_explain_usage(tmp_path, monkeypatch):
    client = _setup_app(tmp_path, monkeypatch)
    sent = []
    monkeypatch.setattr(TelegramChannelAdapter, "send_text", lambda self, chat_id, text, thread_id=None: sent.append((chat_id, text, thread_id)))

    help_resp = client.post(
        "/channels/telegram/engineering-bot/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        json={"message": {"chat": {"id": 1001}, "from": {"id": 2002}, "text": "/help"}},
    )
    assert help_resp.status_code == 200
    assert "How to use it:" in sent[-1][1]
    assert "/link <token>" in sent[-1][1]

    commands_resp = client.post(
        "/channels/telegram/engineering-bot/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        json={"message": {"chat": {"id": 1001}, "from": {"id": 2002}, "text": "/commands"}},
    )
    assert commands_resp.status_code == 200
    assert "Available commands:" in sent[-1][1]
    assert "/whoami" in sent[-1][1]
    assert "/project_create" in sent[-1][1]


def test_telegram_whoami_reports_linked_identity(tmp_path, monkeypatch):
    client = _setup_app(tmp_path, monkeypatch)
    sent = []
    monkeypatch.setattr(TelegramChannelAdapter, "send_text", lambda self, chat_id, text, thread_id=None: sent.append((chat_id, text, thread_id)))

    headers, created = _create_user(client)
    token_resp = client.post(
        f"/management/v1/users/{created['user_id']}/channels/link-token",
        headers=headers,
        json={"channel": "telegram", "bot_name": "engineering-bot"},
    )
    raw_token = token_resp.json()["data"]["raw_token"]

    client.post(
        "/channels/telegram/engineering-bot/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        json={"message": {"chat": {"id": 1001}, "from": {"id": 2002}, "text": f"/link {raw_token}"}},
    )
    client.post(
        "/channels/telegram/engineering-bot/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        json={"message": {"chat": {"id": 1001}, "from": {"id": 2002}, "text": "/workspace project-a"}},
    )

    whoami_resp = client.post(
        "/channels/telegram/engineering-bot/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        json={"message": {"chat": {"id": 1001}, "from": {"id": 2002}, "text": "/whoami"}},
    )

    assert whoami_resp.status_code == 200
    assert f"User ID: {created['user_id']}" in sent[-1][1]
    assert "Workspace: project-a" in sent[-1][1]
    assert "Provider: codex" in sent[-1][1]


def test_telegram_session_command_reports_runtime_session_status(tmp_path, monkeypatch):
    client = _setup_app(tmp_path, monkeypatch)
    sent = []
    monkeypatch.setattr(TelegramChannelAdapter, "send_text", lambda self, chat_id, text, thread_id=None: sent.append((chat_id, text, thread_id)))

    headers, created = _create_user(client)
    token_resp = client.post(
        f"/management/v1/users/{created['user_id']}/channels/link-token",
        headers=headers,
        json={"channel": "telegram", "bot_name": "engineering-bot"},
    )
    raw_token = token_resp.json()["data"]["raw_token"]

    client.post(
        "/channels/telegram/engineering-bot/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        json={"message": {"chat": {"id": 1001}, "from": {"id": 2002}, "text": f"/link {raw_token}"}},
    )
    client.post(
        "/channels/telegram/engineering-bot/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        json={"message": {"chat": {"id": 1001}, "from": {"id": 2002}, "text": "/workspace project-a"}},
    )

    client_session_id = f"{created['user_id']}::project-a::telegram:engineering-bot:1001:0"
    now = datetime.now(timezone.utc)
    gateway_app.db_manager.save_account(
        Account(
            account_id="codex-account",
            provider=ProviderType.CODEX,
            auth_type=AuthType.API_KEY,
            label="Codex Account",
            status=AccountStatus.READY.value,
        )
    )
    gateway_app.db_manager.save_session(
        Session(
            client_session_id=client_session_id,
            backend_id="provider-session-1",
            provider=ProviderType.CODEX,
            account_id="codex-account",
            user_id=created["user_id"],
            api_key_id=None,
            cwd_path=str(Path(created["workspace_path"]) / "project-a"),
            prefix_hash="prefix",
            status=SessionStatus.ACTIVE,
            fence_token=0,
            last_context_tokens=123,
            created_at=now,
            updated_at=now,
            expires_at=now,
        )
    )

    resp = client.post(
        "/channels/telegram/engineering-bot/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        json={"message": {"chat": {"id": 1001}, "from": {"id": 2002}, "text": "/session"}},
    )

    assert resp.status_code == 200
    assert "Runtime:" in sent[-1][1]
    assert "Status: active" in sent[-1][1]
    assert f"Client session: {client_session_id}" in sent[-1][1]
    assert "Provider session: provider-session-1" in sent[-1][1]
    assert "Last context tokens: 123" in sent[-1][1]


def test_telegram_turn_error_is_reported_to_channel(tmp_path, monkeypatch):
    client = _setup_app(tmp_path, monkeypatch)
    sent = []
    edited = []
    next_message_id = {"value": 200}

    def fake_post_json(self, method, payload):
        if method == "sendMessage":
            next_message_id["value"] += 1
            sent.append(payload)
            return {"ok": True, "result": {"message_id": next_message_id["value"]}}
        if method == "editMessageText":
            edited.append(payload)
            return {"ok": True, "result": {"message_id": payload["message_id"]}}
        return {"ok": True}

    async def fake_execute_conversation_turn(self, conversation, text, attachments=None):
        raise RuntimeError("OpenCode CLI exec failed: provider unavailable")

    monkeypatch.setattr(TelegramChannelAdapter, "_post_json", fake_post_json)
    monkeypatch.setattr(ChannelService, "execute_conversation_turn", fake_execute_conversation_turn)

    headers, created = _create_user(client)
    token_resp = client.post(
        f"/management/v1/users/{created['user_id']}/channels/link-token",
        headers=headers,
        json={"channel": "telegram", "bot_name": "engineering-bot"},
    )
    raw_token = token_resp.json()["data"]["raw_token"]

    client.post(
        "/channels/telegram/engineering-bot/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        json={"message": {"chat": {"id": 1001}, "from": {"id": 2002}, "text": f"/link {raw_token}"}},
    )
    client.post(
        "/channels/telegram/engineering-bot/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        json={"message": {"chat": {"id": 1001}, "from": {"id": 2002}, "text": "/provider opencode"}},
    )

    resp = client.post(
        "/channels/telegram/engineering-bot/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        json={"message": {"chat": {"id": 1001}, "from": {"id": 2002}, "text": "Please edit the project"}},
    )

    assert resp.status_code == 200
    assert any("Status: Failed" in payload["text"] for payload in edited)
    assert sent[-1]["text"].startswith("Codara turn failed:")
    assert "provider unavailable" in sent[-1]["text"]


def test_telegram_webhook_executes_turn_via_user_bound_inference_service(tmp_path, monkeypatch):
    client = _setup_app(tmp_path, monkeypatch)
    sent = []
    monkeypatch.setattr(TelegramChannelAdapter, "send_text", lambda self, chat_id, text, thread_id=None: sent.append((chat_id, text, thread_id)))

    headers, created = _create_user(client)
    token_resp = client.post(
        f"/management/v1/users/{created['user_id']}/channels/link-token",
        headers=headers,
        json={"channel": "telegram", "bot_name": "engineering-bot"},
    )
    raw_token = token_resp.json()["data"]["raw_token"]

    client.post(
        "/channels/telegram/engineering-bot/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        json={"message": {"chat": {"id": 1001}, "from": {"id": 2002}, "text": f"/link {raw_token}"}},
    )
    client.post(
        "/channels/telegram/engineering-bot/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        json={"message": {"chat": {"id": 1001}, "from": {"id": 2002}, "text": "/workspace project-a"}},
    )
    client.post(
        "/channels/telegram/engineering-bot/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        json={"message": {"chat": {"id": 1001}, "from": {"id": 2002}, "text": "/provider gemini"}},
    )

    observed = {}

    async def fake_handle_request(options, messages, provider_model=None):
        observed["workspace_root"] = options.workspace_root
        observed["workspace_id"] = options.workspace_id
        observed["client_session_id"] = options.client_session_id
        observed["provider"] = options.provider.value
        observed["user_id"] = options.user_id
        observed["api_key_id"] = options.api_key_id
        observed["message_count"] = len(messages)
        observed["first_role"] = messages[0].role
        return TurnResult(
            output="Patch applied",
            backend_id="tg-sess-1",
            finish_reason="stop",
            modified_files=["app.py"],
        )

    monkeypatch.setattr(gateway_app.orchestrator, "handle_request", fake_handle_request)

    resp = client.post(
        "/channels/telegram/engineering-bot/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        json={
            "message": {
                "chat": {"id": 1001},
                "from": {"id": 2002},
                "text": "Please edit the project",
            }
        },
    )

    assert resp.status_code == 200
    assert observed["workspace_id"] == "project-a"
    assert observed["provider"] == "gemini"
    assert observed["user_id"] == created["user_id"]
    assert observed["client_session_id"].startswith(f"{created['user_id']}::project-a::telegram:engineering-bot:1001:0")
    assert Path(observed["workspace_root"]).resolve() == (Path(created["workspace_path"]) / "project-a").resolve()
    assert sent[-1][1].startswith("Patch applied")
    assert "Modified files:" in sent[-1][1]


def test_telegram_turn_sends_realtime_status_updates(tmp_path, monkeypatch):
    client = _setup_app(tmp_path, monkeypatch)
    sent = []
    edited = []
    next_message_id = {"value": 100}

    def fake_post_json(self, method, payload):
        if method == "sendMessage":
            next_message_id["value"] += 1
            sent.append(payload)
            return {"ok": True, "result": {"message_id": next_message_id["value"]}}
        if method == "editMessageText":
            edited.append(payload)
            return {"ok": True, "result": {"message_id": payload["message_id"]}}
        return {"ok": True}

    async def fake_execute_conversation_turn(self, conversation, text, attachments=None):
        return ChannelTurnResult(
            text="Turn complete",
            workspace_id=conversation["workspace_id"],
            provider=conversation["provider"],
            client_session_id="client-session",
            attachments=[],
            modified_files=["app.py"],
            diff="diff",
        )

    monkeypatch.setattr(TelegramChannelAdapter, "_post_json", fake_post_json)
    monkeypatch.setattr(ChannelService, "execute_conversation_turn", fake_execute_conversation_turn)

    headers, created = _create_user(client)
    token_resp = client.post(
        f"/management/v1/users/{created['user_id']}/channels/link-token",
        headers=headers,
        json={"channel": "telegram", "bot_name": "engineering-bot"},
    )
    raw_token = token_resp.json()["data"]["raw_token"]

    client.post(
        "/channels/telegram/engineering-bot/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        json={"message": {"chat": {"id": 1001}, "from": {"id": 2002}, "text": f"/link {raw_token}"}},
    )
    client.post(
        "/channels/telegram/engineering-bot/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        json={"message": {"chat": {"id": 1001}, "from": {"id": 2002}, "text": "/workspace project-a"}},
    )

    resp = client.post(
        "/channels/telegram/engineering-bot/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        json={"message": {"chat": {"id": 1001}, "from": {"id": 2002}, "text": "Please edit the project"}},
    )

    assert resp.status_code == 200
    sent_texts = [payload["text"] for payload in sent]
    edited_texts = [payload["text"] for payload in edited]
    assert any("Status: Queued" in text for text in sent_texts)
    assert any("Status: Preparing workspace" in text for text in edited_texts)
    assert any("Status: Running provider" in text for text in edited_texts)
    assert any("Status: Completed" in text and "Modified files: 1" in text for text in edited_texts)
    assert sent_texts[-1].startswith("Turn complete")


def test_telegram_document_attachment_is_staged_into_workspace(tmp_path, monkeypatch):
    client = _setup_app(tmp_path, monkeypatch)
    sent = []
    monkeypatch.setattr(TelegramChannelAdapter, "send_text", lambda self, chat_id, text, thread_id=None: sent.append((chat_id, text, thread_id)))
    monkeypatch.setattr(
        TelegramChannelAdapter,
        "fetch_attachment",
        lambda self, file_id, filename, content_type=None: gateway_app.AttachmentInput(filename=filename, content=b"hello from telegram", content_type=content_type),
    )

    headers, created = _create_user(client)
    token_resp = client.post(
        f"/management/v1/users/{created['user_id']}/channels/link-token",
        headers=headers,
        json={"channel": "telegram", "bot_name": "engineering-bot"},
    )
    raw_token = token_resp.json()["data"]["raw_token"]
    client.post(
        "/channels/telegram/engineering-bot/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        json={"message": {"chat": {"id": 1001}, "from": {"id": 2002}, "text": f"/link {raw_token}"}},
    )

    observed = {}

    async def fake_handle_request(options, messages, provider_model=None):
        observed["workspace_root"] = options.workspace_root
        observed["messages"] = messages
        return TurnResult(output="Uploaded file processed", backend_id="tg-sess-2", finish_reason="stop")

    monkeypatch.setattr(gateway_app.orchestrator, "handle_request", fake_handle_request)

    resp = client.post(
        "/channels/telegram/engineering-bot/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        json={
            "message": {
                "chat": {"id": 1001},
                "from": {"id": 2002},
                "text": "Use the uploaded file",
                "document": {
                    "file_id": "file-123",
                    "file_name": "notes.txt",
                    "mime_type": "text/plain",
                },
            }
        },
    )

    assert resp.status_code == 200
    assert len(observed["messages"]) == 2
    system_message = observed["messages"][0]
    assert system_message.role == "system"
    assert ".uag/uploads/" in system_message.content
    upload_path = system_message.content.split("-> ", 1)[1].split("\n", 1)[0].split(" ", 1)[0]
    assert (Path(observed["workspace_root"]) / upload_path).read_text() == "hello from telegram"


def test_multiple_telegram_bots_keep_bindings_separate(tmp_path, monkeypatch):
    client = _setup_app(tmp_path, monkeypatch)
    sent = []
    monkeypatch.setattr(TelegramChannelAdapter, "send_text", lambda self, chat_id, text, thread_id=None: sent.append((self.bot_name, chat_id, text, thread_id)))

    headers, created = _create_user(client)
    token_resp = client.post(
        f"/management/v1/users/{created['user_id']}/channels/link-token",
        headers=headers,
        json={"channel": "telegram", "bot_name": "engineering-bot"},
    )
    raw_token = token_resp.json()["data"]["raw_token"]

    link_resp = client.post(
        "/channels/telegram/ops-bot/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret-2"},
        json={"message": {"chat": {"id": 1001}, "from": {"id": 2002}, "text": f"/link {raw_token}"}},
    )

    assert link_resp.status_code == 200
    assert gateway_app.db_manager.get_channel_user_link("telegram", "engineering-bot", "2002") is None
    assert gateway_app.db_manager.get_channel_user_link("telegram", "ops-bot", "2002") is None
    assert sent[-1][2] == "Invalid or expired channel link token"


def test_telegram_webhook_route_rejects_polling_mode(tmp_path, monkeypatch):
    client = _setup_app(tmp_path, monkeypatch, receive_mode="polling")

    resp = client.post(
        "/channels/telegram/engineering-bot/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        json={"message": {"chat": {"id": 1001}, "from": {"id": 2002}, "text": "hello"}},
    )

    assert resp.status_code == 503
    assert "webhook route unavailable" in resp.json()["detail"]


def test_telegram_polling_manager_processes_updates_and_saves_offset(tmp_path, monkeypatch):
    _setup_app(tmp_path, monkeypatch, receive_mode="polling")
    adapter = gateway_app._telegram_adapter("engineering-bot")
    handled = []
    calls = {"count": 0}

    def fake_delete_webhook(self, drop_pending_updates=False):
        return {"ok": True}

    def fake_get_updates(self, offset=0, timeout=20):
        calls["count"] += 1
        if calls["count"] == 1:
            return [{"update_id": 41, "message": {"chat": {"id": 1001}, "from": {"id": 2002}, "text": "hello"}}]
        time.sleep(0.05)
        return []

    async def fake_handle_update(self, update):
        handled.append(update["update_id"])
        return {"handled": True}

    monkeypatch.setattr(TelegramChannelAdapter, "delete_webhook", fake_delete_webhook)
    monkeypatch.setattr(TelegramChannelAdapter, "get_updates", fake_get_updates)
    monkeypatch.setattr(TelegramChannelAdapter, "handle_update", fake_handle_update)

    async def run_poll():
        manager = TelegramPollingManager([adapter])
        await manager.start()
        await asyncio.sleep(0.15)
        await manager.stop()

    asyncio.run(run_poll())

    assert handled == [41]
    assert gateway_app.db_manager.get_channel_polling_offset("telegram", "engineering-bot") == 42


def test_telegram_polling_dispatches_updates_without_waiting_for_long_turn(tmp_path, monkeypatch):
    _setup_app(tmp_path, monkeypatch, receive_mode="polling")
    adapter = gateway_app._telegram_adapter("engineering-bot")
    started = asyncio.Event()
    release_first = asyncio.Event()
    handled = []
    calls = {"count": 0}

    def fake_delete_webhook(self, drop_pending_updates=False):
        return {"ok": True}

    def fake_get_updates(self, offset=0, timeout=20):
        calls["count"] += 1
        if calls["count"] == 1:
            return [{"update_id": 41, "message": {"chat": {"id": 1001}, "from": {"id": 2002}, "text": "long turn"}}]
        if calls["count"] == 2:
            return [{"update_id": 42, "message": {"chat": {"id": 1001}, "from": {"id": 2002}, "text": "/status"}}]
        time.sleep(0.05)
        return []

    async def fake_handle_update(self, update):
        handled.append(update["update_id"])
        if update["update_id"] == 41:
            started.set()
            await release_first.wait()

    monkeypatch.setattr(TelegramChannelAdapter, "delete_webhook", fake_delete_webhook)
    monkeypatch.setattr(TelegramChannelAdapter, "get_updates", fake_get_updates)
    monkeypatch.setattr(TelegramChannelAdapter, "handle_update", fake_handle_update)

    async def run_poll():
        manager = TelegramPollingManager([adapter])
        await manager.start()
        await asyncio.wait_for(started.wait(), timeout=1)
        for _ in range(20):
            if 42 in handled:
                break
            await asyncio.sleep(0.02)
        release_first.set()
        await manager.stop()

    asyncio.run(run_poll())

    assert handled[:2] == [41, 42]
    assert gateway_app.db_manager.get_channel_polling_offset("telegram", "engineering-bot") >= 43
