from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

from amesh.config import Settings, resolve_provider_model
from amesh.core.models import Message, TurnResult, UagOptions
from amesh.database.manager import DatabaseManager
from amesh.orchestrator.engine import Orchestrator
from amesh.telemetry import record_event, start_span
from amesh.workspace.engine import WorkspaceEngine


_WORKSPACE_ID_RE = re.compile(r"^[A-Za-z0-9._/-]{1,120}$")


@dataclass
class AttachmentInput:
    filename: str
    content: bytes
    content_type: Optional[str] = None


class InferenceService:
    def __init__(self, db_manager: DatabaseManager, orchestrator: Orchestrator, settings: Settings):
        self.db = db_manager
        self.orchestrator = orchestrator
        self.settings = settings

    def normalize_workspace_id(self, workspace_id: Optional[str]) -> str:
        candidate = (workspace_id or "default").strip() or "default"
        if candidate.startswith("/") or "::" in candidate or not _WORKSPACE_ID_RE.fullmatch(candidate):
            raise HTTPException(status_code=400, detail="Invalid workspace_id")
        parts = [part for part in candidate.split("/") if part and part != "."]
        if not parts:
            return "default"
        if any(part == ".." for part in parts):
            raise HTTPException(status_code=400, detail="Invalid workspace_id")
        return "/".join(parts)

    def workspace_session_token(self, workspace_id: str) -> str:
        return workspace_id.replace("/", "__")

    def resolve_user_workspace(self, base_workspace_path: str, workspace_id: Optional[str]) -> tuple[str, str]:
        normalized = self.normalize_workspace_id(workspace_id)
        base_path = Path(base_workspace_path).expanduser().resolve()
        base_path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if normalized == "default":
            WorkspaceEngine(str(base_path)).ensure_git_repository()
            return str(base_path), normalized

        workspace_path = (base_path / normalized).resolve()
        if os.path.commonpath([str(base_path), str(workspace_path)]) != str(base_path):
            raise HTTPException(status_code=400, detail="Invalid workspace_id")
        workspace_path.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            workspace_path.chmod(0o700)
        except OSError:
            pass
        WorkspaceEngine(str(workspace_path)).ensure_git_repository()
        return str(workspace_path), normalized

    def user_session_id(self, user_id: str, workspace_id: str, session_label: Optional[str]) -> str:
        return f"{user_id}::{self.workspace_session_token(workspace_id)}::{session_label or 'default'}"

    def ensure_active_api_key(self, user_id: str, *, label: Optional[str] = None):
        from amesh.core.security import generate_api_key

        active_keys = self.db.list_active_api_keys(user_id)
        if active_keys:
            return active_keys[0]
        raw_key = generate_api_key()
        return self.db.save_api_key(user_id, raw_key, label=label)

    def sanitize_upload_name(self, filename: Optional[str], fallback_index: int) -> str:
        candidate = Path(filename or f"attachment-{fallback_index}").name.strip() or f"attachment-{fallback_index}"
        sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", candidate).strip(".-")
        return sanitized or f"attachment-{fallback_index}"

    def attachment_notice(self, attachments: list[dict[str, object]]) -> str:
        lines = [
            "Attached files are available in the workspace for this turn.",
            "Use the provided relative paths when reading or referencing them:",
        ]
        for item in attachments:
            content_type = item.get("content_type")
            lines.append(
                f"- {item['original_name']} -> {item['path']}"
                + (f" ({content_type})" if content_type else "")
            )
        return "\n".join(lines)

    def merge_attachment_message(self, messages: list[Message], attachments: list[dict[str, object]]) -> list[Message]:
        if not attachments:
            return messages
        notice = Message(role="system", content=self.attachment_notice(attachments))
        insert_at = 0
        while insert_at < len(messages) and getattr(messages[insert_at], "role", "") == "system":
            insert_at += 1
        return messages[:insert_at] + [notice] + messages[insert_at:]

    def materialize_attachments(
        self,
        workspace_root: Optional[str],
        messages: list[Message],
        attachments: list[AttachmentInput],
        *,
        session_label: Optional[str],
    ) -> list[dict[str, object]]:
        if not attachments:
            return []
        if not workspace_root:
            raise HTTPException(status_code=400, detail="workspace_root is required when uploading files")

        from uuid import uuid4

        upload_scope = re.sub(r"[^A-Za-z0-9._-]+", "-", session_label or uuid4().hex[:12]).strip(".-") or uuid4().hex[:12]
        attachments_root = Path(workspace_root) / ".uag" / "uploads" / upload_scope
        attachments_root.mkdir(parents=True, exist_ok=True)

        materialized: list[dict[str, object]] = []
        used_paths: set[str] = set()
        root_path = Path(workspace_root)
        for index, attachment in enumerate(attachments, start=1):
            filename = self.sanitize_upload_name(attachment.filename, index)
            destination = attachments_root / filename
            stem = destination.stem
            suffix = destination.suffix
            counter = 1
            while str(destination.relative_to(root_path)) in used_paths or destination.exists():
                destination = attachments_root / f"{stem}-{counter}{suffix}"
                counter += 1
            destination.write_bytes(attachment.content)
            relative_path = str(destination.relative_to(root_path))
            used_paths.add(relative_path)
            materialized.append(
                {
                    "original_name": attachment.filename or filename,
                    "path": relative_path,
                    "content_type": attachment.content_type,
                    "size_bytes": len(attachment.content),
                }
            )

        messages[:] = self.merge_attachment_message(messages, materialized)
        return materialized

    async def execute_user_turn(
        self,
        *,
        model: str,
        messages: list[Message],
        options: UagOptions,
        user,
        api_key,
        default_session_label: Optional[str] = None,
        attachments: Optional[list[AttachmentInput]] = None,
    ) -> tuple[TurnResult, str, str, list[dict[str, object]]]:
        async with start_span(
            "inference.user_turn",
            component="service.inference",
            db=self.db,
            attributes={
                "user_id": user.user_id,
                "provider": options.provider.value,
                "requested_workspace_id": options.workspace_id,
            },
        ):
            workspace_root, workspace_id = self.resolve_user_workspace(
                user.workspace_path,
                options.workspace_id,
            )
            
            # Ensure workspace exists in DB
            db_workspace = self.db.get_workspace_v2(workspace_id)
            if not db_workspace:
                from amesh.core.models import Workspace
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc)
                db_workspace = Workspace(
                    workspace_id=workspace_id,
                    name=workspace_id,
                    path=workspace_root,
                    user_id=user.user_id,
                    template="empty",
                    created_at=now,
                    updated_at=now,
                )
                self.db.save_workspace(db_workspace)

            options.workspace_root = workspace_root
            options.workspace_id = workspace_id
            options.user_id = user.user_id
            options.api_key_id = api_key.key_id
            options.client_session_id = self.user_session_id(
                user.user_id,
                workspace_id,
                options.client_session_id or default_session_label,
            )
            provider_model = resolve_provider_model(options.provider, model, self.settings)
            materialized = self.materialize_attachments(
                workspace_root,
                messages,
                attachments or [],
                session_label=options.client_session_id or default_session_label,
            )
            record_event(
                "inference.user_turn.bound",
                component="service.inference",
                db=self.db,
                attributes={
                    "user_id": user.user_id,
                    "workspace_id": workspace_id,
                    "client_session_id": options.client_session_id,
                    "provider_model": provider_model,
                    "attachments": len(materialized),
                },
            )
            result = await self.orchestrator.handle_request(
                options,
                messages,
                provider_model=provider_model,
                workspace_id=workspace_id,
            )
            return result, workspace_root, workspace_id, materialized
