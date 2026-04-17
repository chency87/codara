from __future__ import annotations

import json
import logging
import asyncio
from time import perf_counter
from typing import Any, Optional
from urllib import parse, request
from urllib.error import HTTPError, URLError

from fastapi import HTTPException

from codara.channels.service import ChannelService
from codara.config import TelegramBotSettings, TelegramChannelSettings
from codara.services.inference import AttachmentInput
from codara.telemetry import record_event, start_span

logger = logging.getLogger(__name__)

DEFAULT_TELEGRAM_COMMANDS = [
    {"command": "start", "description": "Show how to use this Codara bot"},
    {"command": "help", "description": "Show available commands and workflow"},
    {"command": "commands", "description": "List the bot commands"},
    {"command": "link", "description": "Link this Telegram account to Codara"},
    {"command": "whoami", "description": "Show your linked Codara identity"},
    {"command": "workspace", "description": "Select the active workspace"},
    {"command": "projects", "description": "List your Codara projects"},
    {"command": "project", "description": "Select a project workspace"},
    {"command": "project_create", "description": "Create a project workspace"},
    {"command": "project_info", "description": "Show project details"},
    {"command": "provider", "description": "Select the active provider"},
    {"command": "status", "description": "Show current workspace and session"},
    {"command": "session", "description": "Show current runtime session status"},
    {"command": "reset", "description": "Reset the current conversation session"},
]

TELEGRAM_TEXT_CHUNK_LIMIT = 3900
TELEGRAM_MAX_REPLY_CHUNKS = 4
TELEGRAM_STATUS_UPDATE_INTERVAL_SECONDS = 12.0


class TelegramApiError(RuntimeError):
    def __init__(self, method: str, *, error_code: int | None = None, description: str | None = None):
        self.method = method
        self.error_code = error_code
        self.description = description or "Telegram API request failed"
        detail = f"Telegram {method} failed"
        if error_code is not None:
            detail = f"{detail} ({error_code})"
        super().__init__(f"{detail}: {self.description}")


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

    def _require_webhook_mode(self):
        self._require_configured()
        if self.channel_config.receive_mode != "webhook":
            raise HTTPException(status_code=503, detail="Telegram webhook route unavailable in current receive mode")

    def verify_webhook_secret(self, provided_secret: Optional[str]):
        self._require_webhook_mode()
        expected = self.webhook_secret
        if expected and provided_secret != expected:
            raise HTTPException(status_code=401, detail="Invalid Telegram webhook secret")

    def _api_url(self, method: str) -> str:
        self._require_configured()
        return f"{self.api_base}/bot{self.bot_token}/{method}"

    def _thread_payload(self, thread_id: str | None) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if thread_id:
            try:
                payload["message_thread_id"] = int(thread_id)
            except ValueError:
                pass
        return payload

    def _post_json(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self._api_url(method),
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=15) as response:
                content = response.read()
        except HTTPError as exc:
            content = exc.read()
            description = exc.reason
            try:
                error_payload = json.loads(content.decode("utf-8")) if content else {}
                if isinstance(error_payload, dict):
                    description = str(error_payload.get("description") or description)
                    error_code = error_payload.get("error_code")
                    if not isinstance(error_code, int):
                        error_code = exc.code
                    raise TelegramApiError(method, error_code=error_code, description=description) from exc
            except json.JSONDecodeError:
                pass
            raise TelegramApiError(method, error_code=exc.code, description=str(description)) from exc
        if not content:
            return {"ok": True}
        result = json.loads(content.decode("utf-8"))
        if isinstance(result, dict) and not result.get("ok", True):
            error_code = result.get("error_code")
            if not isinstance(error_code, int):
                error_code = None
            description = result.get("description")
            raise TelegramApiError(method, error_code=error_code, description=str(description) if description else None)
        return result

    def _split_text_chunks(self, text: str) -> list[str]:
        remaining = text or " "
        chunks: list[str] = []
        while remaining:
            if len(remaining) <= TELEGRAM_TEXT_CHUNK_LIMIT:
                chunks.append(remaining)
                break
            split_at = remaining.rfind("\n", 0, TELEGRAM_TEXT_CHUNK_LIMIT)
            if split_at < TELEGRAM_TEXT_CHUNK_LIMIT // 2:
                split_at = remaining.rfind(" ", 0, TELEGRAM_TEXT_CHUNK_LIMIT)
            if split_at < TELEGRAM_TEXT_CHUNK_LIMIT // 2:
                split_at = TELEGRAM_TEXT_CHUNK_LIMIT
            chunks.append(remaining[:split_at].rstrip())
            remaining = remaining[split_at:].lstrip()
        if len(chunks) > TELEGRAM_MAX_REPLY_CHUNKS:
            notice = "\n\n[Reply truncated. Ask for a smaller summary or inspect Codara logs/dashboard for full details.]"
            chunks = chunks[:TELEGRAM_MAX_REPLY_CHUNKS]
            chunks[-1] = (chunks[-1][: TELEGRAM_TEXT_CHUNK_LIMIT - len(notice)]).rstrip() + notice
        return chunks

    def send_text(self, chat_id: str, text: str, *, thread_id: str | None = None) -> list[dict[str, Any]]:
        chunks = self._split_text_chunks(text)
        responses: list[dict[str, Any]] = []
        for index, chunk in enumerate(chunks, start=1):
            payload: dict[str, Any] = {"chat_id": chat_id, "text": chunk, **self._thread_payload(thread_id)}
            try:
                response = self._post_json("sendMessage", payload)
            except Exception as exc:
                record_event(
                    "telegram.message.send_failed",
                    component="channel.telegram",
                    db=self.channel_service.db,
                    level="ERROR",
                    attributes={
                        "bot_name": self.bot_name,
                        "chat_id": chat_id,
                        "chunk_index": index,
                        "chunk_count": len(chunks),
                        "error": str(exc),
                        "exception_type": type(exc).__name__,
                    },
                )
                raise
            responses.append(response)
            record_event(
                "telegram.message.sent",
                component="channel.telegram",
                db=self.channel_service.db,
                attributes={
                    "bot_name": self.bot_name,
                    "chat_id": chat_id,
                    "chunk_index": index,
                    "chunk_count": len(chunks),
                    "text_length": len(chunk),
                },
            )
        return responses

    def edit_text(self, chat_id: str, message_id: int, text: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text[:TELEGRAM_TEXT_CHUNK_LIMIT],
        }
        response = self._post_json("editMessageText", payload)
        record_event(
            "telegram.message.edited",
            component="channel.telegram",
            db=self.channel_service.db,
            attributes={
                "bot_name": self.bot_name,
                "chat_id": chat_id,
                "message_id": message_id,
                "text_length": len(payload["text"]),
            },
        )
        return response

    def send_chat_action(self, chat_id: str, *, action: str = "typing", thread_id: str | None = None) -> None:
        payload: dict[str, Any] = {"chat_id": chat_id, "action": action, **self._thread_payload(thread_id)}
        self._post_json("sendChatAction", payload)

    def set_message_reaction(self, chat_id: str, message_id: int, *, emoji: str = "👀", is_big: bool = False) -> None:
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "reaction": [{"type": "emoji", "emoji": emoji}],
            "is_big": is_big,
        }
        self._post_json("setMessageReaction", payload)

    def acknowledge_inbound_message(self, message: dict[str, Any], *, chat_id: str, thread_id: str | None = None) -> None:
        message_id = message.get("message_id")
        if isinstance(message_id, int):
            try:
                self.set_message_reaction(chat_id, message_id)
                return
            except (TelegramApiError, URLError, OSError, ValueError, json.JSONDecodeError):
                logger.debug("Telegram reaction acknowledgement failed", exc_info=True)
        try:
            self.send_chat_action(chat_id, action="typing", thread_id=thread_id)
        except (TelegramApiError, URLError, OSError, ValueError, json.JSONDecodeError):
            logger.debug("Telegram chat-action acknowledgement failed", exc_info=True)

    def _telegram_get(self, method: str, query: dict[str, str], *, timeout: int = 15) -> dict[str, Any]:
        url = f"{self._api_url(method)}?{parse.urlencode(query)}"
        with request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def delete_webhook(self, *, drop_pending_updates: bool = False) -> dict[str, Any]:
        return self._telegram_get(
            "deleteWebhook",
            {"drop_pending_updates": "true" if drop_pending_updates else "false"},
        )

    def get_updates(self, *, offset: int = 0, timeout: int = 20) -> list[dict[str, Any]]:
        payload = self._telegram_get(
            "getUpdates",
            {
                "offset": str(offset),
                "timeout": str(timeout),
                "allowed_updates": json.dumps(["message", "edited_message"]),
            },
            timeout=max(timeout + 10, 30),
        )
        result = payload.get("result")
        if not payload.get("ok") or not isinstance(result, list):
            raise RuntimeError("Telegram getUpdates failed")
        return [item for item in result if isinstance(item, dict)]

    def set_my_commands(self, commands: Optional[list[dict[str, str]]] = None) -> dict[str, Any]:
        payload = {
            "commands": commands or DEFAULT_TELEGRAM_COMMANDS,
        }
        return self._post_json("setMyCommands", payload)

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
        elif not result.diff:
            lines.append("")
            lines.append("No workspace file changes were detected for this turn.")
        return "\n".join(lines).strip()

    def _message_id_from_send_results(self, results: Any) -> Optional[int]:
        if not isinstance(results, list) or not results:
            return None
        first = results[0]
        if not isinstance(first, dict):
            return None
        result = first.get("result")
        if not isinstance(result, dict):
            return None
        message_id = result.get("message_id")
        return message_id if isinstance(message_id, int) else None

    def _render_turn_status(
        self,
        *,
        stage: str,
        workspace_id: str,
        provider: str,
        elapsed_seconds: Optional[float] = None,
        modified_file_count: Optional[int] = None,
    ) -> str:
        lines = [
            "Codara workspace turn",
            f"Status: {stage}",
            f"Workspace: {workspace_id}",
            f"Provider: {provider}",
        ]
        if elapsed_seconds is not None:
            lines.append(f"Elapsed: {int(elapsed_seconds)}s")
        if modified_file_count is not None:
            lines.append(f"Modified files: {modified_file_count}")
        return "\n".join(lines)

    def _render_turn_error(self, exc: Exception) -> str:
        detail = str(exc).strip() or type(exc).__name__
        return f"Codara turn failed: {detail[:1200]}"

    def _send_turn_status_message(self, chat_id: str, conversation: dict, *, thread_id: str | None = None) -> Optional[int]:
        text = self._render_turn_status(
            stage="Queued",
            workspace_id=conversation["workspace_id"],
            provider=conversation["provider"],
            elapsed_seconds=0,
        )
        try:
            return self._message_id_from_send_results(self.send_text(chat_id, text, thread_id=thread_id))
        except Exception:
            logger.debug("Telegram turn status message send failed", exc_info=True)
            record_event(
                "telegram.turn_status.send_failed",
                component="channel.telegram",
                db=self.channel_service.db,
                level="WARNING",
                attributes={"bot_name": self.bot_name, "chat_id": chat_id},
            )
            return None

    async def _update_turn_status(
        self,
        *,
        chat_id: str,
        message_id: Optional[int],
        conversation: dict,
        stage: str,
        started_at: float,
        modified_file_count: Optional[int] = None,
    ) -> None:
        if message_id is None:
            return
        text = self._render_turn_status(
            stage=stage,
            workspace_id=conversation["workspace_id"],
            provider=conversation["provider"],
            elapsed_seconds=perf_counter() - started_at,
            modified_file_count=modified_file_count,
        )
        try:
            await asyncio.to_thread(self.edit_text, chat_id, message_id, text)
        except Exception as exc:
            logger.debug("Telegram turn status update failed", exc_info=True)
            record_event(
                "telegram.turn_status.update_failed",
                component="channel.telegram",
                db=self.channel_service.db,
                level="WARNING",
                attributes={
                    "bot_name": self.bot_name,
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "stage": stage,
                    "error": str(exc),
                    "exception_type": type(exc).__name__,
                },
            )

    async def _turn_status_heartbeat(
        self,
        *,
        chat_id: str,
        message_id: Optional[int],
        conversation: dict,
        thread_id: str | None,
        started_at: float,
        stop_event: asyncio.Event,
    ) -> None:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=TELEGRAM_STATUS_UPDATE_INTERVAL_SECONDS)
                break
            except asyncio.TimeoutError:
                pass
            try:
                await asyncio.to_thread(self.send_chat_action, chat_id, action="typing", thread_id=thread_id)
            except Exception:
                logger.debug("Telegram turn status heartbeat typing action failed", exc_info=True)
            await self._update_turn_status(
                chat_id=chat_id,
                message_id=message_id,
                conversation=conversation,
                stage="Running provider",
                started_at=started_at,
            )

    def _render_help_text(self, *, linked: bool) -> str:
        lines = [
            "Codara Telegram Bot",
            "",
            "How to use it:",
            "1. Link your Telegram account with /link <token>",
            "2. Pick a workspace with /workspace <workspace_id>",
            "3. Pick a provider with /provider <codex|gemini|opencode>",
            "4. Send a normal text message to run a turn in that workspace",
            "",
            "Commands:",
            "/start - quick introduction",
            "/help - full usage guide",
            "/commands - list commands",
            "/whoami - show your linked Codara identity",
            "/projects - list your project workspaces",
            "/project <name> - switch to an existing project",
            "/project_create <name> [default|python|docs|empty] - create and switch to a project",
            "/project_info <name> - show project details",
            "/status - show current workspace, provider, and session",
            "/session - show current runtime session status",
            "/reset - reset the current conversation session",
        ]
        if linked:
            lines.extend(
                [
                    "/workspace <id> - switch workspace",
                    "/provider <name> - switch provider",
                ]
            )
        else:
            lines.append("/link <token> - link this Telegram account")
        return "\n".join(lines)

    def _render_projects_text(self, projects: list[dict[str, Any]]) -> str:
        if not projects:
            return "No Codara projects found. Create one with /project_create <name>."
        lines = ["Your Codara projects:"]
        for record in projects[:20]:
            metadata = record.get("project") or {}
            name = metadata.get("name") or record.get("name")
            template = metadata.get("template") or "unknown"
            relative = record.get("relative_path") or name
            lines.append(f"- {name} ({template}) -> /project {relative}")
        if len(projects) > 20:
            lines.append(f"...and {len(projects) - 20} more.")
        return "\n".join(lines)

    def _render_project_info_text(self, record: dict[str, Any]) -> str:
        metadata = record.get("project") or {}
        lines = [
            f"Project: {metadata.get('name') or record.get('name')}",
            f"Template: {metadata.get('template') or 'unknown'}",
            f"Workspace: {record.get('relative_path') or record.get('name')}",
            f"Path: {record.get('path')}",
            f"Default provider: {metadata.get('default_provider') or 'n/a'}",
            f"Git repo: {'yes' if record.get('git', {}).get('is_git_repo') else 'no'}",
            f"Bound sessions: {len(record.get('sessions') or [])}",
        ]
        if metadata.get("created_at"):
            lines.append(f"Created at: {metadata['created_at']}")
        return "\n".join(lines)

    def _render_commands_text(self) -> str:
        lines = ["Available commands:"]
        for item in DEFAULT_TELEGRAM_COMMANDS:
            lines.append(f"/{item['command']} - {item['description']}")
        return "\n".join(lines)

    def _render_whoami_text(self, user: Any, conversation: Optional[dict] = None) -> str:
        lines = [
            f"User ID: {user.user_id}",
            f"Name: {user.display_name}",
            f"Email: {user.email}",
        ]
        if conversation:
            lines.extend(
                [
                    f"Workspace: {conversation['workspace_id']}",
                    f"Provider: {conversation['provider']}",
                    f"Session: {conversation['session_label']}",
                ]
            )
        return "\n".join(lines)

    def _render_status_text(self, conversation: dict) -> str:
        runtime = self.channel_service.get_conversation_session_status(conversation)
        lines = [
            f"Workspace: {conversation['workspace_id']}",
            f"Provider: {conversation['provider']}",
            f"Session: {conversation['session_label']}",
            "",
            "Runtime:",
            f"Status: {runtime['status']}",
            f"Client session: {runtime['client_session_id']}",
        ]
        if runtime.get("backend_id"):
            lines.append(f"Provider session: {runtime['backend_id']}")
        if runtime.get("account_id"):
            lines.append(f"Account: {runtime['account_id']}")
        if runtime.get("cwd_path"):
            lines.append(f"Workspace path: {runtime['cwd_path']}")
        if runtime.get("last_context_tokens") is not None:
            lines.append(f"Last context tokens: {runtime['last_context_tokens']}")
        if runtime.get("updated_at"):
            lines.append(f"Updated at: {runtime['updated_at']}")
        if not runtime["exists"]:
            lines.append("No provider turn has started for this conversation yet.")
        return "\n".join(lines)

    async def handle_update(self, update: dict[str, Any]) -> dict[str, Any]:
        async with start_span(
            "telegram.handle_update",
            component="channel.telegram",
            db=self.channel_service.db,
            attributes={"bot_name": self.bot_name},
        ):
            def handled(action: str, **attributes: Any) -> dict[str, Any]:
                event_attributes = {"bot_name": self.bot_name, "action": action, **attributes}
                record_event(
                    "telegram.update.handled",
                    component="channel.telegram",
                    db=self.channel_service.db,
                    attributes=event_attributes,
                )
                return {"handled": True, "action": action}

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
            self.acknowledge_inbound_message(message, chat_id=chat_id, thread_id=thread_token)

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
                    return handled("link-error")
                self.send_text(chat_id, "Telegram account linked to Codara.", thread_id=thread_token)
                return handled("linked")

            if text in {"/start", "/help"}:
                link = self.channel_service.get_bound_user(
                    channel=self.channel,
                    bot_name=self.bot_name,
                    external_user_id=external_user_id,
                )
                self.send_text(chat_id, self._render_help_text(linked=bool(link)), thread_id=thread_token)
                return handled("help")

            if text == "/commands":
                self.send_text(chat_id, self._render_commands_text(), thread_id=thread_token)
                return handled("commands")

            user = self.channel_service.get_bound_user(channel=self.channel, bot_name=self.bot_name, external_user_id=external_user_id)
            if not user:
                self.send_text(chat_id, "This Telegram account is not linked. Use /link <token> first.", thread_id=thread_token)
                return handled("not-linked")

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
                    return handled("workspace-error")
                self.send_text(chat_id, f"Workspace set to {updated['workspace_id']}.", thread_id=thread_token)
                return handled("workspace-set", workspace_id=updated["workspace_id"])

            if text == "/projects":
                projects = self.channel_service.list_user_projects(user.user_id)
                self.send_text(chat_id, self._render_projects_text(projects), thread_id=thread_token)
                return handled("projects")

            if text.startswith("/project_create "):
                parts = text.split()
                if len(parts) < 2:
                    self.send_text(chat_id, "Usage: /project_create <name> [default|python|docs|empty]", thread_id=thread_token)
                    return handled("project-create-error")
                name = parts[1]
                template = parts[2].lower() if len(parts) >= 3 else "default"
                try:
                    result = self.channel_service.create_user_project(
                        user_id=user.user_id,
                        name=name,
                        template=template,
                        default_provider=conversation["provider"],
                    )
                    updated = self.channel_service.update_conversation_workspace(conversation, result["relative_path"])
                    conversation = updated
                except HTTPException as exc:
                    self.send_text(chat_id, f"Project creation failed: {exc.detail}", thread_id=thread_token)
                    return handled("project-create-error")
                except Exception as exc:
                    self.send_text(chat_id, f"Project creation failed: {exc}", thread_id=thread_token)
                    return handled("project-create-error")
                self.send_text(
                    chat_id,
                    f"Project created: {result['name']}\nWorkspace set to {updated['workspace_id']}.\nTemplate: {result['template']}",
                    thread_id=thread_token,
                )
                return handled("project-created", workspace_id=updated["workspace_id"])

            if text.startswith("/project_info "):
                name = text.split(" ", 1)[1].strip()
                try:
                    record = self.channel_service.get_user_project(user.user_id, name)
                except HTTPException as exc:
                    self.send_text(chat_id, f"Project lookup failed: {exc.detail}", thread_id=thread_token)
                    return handled("project-info-error")
                if not record:
                    self.send_text(chat_id, f"Project not found: {name}", thread_id=thread_token)
                    return handled("project-info-missing")
                self.send_text(chat_id, self._render_project_info_text(record), thread_id=thread_token)
                return handled("project-info")

            if text.startswith("/project "):
                name = text.split(" ", 1)[1].strip()
                try:
                    record = self.channel_service.get_user_project(user.user_id, name)
                    if not record:
                        self.send_text(chat_id, f"Project not found: {name}", thread_id=thread_token)
                        return handled("project-missing")
                    workspace_id = record.get("relative_path") or record.get("name") or name
                    updated = self.channel_service.update_conversation_workspace(conversation, workspace_id)
                except HTTPException as exc:
                    self.send_text(chat_id, f"Project switch failed: {exc.detail}", thread_id=thread_token)
                    return handled("project-error")
                self.send_text(chat_id, f"Project set to {updated['workspace_id']}.", thread_id=thread_token)
                return handled("project-set", workspace_id=updated["workspace_id"])

            if text.startswith("/provider "):
                provider = text.split(" ", 1)[1].strip().lower()
                try:
                    updated = self.channel_service.update_conversation_provider(conversation, provider)
                except Exception:
                    self.send_text(chat_id, "Unsupported provider.", thread_id=thread_token)
                    return handled("provider-error")
                self.send_text(chat_id, f"Provider set to {updated['provider']}.", thread_id=thread_token)
                return handled("provider-set", provider=updated["provider"])

            if text == "/reset":
                client_session_id = self.channel_service.reset_conversation_session(conversation)
                self.send_text(chat_id, f"Session reset: `{client_session_id}`", thread_id=thread_token)
                return handled("reset")

            if text in {"/status", "/session"}:
                self.send_text(chat_id, self._render_status_text(conversation), thread_id=thread_token)
                return handled("status")

            if text == "/whoami":
                self.send_text(
                    chat_id,
                    self._render_whoami_text(user, conversation),
                    thread_id=thread_token,
                )
                return handled("whoami")

            if not text:
                self.send_text(chat_id, "Send text or a supported command.", thread_id=thread_token)
                return handled("empty")

            status_started_at = perf_counter()
            status_message_id = self._send_turn_status_message(chat_id, conversation, thread_id=thread_token)
            await self._update_turn_status(
                chat_id=chat_id,
                message_id=status_message_id,
                conversation=conversation,
                stage="Preparing workspace",
                started_at=status_started_at,
            )

            attachments: list[AttachmentInput] = []
            document = message.get("document")
            if isinstance(document, dict) and document.get("file_id"):
                await self._update_turn_status(
                    chat_id=chat_id,
                    message_id=status_message_id,
                    conversation=conversation,
                    stage="Fetching attachment",
                    started_at=status_started_at,
                )
                attachments.append(
                    self.fetch_attachment(
                        str(document["file_id"]),
                        document.get("file_name") or "telegram-upload",
                        document.get("mime_type"),
                    )
                )

            await self._update_turn_status(
                chat_id=chat_id,
                message_id=status_message_id,
                conversation=conversation,
                stage="Running provider",
                started_at=status_started_at,
            )
            stop_status = asyncio.Event()
            heartbeat_task = asyncio.create_task(
                self._turn_status_heartbeat(
                    chat_id=chat_id,
                    message_id=status_message_id,
                    conversation=conversation,
                    thread_id=thread_token,
                    started_at=status_started_at,
                    stop_event=stop_status,
                )
            )
            try:
                turn = await self.channel_service.execute_conversation_turn(
                    conversation=conversation,
                    text=text,
                    attachments=attachments,
                )
            except Exception as exc:
                await self._update_turn_status(
                    chat_id=chat_id,
                    message_id=status_message_id,
                    conversation=conversation,
                    stage="Failed",
                    started_at=status_started_at,
                )
                self.send_text(chat_id, self._render_turn_error(exc), thread_id=thread_token)
                record_event(
                    "telegram.turn.failed",
                    component="channel.telegram",
                    db=self.channel_service.db,
                    level="ERROR",
                    attributes={
                        "bot_name": self.bot_name,
                        "chat_id": chat_id,
                        "workspace_id": conversation["workspace_id"],
                        "provider": conversation["provider"],
                        "error": str(exc),
                        "exception_type": type(exc).__name__,
                    },
                )
                return handled("turn-error", workspace_id=conversation["workspace_id"], provider=conversation["provider"])
            finally:
                stop_status.set()
                await heartbeat_task
            await self._update_turn_status(
                chat_id=chat_id,
                message_id=status_message_id,
                conversation=conversation,
                stage="Completed",
                started_at=status_started_at,
                modified_file_count=len(turn.modified_files),
            )
            self.send_text(chat_id, self._render_turn_reply(turn), thread_id=thread_token)
            return handled(
                "turn",
                workspace_id=turn.workspace_id,
                provider=turn.provider,
                client_session_id=turn.client_session_id,
                modified_file_count=len(turn.modified_files),
                has_diff=bool(turn.diff),
            )


class TelegramPollingManager:
    def __init__(self, adapters: list[TelegramChannelAdapter]):
        self.adapters = adapters
        self._tasks: dict[str, asyncio.Task] = {}
        self._update_tasks: set[asyncio.Task] = set()
        self._stopping = False

    async def start(self) -> None:
        self._stopping = False
        for adapter in self.adapters:
            if adapter.bot_name in self._tasks and not self._tasks[adapter.bot_name].done():
                continue
            self._tasks[adapter.bot_name] = asyncio.create_task(
                self._poll_bot(adapter),
                name=f"telegram-poll-{adapter.bot_name}",
            )

    async def stop(self) -> None:
        self._stopping = True
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug("Telegram polling task exited with error during shutdown", exc_info=True)
        update_tasks = list(self._update_tasks)
        for task in update_tasks:
            task.cancel()
        for task in update_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug("Telegram update task exited with error during shutdown", exc_info=True)
        self._tasks.clear()
        self._update_tasks.clear()

    def _dispatch_update(self, adapter: TelegramChannelAdapter, update: dict[str, Any]) -> None:
        async def run_update() -> None:
            try:
                await adapter.handle_update(update)
            except Exception:
                logger.warning("Telegram polling failed to handle update for bot %s", adapter.bot_name, exc_info=True)

        task = asyncio.create_task(run_update(), name=f"telegram-update-{adapter.bot_name}-{update.get('update_id', 'unknown')}")
        self._update_tasks.add(task)
        task.add_done_callback(self._update_tasks.discard)

    async def _poll_bot(self, adapter: TelegramChannelAdapter) -> None:
        db = adapter.channel_service.db
        try:
            await asyncio.to_thread(adapter.delete_webhook, drop_pending_updates=False)
        except Exception:
            logger.warning("Telegram polling failed to delete webhook for bot %s", adapter.bot_name, exc_info=True)
        offset = db.get_channel_polling_offset(adapter.channel, adapter.bot_name)
        while not self._stopping:
            try:
                updates = await asyncio.to_thread(adapter.get_updates, offset=offset, timeout=20)
                for update in updates:
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        offset = update_id + 1
                    self._dispatch_update(adapter, update)
                    if isinstance(update_id, int):
                        db.save_channel_polling_offset(adapter.channel, adapter.bot_name, offset)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Telegram polling loop failed for bot %s", adapter.bot_name, exc_info=True)
                await asyncio.sleep(2)


async def register_telegram_bot_commands(adapters: list[TelegramChannelAdapter]) -> None:
    for adapter in adapters:
        try:
            await asyncio.to_thread(adapter.set_my_commands)
        except Exception:
            logger.warning("Telegram command registration failed for bot %s", adapter.bot_name, exc_info=True)
