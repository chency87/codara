from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException

from codara.config import Settings, get_provider_default_model
from codara.core.models import Message, ProviderType, UagOptions
from codara.database.manager import DatabaseManager
from codara.services.inference import AttachmentInput, InferenceService
from codara.workspace.manager import WorkspaceManager
from codara.workspace.project import PROJECT_TEMPLATES, ProjectService


@dataclass
class ChannelTurnResult:
    text: str
    workspace_id: str
    provider: str
    client_session_id: str
    attachments: list[dict[str, object]]
    modified_files: list[str]
    diff: Optional[str]


class ChannelService:
    def __init__(self, db: DatabaseManager, inference: InferenceService, settings: Settings):
        self.db = db
        self.inference = inference
        self.settings = settings

    def create_link_token(self, *, user_id: str, channel: str, bot_name: str, created_by: str, expires_in_minutes: int = 30) -> dict:
        return self.db.create_channel_link_token(
            user_id=user_id,
            channel=channel,
            bot_name=bot_name,
            created_by=created_by,
            expires_in_minutes=expires_in_minutes,
        )

    def link_external_user(
        self,
        *,
        channel: str,
        bot_name: str,
        raw_token: str,
        external_user_id: str,
        external_chat_id: Optional[str] = None,
    ) -> dict:
        token_row = self.db.consume_channel_link_token(raw_token, channel, bot_name)
        if not token_row:
            raise HTTPException(status_code=400, detail="Invalid or expired channel link token")
        user = self.db.get_user(token_row["user_id"])
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return self.db.save_channel_user_link(
            channel=channel,
            bot_name=bot_name,
            external_user_id=external_user_id,
            user_id=user.user_id,
            external_chat_id=external_chat_id,
        )

    def get_bound_user(self, *, channel: str, bot_name: str, external_user_id: str):
        link = self.db.get_channel_user_link(channel, bot_name, external_user_id)
        if not link or link.get("status") != "active":
            return None
        return self.db.get_user(link["user_id"])

    def get_or_create_conversation(
        self,
        *,
        channel: str,
        bot_name: str,
        conversation_key: str,
        user_id: str,
        external_chat_id: str,
        external_thread_id: Optional[str],
    ) -> dict:
        existing = self.db.get_channel_conversation(channel, bot_name, conversation_key)
        if existing:
            return existing
        session_suffix = external_thread_id or "0"
        session_label = f"{channel}:{bot_name}:{external_chat_id}:{session_suffix}"
        return self.db.save_channel_conversation(
            channel=channel,
            bot_name=bot_name,
            conversation_key=conversation_key,
            user_id=user_id,
            external_chat_id=external_chat_id,
            external_thread_id=external_thread_id,
            workspace_id="default",
            provider=ProviderType.CODEX.value,
            session_label=session_label,
        )

    def update_conversation_workspace(self, conversation: dict, workspace_id: str) -> dict:
        user = self.db.get_user(conversation["user_id"])
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        _root, normalized = self.inference.resolve_user_workspace(user.workspace_path, workspace_id)
        return self.db.save_channel_conversation(
            channel=conversation["channel"],
            bot_name=conversation["bot_name"],
            conversation_key=conversation["conversation_key"],
            user_id=conversation["user_id"],
            external_chat_id=conversation.get("external_chat_id"),
            external_thread_id=conversation.get("external_thread_id"),
            workspace_id=normalized,
            provider=conversation["provider"],
            session_label=conversation["session_label"],
        )

    def _project_service_for_user(self, user) -> ProjectService:
        return ProjectService(
            WorkspaceManager(
                self.db,
                workspaces_root=user.workspace_path,
                isolated_envs_root=self.settings.isolated_envs_root,
            )
        )

    def create_user_project(
        self,
        *,
        user_id: str,
        name: str,
        template: str = "default",
        default_provider: Optional[str] = None,
    ) -> dict:
        user = self.db.get_user(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        if template not in PROJECT_TEMPLATES:
            raise HTTPException(status_code=400, detail=f"Unsupported project template: {template}")
        result = self._project_service_for_user(user).create_project(
            name,
            template=template,
            default_provider=default_provider,
            created_by=f"channel:{user_id}",
        )
        return result.to_dict()

    def list_user_projects(self, user_id: str) -> list[dict]:
        user = self.db.get_user(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return self._project_service_for_user(user).list_projects()

    def get_user_project(self, user_id: str, name: str) -> Optional[dict]:
        user = self.db.get_user(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return self._project_service_for_user(user).get_project(name)

    def update_conversation_provider(self, conversation: dict, provider: str) -> dict:
        provider_enum = ProviderType(provider)
        return self.db.save_channel_conversation(
            channel=conversation["channel"],
            bot_name=conversation["bot_name"],
            conversation_key=conversation["conversation_key"],
            user_id=conversation["user_id"],
            external_chat_id=conversation.get("external_chat_id"),
            external_thread_id=conversation.get("external_thread_id"),
            workspace_id=conversation["workspace_id"],
            provider=provider_enum.value,
            session_label=conversation["session_label"],
        )

    def reset_conversation_session(self, conversation: dict) -> str:
        user = self.db.get_user(conversation["user_id"])
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        client_session_id = self.inference.user_session_id(
            user.user_id,
            conversation["workspace_id"],
            conversation["session_label"],
        )
        self.db.delete_session(client_session_id)
        return client_session_id

    def get_conversation_session_status(self, conversation: dict) -> dict:
        user = self.db.get_user(conversation["user_id"])
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        client_session_id = self.inference.user_session_id(
            user.user_id,
            conversation["workspace_id"],
            conversation["session_label"],
        )
        session = self.db.get_session(client_session_id)
        return {
            "client_session_id": client_session_id,
            "exists": session is not None,
            "status": session.status.value if session else "not_started",
            "backend_id": session.backend_id if session else None,
            "account_id": session.account_id if session else None,
            "cwd_path": session.cwd_path if session else None,
            "last_context_tokens": session.last_context_tokens if session else None,
            "updated_at": session.updated_at.isoformat() if session else None,
        }

    async def execute_conversation_turn(
        self,
        *,
        conversation: dict,
        text: str,
        attachments: Optional[list[AttachmentInput]] = None,
    ) -> ChannelTurnResult:
        user = self.db.get_user(conversation["user_id"])
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        api_key = self.inference.ensure_active_api_key(user.user_id, label=f"{conversation['channel']}-channel")
        provider = ProviderType(conversation["provider"])
        model = get_provider_default_model(provider, self.settings)
        options = UagOptions(
            provider=provider,
            workspace_id=conversation["workspace_id"],
            client_session_id=conversation["session_label"],
        )
        messages = [Message(role="user", content=text)]
        result, _workspace_root, workspace_id, materialized = await self.inference.execute_user_turn(
            model=model,
            messages=messages,
            options=options,
            user=user,
            api_key=api_key,
            attachments=attachments or [],
        )
        client_session_id = options.client_session_id or self.inference.user_session_id(
            user.user_id,
            workspace_id,
            conversation["session_label"],
        )
        return ChannelTurnResult(
            text=result.output,
            workspace_id=workspace_id,
            provider=provider.value,
            client_session_id=client_session_id,
            attachments=materialized,
            modified_files=result.modified_files,
            diff=result.diff,
        )
