from pathlib import Path

from fastapi.testclient import TestClient

import codara.gateway.app as gateway_app
from codara.channels.telegram import TelegramChannelAdapter
from codara.config import ChannelsSettings
from codara.core.models import TurnResult
from codara.database.manager import DatabaseManager
from codara.orchestrator.engine import Orchestrator
from tests.helpers import operator_headers


def _setup_app(tmp_path, monkeypatch):
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
                "receive_mode": "webhook",
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
