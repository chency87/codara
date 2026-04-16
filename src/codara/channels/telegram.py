from __future__ import annotations

import json
from typing import Any, Optional
from urllib import parse, request

from fastapi import HTTPException

from codara.channels.service import ChannelService
from codara.config import TelegramBotSettings, TelegramChannelSettings
from codara.services.inference import AttachmentInput
from codara.telemetry import record_event, start_span


class TelegramChannelAdapter:
    channel = "telegram"

    def __init__(self, channel_service: ChannelService, *, channel_config: TelegramChannelSettings, bot_config: TelegramBotSettings):
        self.channel_service = channel_service
        self.channel_config = channel_config
        self.bot_config = bot_config
        self.bot_name = bot_config.name
        self.bot_token = bot_config.token
        self.webhook_secret = bot_config.webhook_secret
        self.api_base = channel_config.api_base.rstrip("/")
        self.mention_only = bot_config.mention_only if bot_config.mention_only is not None else channel_config.mention_only

    def _require_configured(self):
        if not self.bot_token:
            raise HTTPException(status_code=503, detail="Telegram channel not configured")
        if not self.bot_config.enabled or not self.channel_config.enabled:
            raise HTTPException(status_code=503, detail="Telegram bot is disabled")
        if self.channel_config.receive_mode != "webhook":
            raise HTTPException(status_code=503, detail="Telegram webhook route unavailable in current receive mode")

    def verify_webhook_secret(self, provided_secret: Optional[str]):
        expected = self.webhook_secret
        if expected and provided_secret != expected:
            raise HTTPException(status_code=401, detail="Invalid Telegram webhook secret")

    def _api_url(self, method: str) -> str:
        self._require_configured()
        return f"{self.api_base}/bot{self.bot_token}/{method}"

    def send_text(self, chat_id: str, text: str, *, thread_id: str | None = None) -> None:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if thread_id:
            try:
                payload["message_thread_id"] = int(thread_id)
            except ValueError:
                pass
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self._api_url("sendMessage"),
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(req, timeout=15):
            return

    def _telegram_get(self, method: str, query: dict[str, str]) -> dict[str, Any]:
        url = f"{self._api_url(method)}?{parse.urlencode(query)}"
        with request.urlopen(url, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))

    def fetch_attachment(self, file_id: str, filename: str, content_type: Optional[str] = None) -> AttachmentInput:
        meta = self._telegram_get("getFile", {"file_id": file_id})
        if not meta.get("ok") or not isinstance(meta.get("result"), dict):
            raise RuntimeError("Telegram getFile failed")
        file_path = meta["result"].get("file_path")
        if not isinstance(file_path, str) or not file_path:
            raise RuntimeError("Telegram file path missing")
        url = f"{self.api_base}/file/bot{self.bot_token}/{file_path}"
        with request.urlopen(url, timeout=30) as response:
            content = response.read()
        return AttachmentInput(filename=filename, content=content, content_type=content_type)

    def _conversation_key(self, chat_id: str, thread_id: Optional[str]) -> str:
        return f"telegram:{self.bot_name}:{chat_id}:{thread_id or '0'}"

    def _should_ignore_for_mentions(self, message: dict[str, Any], text: str) -> bool:
        if not self.mention_only:
            return False
        chat = message.get("chat") or {}
        if chat.get("type") == "private":
            return False
        username = (self.bot_config.username or "").lstrip("@").strip()
        if not username:
            return False
        lowered = text.lower()
        return f"@{username.lower()}" not in lowered

    def _render_turn_reply(self, result) -> str:
        lines = [result.text]
        if result.modified_files:
            lines.append("")
            lines.append("Modified files:")
            lines.extend(f"- {path}" for path in result.modified_files[:10])
        return "\n".join(lines).strip()

    async def handle_update(self, update: dict[str, Any]) -> dict[str, Any]:
        async with start_span(
            "telegram.handle_update",
            component="channel.telegram",
            db=self.channel_service.db,
            attributes={"bot_name": self.bot_name},
        ):
            message = update.get("message") or update.get("edited_message")
            if not isinstance(message, dict):
                return {"handled": False, "reason": "unsupported-update"}
            chat = message.get("chat") or {}
            sender = message.get("from") or {}
            chat_id = str(chat.get("id", ""))
            external_user_id = str(sender.get("id", ""))
            thread_id = message.get("message_thread_id")
            thread_token = str(thread_id) if thread_id is not None else None
            text = (message.get("text") or message.get("caption") or "").strip()
            conversation_key = self._conversation_key(chat_id, thread_token)

            if not external_user_id or not chat_id:
                return {"handled": False, "reason": "missing-identifiers"}
            if self._should_ignore_for_mentions(message, text):
                return {"handled": False, "reason": "mention-only"}
            record_event(
                "telegram.update.received",
                component="channel.telegram",
                db=self.channel_service.db,
                attributes={
                    "bot_name": self.bot_name,
                    "chat_id": chat_id,
                    "external_user_id": external_user_id,
                    "has_text": bool(text),
                    "conversation_key": conversation_key,
                },
            )

            if text.startswith("/link "):
                token = text.split(" ", 1)[1].strip()
                try:
                    self.channel_service.link_external_user(
                        channel=self.channel,
                        bot_name=self.bot_name,
                        raw_token=token,
                        external_user_id=external_user_id,
                        external_chat_id=chat_id,
                    )
                except HTTPException as exc:
                    self.send_text(chat_id, str(exc.detail), thread_id=thread_token)
                    return {"handled": True, "action": "link-error"}
                self.send_text(chat_id, "Telegram account linked to Codara.", thread_id=thread_token)
                return {"handled": True, "action": "linked"}

            user = self.channel_service.get_bound_user(channel=self.channel, bot_name=self.bot_name, external_user_id=external_user_id)
            if not user:
                self.send_text(chat_id, "This Telegram account is not linked. Use /link <token> first.", thread_id=thread_token)
                return {"handled": True, "action": "not-linked"}

            conversation = self.channel_service.get_or_create_conversation(
                channel=self.channel,
                bot_name=self.bot_name,
                conversation_key=conversation_key,
                user_id=user.user_id,
                external_chat_id=chat_id,
                external_thread_id=thread_token,
            )

            if text.startswith("/workspace "):
                workspace_id = text.split(" ", 1)[1].strip()
                try:
                    updated = self.channel_service.update_conversation_workspace(conversation, workspace_id)
                except HTTPException as exc:
                    self.send_text(chat_id, f"Workspace change failed: {exc.detail}", thread_id=thread_token)
                    return {"handled": True, "action": "workspace-error"}
                self.send_text(chat_id, f"Workspace set to {updated['workspace_id']}.", thread_id=thread_token)
                return {"handled": True, "action": "workspace-set"}

            if text.startswith("/provider "):
                provider = text.split(" ", 1)[1].strip().lower()
                try:
                    updated = self.channel_service.update_conversation_provider(conversation, provider)
                except Exception:
                    self.send_text(chat_id, "Unsupported provider.", thread_id=thread_token)
                    return {"handled": True, "action": "provider-error"}
                self.send_text(chat_id, f"Provider set to {updated['provider']}.", thread_id=thread_token)
                return {"handled": True, "action": "provider-set"}

            if text == "/reset":
                client_session_id = self.channel_service.reset_conversation_session(conversation)
                self.send_text(chat_id, f"Session reset: `{client_session_id}`", thread_id=thread_token)
                return {"handled": True, "action": "reset"}

            if text == "/status":
                self.send_text(
                    chat_id,
                    (
                        f"Workspace: {conversation['workspace_id']}\n"
                        f"Provider: {conversation['provider']}\n"
                        f"Session: {conversation['session_label']}"
                    ),
                    thread_id=thread_token,
                )
                return {"handled": True, "action": "status"}

            if not text:
                self.send_text(chat_id, "Send text or a supported command.", thread_id=thread_token)
                return {"handled": True, "action": "empty"}

            attachments: list[AttachmentInput] = []
            document = message.get("document")
            if isinstance(document, dict) and document.get("file_id"):
                attachments.append(
                    self.fetch_attachment(
                        str(document["file_id"]),
                        document.get("file_name") or "telegram-upload",
                        document.get("mime_type"),
                    )
                )

            turn = await self.channel_service.execute_conversation_turn(
                conversation=conversation,
                text=text,
                attachments=attachments,
            )
            self.send_text(chat_id, self._render_turn_reply(turn), thread_id=thread_token)
            return {"handled": True, "action": "turn"}
