import asyncio
import base64
import hashlib
import hmac
from pathlib import Path
from fastapi import FastAPI, HTTPException, APIRouter, Depends, Query, Request, Security, status, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, OAuth2PasswordBearer
import json
import os
import re
from asyncio import Queue
from collections import deque
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import List, Optional, Any, AsyncIterator
from pydantic import BaseModel, ConfigDict, Field, AliasChoices, ValidationError, model_validator
from uuid import uuid4

from amesh.core.models import (
    Message,
    UagOptions,
    TurnResult,
    Session,
    SessionStatus,
    UserStatus,
    ProviderType,
    Workspace,
    Task,
)
from amesh.config import get_config_path, get_settings, resolve_provider_model, get_telegram_bot_config
from amesh.orchestrator.engine import Orchestrator
from amesh.database.manager import DatabaseManager
from amesh.core.security import generate_api_key, hash_api_key
from amesh.runtime_log_store import RuntimeLogStore
from amesh.cli_run_store import CliRunStore
from amesh.file_tail_hub import FileTailHub
from amesh.channels.service import ChannelService
from amesh.channels.telegram import TelegramChannelAdapter, TelegramPollingManager, register_telegram_bot_commands
from amesh.telemetry import current_request_id, current_trace_id, record_event, start_span, start_trace
from amesh.workspace.engine import WorkspaceEngine
from amesh.workspace.manager import WorkspaceManager
from amesh.workspace.service import WORKSPACE_TEMPLATES, WorkspaceService
from amesh.services.inference import AttachmentInput, InferenceService
from amesh.version import check_for_update, get_version

# --- Models for Management API ---

class ManagementResponse(BaseModel):
    ok: bool
    data: Optional[Any] = None
    meta: Optional[dict] = None
    error: Optional[dict] = None

settings = get_settings()
telegram_polling_manager: Optional[TelegramPollingManager] = None
_VERSION_CHECK_CACHE: dict[str, Any] = {}

TAG_INFERENCE = "Inference"
TAG_USER_SELF_SERVICE = "User Self-Service"
TAG_PLAYGROUND = "Playground"
TAG_MANAGEMENT_AUTH = "Management Authentication"
TAG_MANAGEMENT_USERS = "Management Users"
TAG_MANAGEMENT_WORKSPACES = "Management Workspaces"
TAG_MANAGEMENT_WORKSPACES_V2 = "Management Workspaces V2"
TAG_MANAGEMENT_SESSIONS = "Management Sessions"
TAG_MANAGEMENT_OBSERVABILITY = "Management Observability"
TAG_MANAGEMENT_AUDIT = "Management Audit"

OPENAPI_TAGS = [
    {
        "name": TAG_INFERENCE,
        "description": "OpenAI-compatible inference entrypoints.",
    },
    {
        "name": TAG_USER_SELF_SERVICE,
        "description": "User self-service endpoints for keys, sessions, and workspace state.",
    },
    {
        "name": TAG_PLAYGROUND,
        "description": "Operator playground flows backed by the management plane.",
    },
    {
        "name": TAG_MANAGEMENT_AUTH,
        "description": "Operator authentication and token lifecycle endpoints.",
    },
    {
        "name": TAG_MANAGEMENT_USERS,
        "description": "Provisioning and lifecycle management for end users.",
    },
    {
        "name": TAG_MANAGEMENT_WORKSPACES,
        "description": "Inventory and lifecycle management for provisioned workspaces.",
    },
    {
        "name": TAG_MANAGEMENT_WORKSPACES_V2,
        "description": "User-facing workspaces stored in the database.",
    },
    {
        "name": TAG_MANAGEMENT_SESSIONS,
        "description": "Inspection and control of persisted runtime sessions.",
    },
    {
        "name": TAG_MANAGEMENT_OBSERVABILITY,
        "description": "Health checks and metrics for operators.",
    },
    {
        "name": TAG_MANAGEMENT_AUDIT,
        "description": "Immutable audit-log access for management mutations.",
    },
]

# --- Auth Setup ---

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/management/v1/auth/token", auto_error=False)
operator_bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="Operator Bearer",
    description="Use an operator access token from /management/v1/auth/token or the configured operator passkey.",
)
user_api_key_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="User API Key",
    description="Use a provisioned user API key that starts with uagk_.",
)

OPERATOR_ACCESS_COOKIE = "uag_op_access"
OPERATOR_REFRESH_COOKIE = "uag_op_refresh"


_DOTENV_CACHE: dict[str, Optional[str]] = {}


def _dotenv_value(name: str) -> Optional[str]:
    if name in _DOTENV_CACHE:
        return _DOTENV_CACHE[name]

    env_paths = []
    config_env = get_config_path().parent / ".env"
    cwd_env = Path.cwd() / ".env"
    if config_env not in env_paths:
        env_paths.append(config_env)
    if cwd_env not in env_paths:
        env_paths.append(cwd_env)
    for env_path in env_paths:
        if not env_path.exists():
            continue
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, value = stripped.split("=", 1)
                if key.strip() != name:
                    continue
                value = value.strip()
                if value[:1] == value[-1:] and value[:1] in {"'", '"'}:
                    value = value[1:-1]
                _DOTENV_CACHE[name] = value
                return value
        except Exception:
            continue
    _DOTENV_CACHE[name] = None
    return None


_OPERATOR_PASSKEY_CACHE: Optional[str] = None


def clear_auth_caches():
    """Clear the internal authentication passkey caches (used primarily for testing)."""
    global _OPERATOR_PASSKEY_CACHE
    _OPERATOR_PASSKEY_CACHE = None
    _DOTENV_CACHE.clear()


def _operator_passkey() -> str:
    global _OPERATOR_PASSKEY_CACHE
    if _OPERATOR_PASSKEY_CACHE is not None:
        return _OPERATOR_PASSKEY_CACHE

    pk = (
        os.getenv("UAG_MGMT_SECRET")
        or os.getenv("API_TOKEN")
        or _dotenv_value("UAG_MGMT_SECRET")
        or _dotenv_value("API_TOKEN")
        or settings.secret_key
    )
    # We only cache if it's not the default settings.secret_key 
    # OR if it's explicitly loaded from env/dotenv.
    # For now, let's just cache it once it's resolved.
    _OPERATOR_PASSKEY_CACHE = pk
    return pk


def _encode_payload(payload: dict) -> str:
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ).decode().rstrip("=")
    signature = hmac.new(_operator_passkey().encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def _decode_payload(token: str) -> dict:
    try:
        encoded, signature = token.split(".", 1)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    expected = hmac.new(_operator_passkey().encode(), encoded.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    padding = "=" * (-len(encoded) % 4)
    return json.loads(base64.urlsafe_b64decode(encoded + padding))


def _validate_operator_token(token: str) -> dict:
    payload = _decode_payload(token)
    if payload.get("scope") != "operator":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if payload.get("exp", 0) < int(datetime.now(timezone.utc).timestamp()) - 300: # 5 minute buffer for clock skew
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    now = datetime.now(timezone.utc)
    expires_at = now + (expires_delta or timedelta(hours=8))
    payload = {
        **data,
        "token_type": data.get("token_type", "access"),
        "exp": int(expires_at.timestamp()),
        "iat": int(now.timestamp()),
    }
    return _encode_payload(payload)


def _set_operator_cookies(response: JSONResponse, *, access_token: str, refresh_token: str) -> None:
    # Use SameSite=Strict to reduce CSRF risk for cookie-authenticated management endpoints.
    # Secure is disabled by default so local http:// dev works; production should terminate TLS.
    response.set_cookie(
        OPERATOR_ACCESS_COOKIE,
        access_token,
        httponly=True,
        samesite="strict",
        secure=False,
        path="/",
        max_age=8 * 60 * 60,
    )
    response.set_cookie(
        OPERATOR_REFRESH_COOKIE,
        refresh_token,
        httponly=True,
        samesite="strict",
        secure=False,
        path="/management/v1/auth",
        max_age=7 * 24 * 60 * 60,
    )


def _clear_operator_cookies(response: JSONResponse) -> None:
    response.delete_cookie(OPERATOR_ACCESS_COOKIE, path="/")
    response.delete_cookie(OPERATOR_REFRESH_COOKIE, path="/management/v1/auth")


async def get_current_operator(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(operator_bearer_scheme),
):
    # Fallback for when Depends(oauth2_scheme) doesn't find the token
    # or we want to allow the static API_TOKEN/passkey to be used as a bearer token directly.
    auth_header = request.headers.get("Authorization", "")
    bearer_token = credentials.credentials.strip() if isinstance(credentials, HTTPAuthorizationCredentials) else None
    if auth_header.startswith("Bearer "):
        bearer_token = auth_header.removeprefix("Bearer ").strip()
    cookie_token = request.cookies.get(OPERATOR_ACCESS_COOKIE)
    if bearer_token:
        if bearer_token == _operator_passkey():
            return {"id": "svc-account-01", "scope": "operator"}
        if not token:
            token = bearer_token
    if not token and cookie_token:
        token = cookie_token.strip()

    if not token:
        # print(f"DEBUG: get_current_operator failed - no token. auth_header: {auth_header[:20]}...")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token missing",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = _validate_operator_token(token)
        return {"id": payload.get("sub", "svc-account-01"), "scope": "operator"}
    except HTTPException as e:
        # Re-raise with same detail to avoid losing context
        # print(f"DEBUG: get_current_operator failed - validation error: {e.detail}")
        raise
    except Exception as e:
        # print(f"DEBUG: get_current_operator failed - unexpected error: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token validation failed: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _token_is_user_key(token: str) -> bool:
    return token.startswith("uagk_")


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(user_api_key_scheme),
):
    authorization = request.headers.get("authorization", "")
    token = credentials.credentials.strip() if isinstance(credentials, HTTPAuthorizationCredentials) else None
    if not token and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not _token_is_user_key(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    key_hash = hash_api_key(token)
    api_key = db_manager.get_api_key_by_hash(key_hash)
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db_manager.get_user(api_key.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    if api_key.status != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key revoked")
    if api_key.expires_at and api_key.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key expired")
    if user.status == UserStatus.SUSPENDED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User suspended")
    if user.status == UserStatus.DELETED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User deleted")

    db_manager.touch_api_key(api_key.key_id)
    return {"user": user, "api_key": api_key}


def _ensure_user_workspace(user_id: str) -> str:
    root = Path(settings.workspaces_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    workspace_path = root / user_id
    workspace_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        workspace_path.chmod(0o700)
    except OSError:
        pass
    WorkspaceEngine(str(workspace_path)).ensure_git_repository()
    return str(workspace_path)


_WORKSPACE_ID_RE = re.compile(r"^[A-Za-z0-9._/-]{1,120}$")


def _normalize_workspace_id(workspace_id: Optional[str]) -> str:
    candidate = (workspace_id or "default").strip() or "default"
    if candidate.startswith("/") or "::" in candidate or not _WORKSPACE_ID_RE.fullmatch(candidate):
        raise HTTPException(status_code=400, detail="Invalid workspace_id")
    parts = [part for part in candidate.split("/") if part and part != "."]
    if not parts:
        return "default"
    if any(part == ".." for part in parts):
        raise HTTPException(status_code=400, detail="Invalid workspace_id")
    return "/".join(parts)


def _workspace_session_token(workspace_id: str) -> str:
    return workspace_id.replace("/", "__")


def _resolve_user_workspace(base_workspace_path: str, workspace_id: Optional[str]) -> tuple[str, str]:
    return _inference_service().resolve_user_workspace(base_workspace_path, workspace_id)


def _user_session_id(user_id: str, workspace_id: str, session_label: Optional[str]) -> str:
    return _inference_service().user_session_id(user_id, workspace_id, session_label)


_DASHBOARD_ADMIN_EMAIL = "dashboard-admin@amesh.local"


def _ensure_active_api_key(user_id: str, *, label: Optional[str] = None):
    return _inference_service().ensure_active_api_key(user_id, label=label)


def _ensure_dashboard_admin_user():
    user = db_manager.get_user_by_email(_DASHBOARD_ADMIN_EMAIL)
    if user:
        updated = False
        if user.status != UserStatus.ACTIVE:
            user.status = UserStatus.ACTIVE
            updated = True
        if user.max_concurrency != 1:
            user.max_concurrency = 1
            updated = True
        if updated:
            user.updated_at = datetime.now(timezone.utc)
            db_manager.save_user(user)
    else:
        user_id = db_manager._generate_ulid_like("uag_usr")
        workspace_path = _ensure_user_workspace(user_id)
        user = db_manager.create_user(
            email=_DASHBOARD_ADMIN_EMAIL,
            display_name="Dashboard Admin",
            workspace_path=workspace_path,
            created_by="system:dashboard",
            max_api_keys=1,
            max_concurrency=1,
            user_id=user_id,
        )
    api_key = _ensure_active_api_key(user.user_id, label="playground-default")
    return user, api_key


async def _execute_user_bound_chat(
    chat_request: "ChatCompletionRequest",
    *,
    options,
    user,
    api_key,
    default_session_label: Optional[str] = None,
    uploaded_files: Optional[list[UploadFile]] = None,
):
    # options is passed by caller to ensure modifications are visible
    attachment_inputs = await _attachment_inputs_from_uploads(uploaded_files or [])
    result, workspace_root, workspace_id, attachments = await _inference_service().execute_user_turn(
        model=chat_request.model,
        messages=chat_request.messages,
        options=options,
        user=user,
        api_key=api_key,
        default_session_label=default_session_label,
        attachments=attachment_inputs,
    )
    return result, workspace_root, workspace_id, attachments


async def _execute_user_bound_chat_legacy(
    chat_request: "ChatCompletionRequest",
    *,
    user,
    api_key,
    default_session_label: Optional[str] = None,
    uploaded_files: Optional[list[UploadFile]] = None,
):
    options = chat_request.normalized_options()
    return await _execute_user_bound_chat(
        chat_request,
        options=options,
        user=user,
        api_key=api_key,
        default_session_label=default_session_label,
        uploaded_files=uploaded_files,
    )


async def _execute_user_bound_chat_v1(
    chat_request: "ChatCompletionRequest",
    *,
    options,
    user,
    api_key,
    default_session_label: Optional[str] = None,
    uploaded_files: Optional[list[UploadFile]] = None,
):
    return await _execute_user_bound_chat(
        chat_request,
        options=options,
        user=user,
        api_key=api_key,
        default_session_label=default_session_label,
        uploaded_files=uploaded_files,
    )


async def _execute_user_bound_chat_v2(
    chat_request: "ChatCompletionRequest",
    *,
    options,
    user,
    api_key,
    default_session_label: Optional[str] = None,
    uploaded_files: Optional[list[UploadFile]] = None,
):
    return await _execute_user_bound_chat(
        chat_request,
        options=options,
        user=user,
        api_key=api_key,
        default_session_label=default_session_label,
        uploaded_files=uploaded_files,
    )


async def _parse_chat_request(http_request: Request) -> tuple["ChatCompletionRequest", list[UploadFile]]:
    content_type = (http_request.headers.get("content-type") or "").lower()
    if content_type.startswith("multipart/form-data"):
        form = await http_request.form()
        payload = form.get("payload") or form.get("request") or form.get("chat_request")
        if not isinstance(payload, str) or not payload.strip():
            raise HTTPException(status_code=400, detail="Multipart chat requests require a JSON 'payload' field")
        try:
            chat_request = ChatCompletionRequest.model_validate_json(payload)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=json.loads(exc.json())) from exc
        uploads = [value for _, value in form.multi_items() if _is_upload_file(value)]
        return chat_request, uploads

    try:
        body = await http_request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
    try:
        return ChatCompletionRequest.model_validate(body), []
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=json.loads(exc.json())) from exc


def _attachment_notice(attachments: list[dict[str, Any]]) -> str:
    lines = [
        "Attached files are available in the workspace for this turn.",
        "Use the provided relative paths when reading or referencing them:",
    ]
    for item in attachments:
        lines.append(
            f"- {item['original_name']} -> {item['path']}"
            + (f" ({item['content_type']})" if item.get("content_type") else "")
        )
    return "\n".join(lines)


def _merge_attachment_message(messages: list[Message], attachments: list[dict[str, Any]]) -> list[Message]:
    if not attachments:
        return messages
    notice = Message(role="system", content=_attachment_notice(attachments))
    insert_at = 0
    while insert_at < len(messages) and getattr(messages[insert_at], "role", "") == "system":
        insert_at += 1
    return messages[:insert_at] + [notice] + messages[insert_at:]


def _sanitize_upload_name(filename: Optional[str], fallback_index: int) -> str:
    candidate = Path(filename or f"attachment-{fallback_index}").name.strip() or f"attachment-{fallback_index}"
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", candidate).strip(".-")
    return sanitized or f"attachment-{fallback_index}"


def _is_upload_file(value: Any) -> bool:
    return hasattr(value, "filename") and callable(getattr(value, "read", None))


async def _attachment_inputs_from_uploads(uploaded_files: list[UploadFile]) -> list[AttachmentInput]:
    attachments: list[AttachmentInput] = []
    for index, upload in enumerate(uploaded_files, start=1):
        filename = _sanitize_upload_name(getattr(upload, "filename", None), index)
        content = await upload.read()
        attachments.append(
            AttachmentInput(
                filename=getattr(upload, "filename", filename) or filename,
                content=content,
                content_type=getattr(upload, "content_type", None),
            )
        )
    return attachments


async def _materialize_chat_uploads(
    workspace_root: Optional[str],
    messages: list[Message],
    uploaded_files: list[UploadFile],
    *,
    session_label: Optional[str],
) -> list[dict[str, Any]]:
    if not uploaded_files:
        return []
    if not workspace_root:
        raise HTTPException(status_code=400, detail="workspace_root is required when uploading files")

    upload_scope = re.sub(r"[^A-Za-z0-9._-]+", "-", session_label or uuid4().hex[:12]).strip(".-") or uuid4().hex[:12]
    attachments_root = Path(workspace_root) / ".uag" / "uploads" / upload_scope
    attachments_root.mkdir(parents=True, exist_ok=True)

    attachments: list[dict[str, Any]] = []
    used_paths: set[str] = set()
    for index, upload in enumerate(uploaded_files, start=1):
        filename = _sanitize_upload_name(getattr(upload, "filename", None), index)
        destination = attachments_root / filename
        stem = destination.stem
        suffix = destination.suffix
        counter = 1
        while str(destination.relative_to(Path(workspace_root))) in used_paths or destination.exists():
            destination = attachments_root / f"{stem}-{counter}{suffix}"
            counter += 1
        content = await upload.read()
        destination.write_bytes(content)
        relative_path = str(destination.relative_to(Path(workspace_root)))
        used_paths.add(relative_path)
        attachments.append(
            {
                "original_name": getattr(upload, "filename", filename) or filename,
                "path": relative_path,
                "content_type": getattr(upload, "content_type", None),
                "size_bytes": len(content),
            }
        )

    messages[:] = _merge_attachment_message(messages, attachments)
    return attachments


def _raise_chat_runtime_error(exc: RuntimeError):
    detail = str(exc)
    lowered = detail.lower()
    if "No available account for provider" in detail:
        raise HTTPException(status_code=503, detail=detail)
    if "User concurrency limit reached" in detail:
        raise HTTPException(status_code=429, detail=detail)
    if (
        "exhausted your capacity on this model" in lowered
        or "quota will reset" in lowered
        or "quota exhausted" in lowered
        or "quota exceeded" in lowered
        or "capacity on this model" in lowered
    ):
        raise HTTPException(status_code=429, detail=detail)
    if "id_token" in lowered and "missing" in lowered:
        raise HTTPException(status_code=400, detail=detail)
    if "not logged in on the local system" in lowered or "is not installed on the local system" in lowered:
        raise HTTPException(status_code=503, detail=detail)
    if detail.startswith(("Codex ", "Gemini ", "OpenCode ")):
        raise HTTPException(status_code=502, detail=detail)
    raise exc


def _serialize_api_key(key):
    return {
        "key_id": key.key_id,
        "user_id": key.user_id,
        "key_prefix": key.key_prefix,
        "label": key.label,
        "status": key.status,
        "last_used_at": key.last_used_at.isoformat() if key.last_used_at else None,
        "expires_at": key.expires_at.isoformat() if key.expires_at else None,
        "created_at": key.created_at.isoformat(),
        "revoked_at": key.revoked_at.isoformat() if key.revoked_at else None,
    }


def _session_binding_map(session_ids: list[str]) -> dict[str, dict]:
    if not session_ids:
        return {}
    placeholders = ", ".join("?" for _ in session_ids)
    with db_manager._get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT
                sessions.client_session_id,
                sessions.user_id,
                sessions.api_key_id,
                users.display_name AS user_display_name,
                users.email AS user_email,
                api_keys.label AS api_key_label,
                api_keys.key_prefix
            FROM sessions
            LEFT JOIN users ON users.user_id = sessions.user_id
            LEFT JOIN api_keys ON api_keys.key_id = sessions.api_key_id
            WHERE sessions.client_session_id IN ({placeholders})
            """,
            session_ids,
        ).fetchall()
    return {
        row["client_session_id"]: {
            "user_id": row["user_id"],
            "user_display_name": row["user_display_name"],
            "user_email": row["user_email"],
            "api_key_id": row["api_key_id"],
            "api_key_label": row["api_key_label"],
            "api_key_prefix": row["key_prefix"],
        }
        for row in rows
    }


def _serialize_session(session: Session, binding: Optional[dict] = None) -> dict:
    binding = binding or {}
    return {
        "client_session_id": session.client_session_id,
        "backend_id": session.backend_id,
        "provider": session.provider.value,
        "user_id": binding.get("user_id", session.user_id),
        "user_display_name": binding.get("user_display_name"),
        "user_email": binding.get("user_email"),
        "api_key_id": binding.get("api_key_id", session.api_key_id),
        "api_key_label": binding.get("api_key_label"),
        "api_key_prefix": binding.get("api_key_prefix"),
        "cwd_path": session.cwd_path,
        "status": session.status.value,
        "created_at": int(session.created_at.replace(tzinfo=timezone.utc).timestamp()),
        "updated_at": int(session.updated_at.replace(tzinfo=timezone.utc).timestamp()),
        "expires_at": int(session.expires_at.replace(tzinfo=timezone.utc).timestamp()),
    }


def _workspace_manager() -> WorkspaceManager:
    return WorkspaceManager(
        db_manager,
        workspaces_root=settings.workspaces_root,
    )


def _workspace_service_v2() -> WorkspaceService:
    return WorkspaceService(_workspace_manager(), db_manager)


def _inference_service() -> InferenceService:
    return InferenceService(db_manager, orchestrator, settings)


def _channel_service() -> ChannelService:
    return ChannelService(db_manager, _inference_service(), settings)


def _runtime_log_store() -> RuntimeLogStore:
    runtime_root = Path(settings.runtime_log_root).expanduser()
    if not runtime_root.is_absolute():
        runtime_root = Path(settings.logs_root).expanduser().resolve() / runtime_root
    return RuntimeLogStore(str(runtime_root))


def _cli_run_store() -> CliRunStore:
    return CliRunStore(settings=settings)


_tail_hub: FileTailHub | None = None


def _file_tail_hub() -> FileTailHub:
    global _tail_hub
    if _tail_hub is None:
        _tail_hub = FileTailHub()
    return _tail_hub


def _telegram_adapter(bot_name: str) -> TelegramChannelAdapter:
    bot = get_telegram_bot_config(bot_name, settings)
    if bot is None:
        raise HTTPException(status_code=404, detail="Telegram bot not found")
    return TelegramChannelAdapter(
        _channel_service(),
        channel_config=settings.channels.telegram,
        bot_config=bot,
    )


def _telegram_polling_manager() -> Optional[TelegramPollingManager]:
    if not settings.channels.telegram.enabled or settings.channels.telegram.receive_mode != "polling":
        return None
    adapters: list[TelegramChannelAdapter] = []
    for bot in settings.channels.telegram.bots:
        if not bot.enabled:
            continue
        adapters.append(
            TelegramChannelAdapter(
                _channel_service(),
                channel_config=settings.channels.telegram,
                bot_config=bot,
            )
        )
    if not adapters:
        return None
    return TelegramPollingManager(adapters)


def _telegram_adapters_for_enabled_bots() -> list[TelegramChannelAdapter]:
    if not settings.channels.telegram.enabled:
        return []
    adapters: list[TelegramChannelAdapter] = []
    for bot in settings.channels.telegram.bots:
        if not bot.enabled:
            continue
        adapters.append(
            TelegramChannelAdapter(
                _channel_service(),
                channel_config=settings.channels.telegram,
                bot_config=bot,
            )
        )
    return adapters


def _validate_direct_workspace_root(workspace_root: Optional[str]) -> str:
    if not workspace_root or not workspace_root.strip():
        raise HTTPException(status_code=400, detail="workspace_root is required")
    try:
        return str(_workspace_manager().validate_inference_workspace(workspace_root))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="workspace_root not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="workspace_access_denied") from exc


def _encode_workspace_id(workspace_path: str) -> str:
    return base64.urlsafe_b64encode(workspace_path.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_workspace_id(workspace_id: str) -> str:
    padding = "=" * (-len(workspace_id) % 4)
    try:
        return base64.urlsafe_b64decode(f"{workspace_id}{padding}".encode("ascii")).decode("utf-8")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid workspace id") from exc


def _serialize_workspace_user(user, *, owner: bool = False, summary: Optional[dict] = None) -> dict:
    summary = summary or {}
    return {
        "user_id": user.user_id,
        "display_name": user.display_name,
        "email": user.email,
        "status": user.status.value if hasattr(user.status, "value") else user.status,
        "workspace_path": user.workspace_path,
        "owner": owner,
        "active_sessions": summary.get("active_sessions", db_manager.count_user_sessions(user.user_id)),
        "active_keys": summary.get("active_keys", len([k for k in db_manager.list_api_keys(user.user_id) if k.status == "active"])),
    }


def _serialize_workspace(record: dict, *, include_details: bool = False) -> dict:
    owner_ids = {user.user_id for user in record["owners"]}
    user_summaries = _user_summary_map([user.user_id for user in record["users"]])
    session_ids = [session.client_session_id for session in record["sessions"]]
    bindings = _session_binding_map(session_ids)
    payload = {
        "workspace_id": _encode_workspace_id(record["path"]),
        "name": record["name"],
        "project": record.get("project"),
        "path": record["path"],
        "relative_path": record["relative_path"],
        "exists": record["exists"],
        "scope": record["scope"],
        "git": record["git"],
        "bound_sessions_count": len(record["sessions"]),
        "bound_users_count": len(record["users"]),
        "owners": [
            _serialize_workspace_user(user, owner=True, summary=user_summaries.get(user.user_id))
            for user in record["owners"]
        ],
    }
    if include_details:
        payload["users"] = [
            _serialize_workspace_user(
                user,
                owner=user.user_id in owner_ids,
                summary=user_summaries.get(user.user_id),
            )
            for user in record["users"]
        ]
        payload["sessions"] = [
            _serialize_session(session, bindings.get(session.client_session_id))
            for session in record["sessions"]
        ]
    return payload


def _build_chat_completion_response(model: str, options: UagOptions, result: TurnResult, extra_extensions: Optional[dict] = None):
    return {
        "id": f"chatcmpl-{result.backend_id}",
        "object": "chat.completion",
        "created": int(datetime.now().timestamp()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": result.output},
            "finish_reason": result.finish_reason
        }],
	        "extensions": {
	            "modified_files": result.modified_files,
	            "diff": result.diff,
	            "actions": result.actions,
	            "dirty": result.dirty,
	            "client_session_id": options.client_session_id,
	            "workspace_id": options.workspace_id,
	            **(extra_extensions or {}),
	        }
	    }

# --- App Initialization ---

app = FastAPI(
    title="Unified Agent Gateway (UAG)",
    openapi_tags=OPENAPI_TAGS,
)
db_manager = DatabaseManager(settings.database_path)
orchestrator = Orchestrator(db_manager)
management_router = APIRouter(prefix="/management/v1", dependencies=[Depends(get_current_operator)])
auth_router = APIRouter(prefix="/management/v1/auth")
user_router = APIRouter(prefix="/v1/user")
channel_router = APIRouter(prefix="/channels")

AUDIT_EVENTS = deque(maxlen=500)
RUNTIME_LOGS = deque(maxlen=1000)

_audit_log_store = None


def _get_audit_log_store():
    global _audit_log_store
    if _audit_log_store is None:
        from pathlib import Path
        from amesh.config import get_settings
        settings = get_settings()
        root = Path(settings.logs_root).expanduser().resolve() / settings.audit_log_root
        from amesh.audit_log_store import AuditLogStore
        _audit_log_store = AuditLogStore(str(root))
    return _audit_log_store


def emit_audit_event(actor: str, action: str, target_type: str, target_id: str,
                   before: Optional[dict] = None, after: Optional[dict] = None):
    import uuid
    ts = int(datetime.now(timezone.utc).timestamp())
    
    def _serialize(obj):
        if obj is None:
            return None
        if isinstance(obj, dict):
            return {k: _serialize(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_serialize(v) for v in obj]
        if isinstance(obj, datetime):
            return obj.isoformat()
        if hasattr(obj, 'isoformat'):
            return obj.isoformat()
        return obj
    
    event = {
        "id": f"aud_{uuid.uuid4().hex[:12]}",
        "actor": actor,
        "action": action,
        "target_type": target_type,
        "target_id": target_id,
        "before": _serialize(before),
        "after": _serialize(after),
        "timestamp": ts,
    }
    AUDIT_EVENTS.append(event)
    _get_audit_log_store().append(event)


def emit_runtime_log(log: dict):
    RUNTIME_LOGS.append(log)


from amesh.logging_setup import register_runtime_log_emitter
register_runtime_log_emitter(emit_runtime_log)


@app.on_event("startup")
async def startup_event():
    setup_dashboard(app)
    global telegram_polling_manager
    await register_telegram_bot_commands(_telegram_adapters_for_enabled_bots())
    manager = _telegram_polling_manager()
    telegram_polling_manager = manager
    if manager is not None:
        await manager.start()


@app.on_event("shutdown")
async def shutdown_channel_workers():
    global telegram_polling_manager
    manager = telegram_polling_manager
    telegram_polling_manager = None
    if manager is not None:
        await manager.stop()

# --- Common Helper ---

def envelope(data: Any = None, meta: dict = None):
    return {
        "ok": True,
        "data": data,
        "meta": {
            "request_id": current_request_id() or f"req_{uuid4().hex[:12]}",
            "trace_id": current_trace_id(),
            "timestamp": datetime.now().isoformat(),
            **(meta or {})
        }
    }


DASHBOARD_POLL_HEADER = "x-amesh-dashboard-poll"


def _is_truthy_header(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _is_quiet_dashboard_poll(request: Request) -> bool:
    if request.method.upper() != "GET":
        return False
    if not _is_truthy_header(request.headers.get(DASHBOARD_POLL_HEADER)):
        return False
    return request.url.path.startswith("/management/v1/")


async def _handle_quiet_dashboard_poll(request: Request, call_next, request_id: str):
    path = request.url.path
    started = perf_counter()
    request.state.request_id = request_id
    request.state.trace_id = None
    try:
        response = await call_next(request)
    except Exception as exc:
        record_event(
            "http.dashboard_poll.failed",
            component="gateway.http",
            db=db_manager,
            level="ERROR",
            status="error",
            attributes={
                "method": request.method,
                "path": path,
                "duration_ms": round((perf_counter() - started) * 1000, 2),
                "error": str(exc),
                "exception_type": exc.__class__.__name__,
            },
        )
        raise

    response.headers["X-Trace-Id"] = ""
    response.headers["X-Request-Id"] = request_id
    if response.status_code >= 500:
        record_event(
            "http.dashboard_poll.failed",
            component="gateway.http",
            db=db_manager,
            level="ERROR",
            status="error",
            attributes={
                "method": request.method,
                "path": path,
                "status_code": response.status_code,
                "duration_ms": round((perf_counter() - started) * 1000, 2),
            },
        )
    return response


@app.middleware("http")
async def telemetry_http_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-Id") or f"req_{uuid4().hex[:12]}"
    path = request.url.path
    if _is_quiet_dashboard_poll(request):
        return await _handle_quiet_dashboard_poll(request, call_next, request_id)

    async with start_trace(
        "http.request",
        component="gateway.http",
        db=db_manager,
        request_id=request_id,
        attributes={"method": request.method, "path": path},
    ) as span:
        request.state.request_id = request_id
        request.state.trace_id = span.trace_id
        started = perf_counter()
        record_event(
            "http.request.received",
            component="gateway.http",
            db=db_manager,
            attributes={"method": request.method, "path": path},
        )
        try:
            response = await call_next(request)
        except Exception as exc:
            record_event(
                "http.request.failed",
                component="gateway.http",
                db=db_manager,
                level="ERROR",
                status="error",
                attributes={
                    "method": request.method,
                    "path": path,
                    "duration_ms": round((perf_counter() - started) * 1000, 2),
                    "error": str(exc),
                    "exception_type": exc.__class__.__name__,
                },
            )
            raise
        response.headers["X-Trace-Id"] = span.trace_id or ""
        response.headers["X-Request-Id"] = request_id
        record_event(
            "http.request.completed",
            component="gateway.http",
            db=db_manager,
            status="ok",
            attributes={
                "method": request.method,
                "path": path,
                "status_code": response.status_code,
                "duration_ms": round((perf_counter() - started) * 1000, 2),
            },
        )
        return response


def _serialize_user(user, summary: Optional[dict] = None) -> dict:
    summary = summary or {}
    return {
        "user_id": user.user_id,
        "email": user.email,
        "display_name": user.display_name,
        "status": user.status.value if hasattr(user.status, "value") else user.status,
        "workspace_path": user.workspace_path,
        "workspace_strategy": "base-plus-workspace-id",
        "created_at": user.created_at.isoformat(),
        "created_by": user.created_by,
        "updated_at": user.updated_at.isoformat(),
        "api_key_policy": "single-active-key",
        "max_concurrency": user.max_concurrency,
        "active_keys": summary.get("active_keys", len([k for k in db_manager.list_api_keys(user.user_id) if k.status == "active"])),
        "active_sessions": summary.get("active_sessions", db_manager.count_user_sessions(user.user_id)),
        "summary": summary,
    }

def _serialize_activity(row: dict) -> dict:
    timestamp = row.get("timestamp")
    return {
        "turn_id": row.get("turn_id"),
        "session_id": row.get("client_session_id"), # In activity rows, it is client_session_id
        "client_session_id": row.get("client_session_id"),
        "user_id": row.get("user_id"),
        "api_key_label": row.get("api_key_label") or "primary",
        "provider": row.get("provider"),
        "model": row.get("model"),
        "duration_ms": row.get("duration_ms", 0),
        "timestamp": datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat() if timestamp else None,
        "status": row.get("status"),
    }


def _user_summary_map(user_ids: list[str]) -> dict[str, dict]:
    result = {}
    for uid in user_ids:
        u = db_manager.get_user(uid)
        if u:
            result[uid] = {"user_id": u.user_id, "email": u.email, "display_name": u.display_name}
    return result


def _page_meta(items: list, cursor_field: str) -> dict:
    if not items:
        return {"cursor": None, "has_more": False}
    return {
        "cursor": items[-1].get(cursor_field) if isinstance(items[-1], dict) else getattr(items[-1], cursor_field, None),
        "has_more": len(items) > 0,
    }

# --- Inference API (SRDS §10) ---

class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "model": "uag-codex-v5",
                    "messages": [
                        {
                            "role": "user",
                            "content": "Review the auth module and suggest improvements.",
                        }
                    ],
                    "provider": "codex",
                    "workspace_id": "project-a",
                    "client_session_id": "thread-1",
                }
            ]
        }
    )

    model: str = Field(
        description=(
            "Client-facing model identifier. Send a provider runtime model to target it directly, or send a "
            "`uag-*` alias to use that provider's configured default model."
        )
    )
    messages: List[Message] = Field(description="OpenAI-compatible conversation messages.")
    provider: ProviderType = Field(
        description=(
            "Target provider runtime. For verified user API keys, most clients only need this plus optional "
            "`workspace_id` and `client_session_id`."
        )
    )
    workspace_root: Optional[str] = Field(
        default=None,
        description=(
            "Operator/internal only. Absolute workspace path for direct operator requests. "
            "Verified user-key requests should omit this because the gateway resolves the user's bound workspace root."
        ),
    )
    workspace_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional logical workspace selector beneath a provisioned user workspace. "
            "Use this when a user wants multiple isolated sub-workspaces; omit it to use the default workspace."
        ),
    )
    session_persistence: bool = Field(
        default=True,
        description="Advanced. When true, the gateway reuses and persists session state for the client session id.",
    )
    manual_mode: bool = Field(
        default=False,
        description=(
            "Advanced. When true, the runtime returns ATR actions instead of applying workspace diffs automatically."
        ),
    )
    client_session_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional stable session label for turn resumption. For user-key requests the gateway namespaces this "
            "with the verified user and workspace."
        ),
    )
    uag_options: Optional[UagOptions] = Field(
        default=None,
        exclude=True,
        description="Deprecated compatibility wrapper. Send Codara-specific request fields at top level instead.",
    )

    @model_validator(mode="before")
    @classmethod
    def _flatten_legacy_uag_options(cls, data: Any):
        if not isinstance(data, dict):
            return data
        legacy = data.get("uag_options")
        if not isinstance(legacy, dict):
            return data
        merged = dict(data)
        for key in ("provider", "workspace_root", "workspace_id", "session_persistence", "manual_mode", "client_session_id"):
            if merged.get(key) is None and key in legacy:
                merged[key] = legacy[key]
        return merged

    def normalized_options(self) -> UagOptions:
        return UagOptions(
            provider=self.provider,
            workspace_root=self.workspace_root,
            workspace_id=self.workspace_id,
            session_persistence=self.session_persistence,
            manual_mode=self.manual_mode,
            client_session_id=self.client_session_id,
        )


class CreateUserRequest(BaseModel):
    email: str
    display_name: str
    key_label: Optional[str] = None
    key_expires_at: Optional[datetime] = None
    max_concurrency: int = 3


class UpdateUserRequest(BaseModel):
    display_name: Optional[str] = None
    max_concurrency: Optional[int] = None


class CreateUserKeyRequest(BaseModel):
    label: Optional[str] = None
    expires_at: Optional[datetime] = None


class CreateChannelLinkTokenRequest(BaseModel):
    channel: str
    bot_name: Optional[str] = None
    expires_in_minutes: int = 30


class CreateWorkspaceRequest(BaseModel):
    name: str
    template: str = "default"
    default_provider: Optional[ProviderType] = None
    force: bool = False


class OperatorTokenRequest(BaseModel):
    operator_secret: Optional[str] = Field(
        None, validation_alias=AliasChoices("operator_secret", "operator_passkey", "api_token")
    )


class OperatorRefreshRequest(BaseModel):
    refresh_token: str


async def _list_provider_models(provider: Optional[ProviderType] = None) -> list[dict[str, Any]]:
    providers = [provider] if provider else list(ProviderType)
    rows: list[dict[str, Any]] = []
    for current_provider in providers:
        adapter = orchestrator._get_adapter(current_provider)
        rows.append(await adapter.list_models(settings))
    return rows

@app.post(
    "/v1/chat/completions",
    tags=[TAG_INFERENCE],
    summary="Run a chat completion turn",
    description=(
        "Send the user's API key as `Authorization: Bearer <uagk_...>` just like an OpenAI-style bearer token. "
        "User API-key clients call this endpoint directly with `provider` and optional "
        "`workspace_id`/`client_session_id`. `workspace_root` is reserved for "
        "direct operator or internal requests."
    ),
)
async def chat_completions(
    http_request: Request,
    _chat_auth: Optional[HTTPAuthorizationCredentials] = Security(user_api_key_scheme),
):
    try:
        chat_request, uploaded_files = await _parse_chat_request(http_request)
        options = chat_request.normalized_options()
        authorization = http_request.headers.get("authorization", "")
        token: Optional[str] = None
        if authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication token missing",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if token.startswith("uagk_"):
            user_context = await get_current_user(http_request)
            user = user_context["user"]
            api_key = user_context["api_key"]
            result, _, _, attachments = await _execute_user_bound_chat(
                chat_request,
                options=options,
                user=user,
                api_key=api_key,
                uploaded_files=uploaded_files,
            )
        else:
            # Operator-authenticated turn (passkey or operator access token).
            if token != _operator_passkey():
                _validate_operator_token(token)
            options.workspace_root = _validate_direct_workspace_root(
                options.workspace_root
            )
            attachments = await _materialize_chat_uploads(
                options.workspace_root,
                chat_request.messages,
                uploaded_files,
                session_label=options.client_session_id,
            )
            provider_model = resolve_provider_model(
                options.provider,
                chat_request.model,
                settings,
            )
            result = await orchestrator.handle_request(
                options,
                chat_request.messages,
                provider_model=provider_model,
            )
        return _build_chat_completion_response(
            chat_request.model,
            options,
            result,
            extra_extensions={"attachments": attachments},
        )
    except HTTPException:
        raise
    except RuntimeError as e:
        _raise_chat_runtime_error(e)
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@channel_router.post("/telegram/{bot_name}/webhook", summary="Telegram webhook")
async def telegram_webhook(bot_name: str, http_request: Request):
    adapter = _telegram_adapter(bot_name)
    adapter.verify_webhook_secret(http_request.headers.get("X-Telegram-Bot-Api-Secret-Token"))
    try:
        payload = await http_request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid Telegram payload") from exc
    result = await adapter.handle_update(payload)
    return envelope(result)

# --- Management API (v1) ---

@auth_router.post("/token", tags=[TAG_MANAGEMENT_AUTH], summary="Issue an operator access token")
async def login(payload: Optional[OperatorTokenRequest] = None):
    operator_secret = (payload.operator_secret if payload else None) or ""
    if not operator_secret.strip():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Operator passkey required")
    if not hmac.compare_digest(operator_secret, _operator_passkey()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid operator credential")
    access_token = create_access_token(data={"sub": "operator", "scope": "operator", "token_type": "access"})
    refresh_token = create_access_token(
        data={"sub": "operator", "scope": "operator", "token_type": "refresh"},
        expires_delta=timedelta(days=7),
    )
    resp = JSONResponse(
        envelope(
            {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
            }
        )
    )
    _set_operator_cookies(resp, access_token=access_token, refresh_token=refresh_token)
    return resp


@auth_router.post("/refresh", tags=[TAG_MANAGEMENT_AUTH], summary="Refresh an operator access token")
async def refresh_operator_token(http_request: Request, payload: Optional[OperatorRefreshRequest] = None):
    token = (payload.refresh_token if payload else None) or ""
    if not token:
        token = http_request.cookies.get(OPERATOR_REFRESH_COOKIE) or ""
    token_payload = _validate_operator_token(token)
    if token_payload.get("token_type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    access_token = create_access_token(data={"sub": "operator", "scope": "operator", "token_type": "access"})
    refresh_token = create_access_token(
        data={"sub": "operator", "scope": "operator", "token_type": "refresh"},
        expires_delta=timedelta(days=7),
    )
    resp = JSONResponse(
        envelope(
            {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
            }
        )
    )
    _set_operator_cookies(resp, access_token=access_token, refresh_token=refresh_token)
    return resp


@auth_router.get("/me", tags=[TAG_MANAGEMENT_AUTH], summary="Get current operator identity")
async def operator_me(current_operator: dict = Depends(get_current_operator)):
    return envelope({"id": current_operator["id"], "scope": current_operator["scope"]})


@auth_router.post("/logout", tags=[TAG_MANAGEMENT_AUTH], summary="Clear operator auth cookies")
async def operator_logout():
    resp = JSONResponse(envelope({"ok": True}))
    _clear_operator_cookies(resp)
    return resp


@management_router.get("/users", tags=[TAG_MANAGEMENT_USERS], summary="List provisioned users")
async def list_users(limit: int = 50, offset: int = 0):
    _ensure_dashboard_admin_user()
    users = db_manager.list_users(limit=limit, offset=offset)
    summaries = _user_summary_map([user.user_id for user in users])
    return envelope([_serialize_user(user, summaries.get(user.user_id)) for user in users])


@management_router.get("/users/{user_id}", tags=[TAG_MANAGEMENT_USERS], summary="Get a provisioned user")
async def get_user(user_id: str):
    user = db_manager.get_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    
    # Get user summary for active keys and activity
    summary_map = _user_summary_map([user_id])
    data = _serialize_user(user, summary_map.get(user_id))
    
    # Add detailed lists
    data["api_keys"] = [_serialize_api_key(key) for key in db_manager.list_active_api_keys(user_id)]
    sessions = db_manager.get_user_sessions(user_id)
    bindings = _session_binding_map([session.client_session_id for session in sessions])
    data["sessions"] = [_serialize_session(session, bindings.get(session.client_session_id)) for session in sessions]
    data["recent_activity"] = [_serialize_activity(row) for row in db_manager.get_recent_user_activity(user_id)]
    data["resets"] = db_manager.get_workspace_resets(user_id)
    return envelope(data)


@management_router.post("/users", tags=[TAG_MANAGEMENT_USERS], summary="Create a provisioned user")
async def create_user(payload: CreateUserRequest, current_operator: dict = Depends(get_current_operator)):
    user_id = db_manager._generate_ulid_like("uag_usr")
    workspace_path = _ensure_user_workspace(user_id)
    user = db_manager.create_user(
        email=payload.email,
        display_name=payload.display_name,
        workspace_path=workspace_path,
        created_by=f"operator:{current_operator['id']}",
        max_api_keys=1,
        max_concurrency=payload.max_concurrency,
        user_id=user_id,
    )
    
    # Also create a 'default' workspace entry in DB
    _workspace_service_v2().create_workspace(
        "default",
        user_id,
        template="empty",
    )
    raw_key = generate_api_key()
    key = db_manager.save_api_key(user.user_id, raw_key, label=payload.key_label, expires_at=payload.key_expires_at)
    emit_audit_event(
        actor=f"operator:{current_operator['id']}",
        action="user.registered",
        target_type="user",
        target_id=user.user_id,
        after=user.model_dump(),
    )
    # Use rich serialization to ensure UI gets all fields
    summary_map = _user_summary_map([user.user_id])
    data = _serialize_user(user, summary_map.get(user.user_id))
    
    return envelope({
        **data,
        "api_key": {
            "key_id": key.key_id,
            "raw_key": raw_key,
            "label": key.label,
            "expires_at": key.expires_at.isoformat() if key.expires_at else None,
        },
    })


@management_router.patch("/users/{user_id}", tags=[TAG_MANAGEMENT_USERS], summary="Update a provisioned user")
async def update_user(user_id: str, payload: UpdateUserRequest, current_operator: dict = Depends(get_current_operator)):
    user = db_manager.get_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    before = user.model_dump()
    if payload.display_name is not None:
        user.display_name = payload.display_name
    if payload.max_concurrency is not None:
        user.max_concurrency = max(payload.max_concurrency, 1)
    user.updated_at = datetime.now(timezone.utc)
    db_manager.save_user(user)
    emit_audit_event(
        actor=f"operator:{current_operator['id']}",
        action="user.updated",
        target_type="user",
        target_id=user_id,
        before=before,
        after=user.model_dump(),
    )
    return envelope(_serialize_user(user))


@management_router.post("/users/{user_id}/suspend", tags=[TAG_MANAGEMENT_USERS], summary="Suspend a user")
async def suspend_user(user_id: str, current_operator: dict = Depends(get_current_operator)):
    user = db_manager.update_user_status(user_id, UserStatus.SUSPENDED)
    if not user:
        raise HTTPException(404, "User not found")
    emit_audit_event(
        actor=f"operator:{current_operator['id']}",
        action="user.suspended",
        target_type="user",
        target_id=user_id,
        after=user.model_dump(),
    )
    return envelope(_serialize_user(user))


@management_router.post("/users/{user_id}/unsuspend", tags=[TAG_MANAGEMENT_USERS], summary="Unsuspend a user")
async def unsuspend_user(user_id: str, current_operator: dict = Depends(get_current_operator)):
    user = db_manager.update_user_status(user_id, UserStatus.ACTIVE)
    if not user:
        raise HTTPException(404, "User not found")
    emit_audit_event(
        actor=f"operator:{current_operator['id']}",
        action="user.unsuspended",
        target_type="user",
        target_id=user_id,
        after=user.model_dump(),
    )
    return envelope(_serialize_user(user))


@management_router.delete("/users/{user_id}", tags=[TAG_MANAGEMENT_USERS], summary="Delete a user")
async def delete_user(user_id: str, current_operator: dict = Depends(get_current_operator)):
    user = db_manager.get_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    before = user.model_dump()
    user.status = UserStatus.DELETED
    user.updated_at = datetime.now(timezone.utc)
    db_manager.save_user(user)
    with db_manager._get_connection() as conn:
        conn.execute("UPDATE api_keys SET status = 'revoked', revoked_at = ? WHERE user_id = ?", (db_manager._now_ms(), user_id))
        conn.commit()
    emit_audit_event(
        actor=f"operator:{current_operator['id']}",
        action="user.deleted",
        target_type="user",
        target_id=user_id,
        before=before,
        after=user.model_dump(),
    )
    return envelope(_serialize_user(user))


@management_router.get("/users/{user_id}/keys", tags=[TAG_MANAGEMENT_USERS], summary="List a user's API keys")
async def list_user_keys(user_id: str):
    return envelope([_serialize_api_key(key) for key in db_manager.list_active_api_keys(user_id)])


@management_router.delete("/users/{user_id}/keys/{key_id}", tags=[TAG_MANAGEMENT_USERS], summary="Revoke a user's API key")
async def revoke_user_key(user_id: str, key_id: str, current_operator: dict = Depends(get_current_operator)):
    key = next((item for item in db_manager.list_api_keys(user_id) if item.key_id == key_id), None)
    if not key:
        raise HTTPException(404, "API key not found")
    active_keys = [item for item in db_manager.list_api_keys(user_id) if item.status == "active"]
    if key.status == "active" and len(active_keys) <= 1:
        raise HTTPException(status_code=400, detail="Rotate the user's API key instead of revoking the only active key")
    db_manager.revoke_api_key(key_id)
    emit_audit_event(
        actor=f"operator:{current_operator['id']}",
        action="user.key.revoked",
        target_type="api_key",
        target_id=key_id,
        before=key.model_dump(),
    )
    return envelope(_serialize_api_key(db_manager.get_api_key_by_hash(key.key_hash)))


@management_router.post("/users/{user_id}/keys/rotate", tags=[TAG_MANAGEMENT_USERS], summary="Rotate a user's API key")
async def rotate_user_key(user_id: str, payload: CreateUserKeyRequest, current_operator: dict = Depends(get_current_operator)):
    user = db_manager.get_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    raw_key = generate_api_key()
    key = db_manager.save_api_key(user.user_id, raw_key, label=payload.label, expires_at=payload.expires_at)
    emit_audit_event(
        actor=f"operator:{current_operator['id']}",
        action="user.key.rotated",
        target_type="api_key",
        target_id=key.key_id,
        after=key.model_dump(),
    )
    return envelope({
        **_serialize_api_key(key),
        "raw_key": raw_key,
    })


@management_router.post("/users/{user_id}/channels/link-token", tags=[TAG_MANAGEMENT_USERS], summary="Create a channel link token")
async def create_channel_link_token(user_id: str, payload: CreateChannelLinkTokenRequest, current_operator: dict = Depends(get_current_operator)):
    user = db_manager.get_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    channel = payload.channel.strip().lower()
    if channel not in {"telegram", "lark", "feishu"}:
        raise HTTPException(status_code=400, detail="Unsupported channel")
    bot_name = (payload.bot_name or "").strip()
    if channel == "telegram":
        if not bot_name:
            raise HTTPException(status_code=400, detail="bot_name is required for telegram")
        if get_telegram_bot_config(bot_name, settings) is None:
            raise HTTPException(status_code=404, detail="Telegram bot not found")
    token = _channel_service().create_link_token(
        user_id=user.user_id,
        channel=channel,
        bot_name=bot_name,
        created_by=f"operator:{current_operator['id']}",
        expires_in_minutes=payload.expires_in_minutes,
    )
    emit_audit_event(
        actor=f"operator:{current_operator['id']}",
        action="channel.link_token.created",
        target_type="user",
        target_id=user_id,
        after={"channel": channel, "bot_name": bot_name, "expires_at": token["expires_at"]},
    )
    return envelope(token)


@management_router.post("/users/{user_id}/workspace/reset", tags=[TAG_MANAGEMENT_USERS], summary="Reset a user's workspace sessions")
async def reset_user_workspace(user_id: str, current_operator: dict = Depends(get_current_operator)):
    user = db_manager.get_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    sessions = db_manager.get_user_sessions(user_id)
    wiped = len(sessions)
    for session in sessions:
        db_manager.delete_session(session.client_session_id)
    reset = db_manager.record_workspace_reset(user_id, "operator", f"operator:{current_operator['id']}", wiped)
    emit_audit_event(
        actor=f"operator:{current_operator['id']}",
        action="workspace.reset",
        target_type="user",
        target_id=user_id,
        after=reset.model_dump(),
    )
    return envelope({
        "reset_id": reset.reset_id,
        "sessions_wiped": wiped,
        "workspace_path": user.workspace_path,
        "files_preserved": True,
        "reset_at": reset.reset_at.isoformat(),
        "triggered_by": "operator",
    })


@management_router.get("/workspaces/v2", tags=[TAG_MANAGEMENT_WORKSPACES_V2], summary="List managed workspaces v2")
async def list_workspaces_v2():
    records = _workspace_service_v2().list_workspaces_v2()
    return envelope([record.model_dump() for record in records])


@management_router.post("/workspaces/v2", tags=[TAG_MANAGEMENT_WORKSPACES_V2], summary="Create a managed workspace v2")
async def create_workspace_v2(request: CreateWorkspaceRequest, current_operator: dict = Depends(get_current_operator)):
    try:
        result = _workspace_service_v2().create_workspace(
            request.name,
            user_id=f"operator:{current_operator['id']}",
            template=request.template,
            default_provider=request.default_provider.value if request.default_provider else None,
            force=request.force,
        )
    except Exception as exc:
        raise HTTPException(400, str(exc))

    payload = result.model_dump()
    emit_audit_event(
        actor=f"operator:{current_operator['id']}",
        action="workspace.created",
        target_type="workspace",
        target_id=result.workspace_id,
        after=payload,
    )
    return envelope(payload, meta={"templates": sorted(WORKSPACE_TEMPLATES)})


@management_router.get("/workspaces/v2/{workspace_id}", tags=[TAG_MANAGEMENT_WORKSPACES_V2], summary="Get a managed workspace v2")
async def get_workspace_detail_v2(workspace_id: str):
    record = _workspace_service_v2().get_workspace_v2(workspace_id)
    if not record:
        raise HTTPException(404, "Workspace not found")
    return envelope(record.model_dump())


@management_router.get("/workspaces", tags=[TAG_MANAGEMENT_WORKSPACES], summary="List managed workspaces")
async def list_workspaces():
    records = _workspace_manager().list_workspaces()
    return envelope([_serialize_workspace(record) for record in records])


@management_router.get("/workspaces/{workspace_id}", tags=[TAG_MANAGEMENT_WORKSPACES], summary="Get a managed workspace")
async def get_workspace_detail(workspace_id: str):
    record = _workspace_manager().get_workspace(_decode_workspace_id(workspace_id))
    if not record:
        raise HTTPException(404, "Workspace not found")
    return envelope(_serialize_workspace(record, include_details=True))


@management_router.post("/workspaces/{workspace_id}/reset", tags=[TAG_MANAGEMENT_WORKSPACES], summary="Reset workspace sessions")
async def reset_workspace(workspace_id: str, current_operator: dict = Depends(get_current_operator)):
    manager = _workspace_manager()
    workspace_path = _decode_workspace_id(workspace_id)
    record = manager.get_workspace(workspace_path)
    if not record:
        raise HTTPException(404, "Workspace not found")
    try:
        wiped = manager.reset_workspace_sessions(workspace_path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    emit_audit_event(
        actor=f"operator:{current_operator['id']}",
        action="workspace.reset",
        target_type="workspace",
        target_id=record["path"],
        after={
            "workspace_path": record["path"],
            "sessions_wiped": wiped,
            "files_preserved": True,
        },
    )
    return envelope({
        "workspace_id": workspace_id,
        "workspace_path": record["path"],
        "sessions_wiped": wiped,
        "files_preserved": True,
    })


@management_router.delete("/workspaces/{workspace_id}", tags=[TAG_MANAGEMENT_WORKSPACES], summary="Delete a managed workspace")
async def delete_workspace(workspace_id: str, current_operator: dict = Depends(get_current_operator)):
    manager = _workspace_manager()
    workspace_path = _decode_workspace_id(workspace_id)
    record = manager.get_workspace(workspace_path)
    if not record:
        raise HTTPException(404, "Workspace not found")
    existed = record["exists"]
    try:
        wiped = manager.delete_workspace(workspace_path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    emit_audit_event(
        actor=f"operator:{current_operator['id']}",
        action="workspace.deleted",
        target_type="workspace",
        target_id=record["path"],
        after={
            "workspace_path": record["path"],
            "sessions_wiped": wiped,
            "workspace_deleted": existed,
        },
    )
    return envelope({
        "workspace_id": workspace_id,
        "workspace_path": record["path"],
        "sessions_wiped": wiped,
        "workspace_deleted": existed,
    })


@management_router.post("/playground/chat", tags=[TAG_PLAYGROUND], summary="Run a playground chat turn")
async def management_playground_chat(http_request: Request, current_operator: dict = Depends(get_current_operator)):
    chat_request, uploaded_files = await _parse_chat_request(http_request)
    options = chat_request.normalized_options()
    admin_user, api_key = _ensure_dashboard_admin_user()

    try:
        result, workspace_root, workspace_id, attachments = await _execute_user_bound_chat(
            chat_request,
            options=options,
            user=admin_user,
            api_key=api_key,
            default_session_label="playground",
            uploaded_files=uploaded_files,
        )
    except RuntimeError as e:
        _raise_chat_runtime_error(e)
    emit_audit_event(
        actor=f"operator:{current_operator['id']}",
        action="playground.turn.executed",
        target_type="user",
        target_id=admin_user.user_id,
	        after={
	            "provider": options.provider.value,
	            "workspace_id": workspace_id,
	            "client_session_id": options.client_session_id,
	        },
	    )
    return _build_chat_completion_response(
        chat_request.model,
        options,
        result,
        extra_extensions={
            "bound_user_id": admin_user.user_id,
            "bound_user_display_name": admin_user.display_name,
            "bound_workspace_root": workspace_root,
            "attachments": attachments,
        },
    )


@user_router.get("/me", tags=[TAG_USER_SELF_SERVICE], summary="Get the current user profile")
async def user_me(current_user: dict = Depends(get_current_user)):
    user = current_user["user"]
    return envelope(_serialize_user(user))


@user_router.get("/keys", tags=[TAG_USER_SELF_SERVICE], summary="List the current user's API keys")
async def user_keys(current_user: dict = Depends(get_current_user)):
    return envelope([_serialize_api_key(key) for key in db_manager.list_active_api_keys(current_user["user"].user_id)])


@user_router.post("/keys", tags=[TAG_USER_SELF_SERVICE], summary="Rotate the current user's API key")
async def create_user_key(payload: CreateUserKeyRequest, current_user: dict = Depends(get_current_user)):
    user = current_user["user"]
    raw_key = generate_api_key()
    key = db_manager.save_api_key(user.user_id, raw_key, label=payload.label, expires_at=payload.expires_at)
    emit_audit_event(
        actor=f"user:{user.user_id}",
        action="user.key.rotated",
        target_type="api_key",
        target_id=key.key_id,
        after=key.model_dump(),
    )
    return envelope({
        **_serialize_api_key(key),
        "raw_key": raw_key,
    })


@user_router.delete("/keys/{key_id}", tags=[TAG_USER_SELF_SERVICE], summary="Revoke one of the current user's keys")
async def revoke_own_key(key_id: str, current_user: dict = Depends(get_current_user)):
    user = current_user["user"]
    keys = db_manager.list_api_keys(user.user_id)
    key = next((item for item in keys if item.key_id == key_id), None)
    if not key:
        raise HTTPException(404, "API key not found")
    if key.status == "active":
        raise HTTPException(400, "Rotate the active key instead of revoking it")
    db_manager.revoke_api_key(key_id)
    emit_audit_event(
        actor=f"user:{user.user_id}",
        action="user.key.revoked",
        target_type="api_key",
        target_id=key_id,
        before=key.model_dump(),
    )
    return envelope(_serialize_api_key(db_manager.get_api_key_by_hash(key.key_hash)))


@user_router.get("/sessions", tags=[TAG_USER_SELF_SERVICE], summary="List the current user's sessions")
async def user_sessions(current_user: dict = Depends(get_current_user)):
    user = current_user["user"]
    sessions = [session.dict() for session in db_manager.get_user_sessions(user.user_id)]
    return envelope(sessions)


@user_router.get("/sessions/{session_id}", tags=[TAG_USER_SELF_SERVICE], summary="Get one of the current user's sessions")
async def user_session_detail(session_id: str, current_user: dict = Depends(get_current_user)):
    user = current_user["user"]
    if not session_id.startswith(f"{user.user_id}::"):
        raise HTTPException(status_code=404, detail="Session not found")
    session = db_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return envelope({
        "session": session.dict(),
        "turns": db_manager.get_session_turns(session_id),
    })


@user_router.post("/workspace/reset", tags=[TAG_USER_SELF_SERVICE], summary="Reset the current user's workspace sessions")
async def user_workspace_reset(current_user: dict = Depends(get_current_user)):
    user = current_user["user"]
    sessions = db_manager.get_user_sessions(user.user_id)
    wiped = len(sessions)
    for session in sessions:
        db_manager.delete_session(session.client_session_id)
    reset = db_manager.record_workspace_reset(user.user_id, "user", f"user:{user.user_id}", wiped)
    emit_audit_event(
        actor=f"user:{user.user_id}",
        action="workspace.reset",
        target_type="user",
        target_id=user.user_id,
        after=reset.model_dump(),
    )
    return envelope({
        "reset_id": reset.reset_id,
        "sessions_wiped": wiped,
        "workspace_path": user.workspace_path,
        "files_preserved": True,
        "reset_at": reset.reset_at.isoformat(),
        "triggered_by": "user",
    })


@user_router.get("/workspace/resets", tags=[TAG_USER_SELF_SERVICE], summary="List workspace reset history for the current user")
async def user_workspace_resets(current_user: dict = Depends(get_current_user)):
    return envelope(db_manager.get_workspace_resets(current_user["user"].user_id))


@user_router.get("/providers/models", tags=[TAG_USER_SELF_SERVICE], summary="List available provider models")
async def user_provider_models(provider: Optional[ProviderType] = None, current_user: dict = Depends(get_current_user)):
    return envelope(await _list_provider_models(provider))


async def _provider_health_rows() -> list[dict]:
    provider_models = {
        row["provider"]: row
        for row in await _list_provider_models()
    }
    now = datetime.now(timezone.utc).isoformat()
    rows: list[dict] = []
    for provider in ProviderType:
        model_row = provider_models.get(provider.value, {})
        runtime_available = bool(model_row.get("runtime_available"))
        
        rows.append(
            {
                "provider": provider.value,
                "status": "ready" if runtime_available else "unavailable",
                "active_sessions": db_manager.count_sessions(provider=provider.value, status="active"),
                "last_seen_at": None,
                "runtime_available": runtime_available,
                "runtime_detail": model_row.get("detail"),
                "models_status": model_row.get("status"),
                "models_source": model_row.get("source"),
                "default_model": model_row.get("default_model"),
                "model_count": len(model_row.get("models") or []),
                "checked_at": now,
            }
        )
    return rows


def _overview_summary() -> dict:
    dirty_sessions = db_manager.count_sessions(status="dirty")
    sessions_total = db_manager.count_sessions()
    active_sessions = db_manager.count_sessions(status="active")
    with db_manager._get_connection() as conn:
        users_total = int(conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"])
        active_users = int(conn.execute("SELECT COUNT(*) AS count FROM users WHERE status = 'active'").fetchone()["count"])
        active_keys = int(conn.execute("SELECT COUNT(*) AS count FROM api_keys WHERE status = 'active'").fetchone()["count"])
    return {
        "sessions_total": sessions_total,
        "active_sessions": active_sessions,
        "dirty_sessions": dirty_sessions,
        "users_total": users_total,
        "active_users": active_users,
        "active_keys": active_keys,
        "providers_configured": len(ProviderType),
    }


def _measure_component_latency(check) -> float:
    started = perf_counter()
    check()
    return round((perf_counter() - started) * 1000, 2)


def _health_components() -> dict:
    def _state_store_ping():
        with db_manager._get_connection() as conn:
            conn.execute("SELECT 1").fetchone()

    gateway_latency = _measure_component_latency(lambda: None)
    orchestrator_latency = _measure_component_latency(
        lambda: {
            "session_locks": len(orchestrator._session_locks),
            "available_slots": getattr(orchestrator.semaphore, "_value", None),
        }
    )
    state_store_latency = _measure_component_latency(_state_store_ping)
    return {
        "gateway": {"status": "ok", "latency_ms": gateway_latency},
        "orchestrator": {"status": "ok", "latency_ms": orchestrator_latency},
        "state_store": {"status": "ok", "latency_ms": state_store_latency},
    }


async def _version_payload(*, check_updates: bool = False) -> dict:
    current = get_version()
    payload = {
        "name": "amesh",
        "version": current,
        "release_check": {
            "enabled": settings.release_check_enabled,
            "repository": settings.release_repository,
            "status": "disabled" if not settings.release_check_enabled else "not_checked",
            "current_version": current,
            "latest_version": None,
            "update_available": False,
            "release_url": None,
            "error": None,
        },
    }
    if not check_updates or not settings.release_check_enabled:
        return payload
    cache_key = "|".join(
        [
            str(settings.release_repository or ""),
            str(settings.release_api_base_url),
            current,
        ]
    )
    now = perf_counter()
    cached = _VERSION_CHECK_CACHE.get(cache_key)
    if cached and now - cached["checked_perf"] < max(0, int(settings.release_check_cache_ttl_seconds)):
        payload["release_check"] = dict(cached["data"])
        payload["release_check"]["cached"] = True
        return payload
    result = await asyncio.to_thread(
        check_for_update,
        repository=settings.release_repository,
        current_version=current,
        api_base_url=settings.release_api_base_url,
        timeout_seconds=settings.release_check_timeout_seconds,
    )
    release_check = result.to_dict()
    release_check["cached"] = False
    _VERSION_CHECK_CACHE.clear()
    _VERSION_CHECK_CACHE[cache_key] = {"checked_perf": now, "data": release_check}
    payload["release_check"] = release_check
    return payload


@management_router.get("/health", tags=[TAG_MANAGEMENT_OBSERVABILITY], summary="Get overall management-plane health")
async def health_check():
    checked_at = datetime.now(timezone.utc).isoformat()
    components = _health_components()
    provider_rows = await _provider_health_rows()
    degraded = any(component["status"] != "ok" for component in components.values()) or any(row["status"] != "ok" for row in provider_rows)
    return envelope(
        {
            "status": "degraded" if degraded else "ok",
            "components": components,
            "checked_at": checked_at,
        }
    )


@management_router.get("/health/providers", tags=[TAG_MANAGEMENT_OBSERVABILITY], summary="Get per-provider health")
async def provider_health():
    return envelope(await _provider_health_rows())


@management_router.get("/providers/models", tags=[TAG_MANAGEMENT_OBSERVABILITY], summary="List available provider models")
async def management_provider_models(provider: Optional[ProviderType] = None):
    return envelope(await _list_provider_models(provider))


@management_router.get("/version", tags=[TAG_MANAGEMENT_OBSERVABILITY], summary="Get Codara version and optional update status")
async def management_version(check_updates: bool = Query(default=False)):
    return envelope(await _version_payload(check_updates=check_updates))


@management_router.get("/overview", tags=[TAG_MANAGEMENT_OBSERVABILITY], summary="Get the dashboard overview payload")
async def management_overview():
    checked_at = datetime.now(timezone.utc).isoformat()
    summary = _overview_summary()
    components = _health_components()
    recent_audit = db_manager.get_audit_logs(limit=6)
    provider_rows = await _provider_health_rows()
    version_info = await _version_payload(check_updates=settings.release_check_enabled)
    health = {
        "status": "degraded" if any(component["status"] != "ok" for component in components.values()) else "ok",
        "components": components,
        "checked_at": checked_at,
    }
    return envelope({
        "health": health,
        "summary": summary,
        "providers": provider_rows,
        "recent_audit": recent_audit,
        "runtime": {
            "workspaces_root": settings.workspaces_root,
            "max_concurrency": settings.max_concurrency,
            "session_ttl_hours": settings.session_ttl_hours,
        },
        "version": version_info,
    })


def _render_metrics() -> str:
    lines = [
        "# HELP uag_sessions_total Total sessions in the registry",
        "# TYPE uag_sessions_total gauge",
        f"uag_sessions_total {db_manager.count_sessions()}",
        "# HELP uag_sessions_dirty Dirty sessions requiring intervention",
        "# TYPE uag_sessions_dirty gauge",
        f"uag_sessions_dirty {db_manager.count_sessions(status='dirty')}",
    ]
    for provider in ProviderType:
        lines.append(
            f'uag_provider_sessions{{provider="{provider.value}"}} {db_manager.count_sessions(provider=provider.value)}'
        )
    return "\n".join(lines) + "\n"


@management_router.get("/metrics", response_class=PlainTextResponse, tags=[TAG_MANAGEMENT_OBSERVABILITY], summary="Scrape management metrics")
async def metrics():
    return PlainTextResponse(_render_metrics())


@app.get("/metrics", response_class=PlainTextResponse, tags=[TAG_MANAGEMENT_OBSERVABILITY], summary="Scrape public metrics")
async def metrics_alias():
    return PlainTextResponse(_render_metrics())


@management_router.get("/sessions", tags=[TAG_MANAGEMENT_SESSIONS], summary="List sessions")
async def list_sessions(
    status: Optional[str] = None,
    provider: Optional[str] = None,
    workspace: Optional[str] = None,
    after: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
):
    sessions = db_manager.get_all_sessions(
        status=status,
        provider=provider,
        workspace_id=workspace,
        after=after,
        limit=limit,
    )
    bindings = _session_binding_map([session.client_session_id for session in sessions])
    serialized = [_serialize_session(session, bindings.get(session.client_session_id)) for session in sessions]
    return envelope(serialized, meta=_page_meta(serialized, "client_session_id"))


@management_router.get("/sessions/cli-runs", tags=[TAG_MANAGEMENT_OBSERVABILITY], summary="List captured CLI runs")
async def list_cli_runs(
    session_id: Optional[str] = None,
    provider: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 25,
):
    runs = _cli_run_store().list_runs(session_id=session_id, provider=provider, status=status, limit=limit)
    return envelope(runs)


@management_router.get(
    "/sessions/{session_id}/cli-runs",
    tags=[TAG_MANAGEMENT_OBSERVABILITY],
    summary="List captured CLI runs for a session",
    include_in_schema=False,
)
async def list_cli_runs_for_session(
    session_id: str,
    provider: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 25,
):
    runs = _cli_run_store().list_runs(session_id=session_id, provider=provider, status=status, limit=limit)
    return envelope(runs)


@management_router.get("/sessions/{session_id}", tags=[TAG_MANAGEMENT_SESSIONS], summary="Get a session")
async def get_session(session_id: str):
    session = db_manager.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    bindings = _session_binding_map([session.client_session_id])
    return envelope(_serialize_session(session, bindings.get(session.client_session_id)))

@management_router.get("/sessions/{session_id}/turns", tags=[TAG_MANAGEMENT_SESSIONS], summary="List turns for a session")
async def get_turns(session_id: str):
    turns = db_manager.get_session_turns(session_id)
    return envelope(turns)


@management_router.get("/sessions/{session_id}/tasks", tags=[TAG_MANAGEMENT_SESSIONS], summary="List tasks for a session")
async def list_session_tasks(session_id: str):
    tasks = db_manager.list_session_tasks(session_id)
    return envelope(tasks)

@management_router.delete("/sessions/{session_id}", tags=[TAG_MANAGEMENT_SESSIONS], summary="Delete a session")
async def terminate_session(session_id: str, current_operator: dict = Depends(get_current_operator)):
    session = db_manager.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    db_manager.delete_session(session_id)
    emit_audit_event(
        f"operator:{current_operator['id']}",
        "session.terminated",
        "session",
        session_id,
        before=_serialize_session(session),
    )
    return envelope()

@management_router.post("/sessions/{session_id}/reset", tags=[TAG_MANAGEMENT_SESSIONS], summary="Reset a session")
async def reset_session(session_id: str, current_operator: dict = Depends(get_current_operator)):
    session = db_manager.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    before = _serialize_session(session)
    session.status = SessionStatus.IDLE
    db_manager.save_session(session)
    emit_audit_event(
        f"operator:{current_operator['id']}",
        "session.reset",
        "session",
        session_id,
        before=before,
        after=_serialize_session(session),
    )
    return envelope(_serialize_session(session))


def _to_ms(iso_str: Optional[str]) -> Optional[int]:
    if not iso_str:
        return None
    try:
        if iso_str.endswith("Z"):
            iso_str = f"{iso_str[:-1]}+00:00"
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return None


@management_router.get("/audit", tags=[TAG_MANAGEMENT_OBSERVABILITY], summary="List audit logs")
async def list_audit_logs(
    limit: int = 25,
    after: Optional[str] = None,
    actor: Optional[str] = None,
    action: Optional[str] = None,
    target_type: Optional[str] = None,
    search: Optional[str] = None,
):
    after_ts = None
    if after:
        try:
            after_ts = int(after)
        except ValueError:
            pass
    
    logs = _get_audit_log_store().list_events(
        limit=limit + 1,
        after=after_ts,
        actor=actor,
        action=action,
        target_type=target_type,
        search=search,
    )
    
    cursor = None
    if len(logs) > limit:
        logs = logs[:limit]
        cursor = str(logs[-1]["timestamp"])
    
    return envelope(logs, meta={"page": {"cursor": cursor, "total_count": None}})


@management_router.get("/audit/stream", tags=[TAG_MANAGEMENT_OBSERVABILITY], summary="Stream audit logs")
async def stream_audit_logs(
    limit: int = Query(default=50, le=100),
    after: Optional[str] = None,
):
    after_ts = None
    if after:
        try:
            after_ts = int(after)
        except ValueError:
            pass

    initial = [e for e in AUDIT_EVENTS if after_ts is None or e["timestamp"] > after_ts]
    if len(initial) > limit:
        initial = initial[-limit:]

    async def event_stream():
        yield "event: init\ndata: " + json.dumps(initial) + "\n\n"

        last_ts = initial[-1]["timestamp"] if initial else 0
        while True:
            await asyncio.sleep(1)
            new_events = [e for e in AUDIT_EVENTS if e["timestamp"] > last_ts]
            if new_events:
                last_ts = new_events[-1]["timestamp"]
                yield "event: update\ndata: " + json.dumps(new_events) + "\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@management_router.get("/observability/logs/stream", tags=[TAG_MANAGEMENT_OBSERVABILITY], summary="Stream runtime logs")
async def stream_runtime_logs(
    limit: int = Query(default=100, le=200),
    after: Optional[str] = None,
):
    after_ts = after
    initial = [e for e in RUNTIME_LOGS if after_ts is None or str(e.get("timestamp", "")) > after_ts]
    if len(initial) > limit:
        initial = initial[-limit:]

    async def event_stream():
        yield "event: init\ndata: " + json.dumps(initial) + "\n\n"

        last_ts = initial[-1].get("timestamp") if initial else ""
        while True:
            await asyncio.sleep(1)
            new_logs = [e for e in RUNTIME_LOGS if str(e.get("timestamp", "")) > str(last_ts)]
            if new_logs:
                last_ts = new_logs[-1].get("timestamp")
                yield "event: update\ndata: " + json.dumps(new_logs) + "\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@management_router.get("/observability/traces", tags=[TAG_MANAGEMENT_OBSERVABILITY], summary="List traces")
async def list_traces(
    limit: int = 50,
    after: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    search: Optional[str] = None,
):
    traces = db_manager.list_traces(
        limit=limit,
        after=after,
        since=_to_ms(since),
        until=_to_ms(until),
        search=search,
    )
    return envelope(traces, meta=_page_meta(traces, "started_at"))


@management_router.get("/observability/traces/{trace_id}", tags=[TAG_MANAGEMENT_OBSERVABILITY], summary="Get a trace")
async def get_trace(trace_id: str):
    trace = db_manager.get_trace(trace_id)
    if not trace:
        raise HTTPException(404, "Trace not found")
    return envelope(trace)


@management_router.get("/observability/logs", tags=[TAG_MANAGEMENT_OBSERVABILITY], summary="List runtime logs")
async def list_logs(
    limit: int = 100,
    after: Optional[str] = None,
    level: Optional[str] = None,
    search: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
):
    logs = db_manager.list_runtime_logs(
        limit=limit,
        after=after,
        level=level,
        search=search,
        since=_to_ms(since),
        until=_to_ms(until),
    )
    return envelope(logs, meta=_page_meta(logs, "timestamp"))


async def _stream_file_tail(
    *,
    path: Path,
    meta_path: Optional[Path] = None,
    tail_bytes: int = 65536,
    follow: bool = True,
    poll_ms: int = 250,
) -> AsyncIterator[bytes]:
    if not path.exists():
        return
    # Batch file reads to reduce per-chunk overhead (especially on the dashboard UI).
    max_chunk_bytes = 64 * 1024
    try:
        with path.open("rb") as handle:
            if tail_bytes and tail_bytes > 0:
                try:
                    size = path.stat().st_size
                    handle.seek(max(0, size - tail_bytes))
                except OSError:
                    pass
            while True:
                buffer = bytearray()
                while len(buffer) < max_chunk_bytes:
                    chunk = handle.read(min(4096, max_chunk_bytes - len(buffer)))
                    if not chunk:
                        break
                    buffer.extend(chunk)

                if buffer:
                    yield bytes(buffer)
                    continue
                if not follow:
                    return
                ended = False
                if meta_path and meta_path.exists():
                    try:
                        meta = json.loads(meta_path.read_text(encoding="utf-8"))
                        ended = bool(meta.get("ended_at"))
                    except Exception:
                        ended = False
                if ended:
                    return
                await asyncio.sleep(max(0.05, poll_ms / 1000))
    except FileNotFoundError:
        return


@management_router.get(
    "/sessions/{session_id}/cli-runs/{provider}/{run_id}",
    tags=[TAG_MANAGEMENT_OBSERVABILITY],
    summary="Get captured CLI run metadata",
)
async def get_cli_run_meta(session_id: str, provider: str, run_id: str):
    meta_path = _cli_run_store().meta_path(provider=provider, session_id=session_id, run_id=run_id)
    meta = _cli_run_store().read_meta(meta_path)
    if meta is None:
        raise HTTPException(404, "CLI run not found")
    return envelope(meta)


@management_router.get(
    "/sessions/{session_id}/cli-runs/{provider}/{run_id}/{stream_name}",
    tags=[TAG_MANAGEMENT_OBSERVABILITY],
    summary="Get captured CLI run output (stdout/stderr)",
)
async def get_cli_run_output(
    session_id: str,
    provider: str,
    run_id: str,
    stream_name: str,
    max_bytes: int = 1024 * 1024,
    tail_bytes: int = 0,
):
    store = _cli_run_store()
    if stream_name not in {"stdout", "stderr"}:
        raise HTTPException(400, "stream_name must be stdout or stderr")
    path = store.stdout_path(provider=provider, session_id=session_id, run_id=run_id) if stream_name == "stdout" else store.stderr_path(provider=provider, session_id=session_id, run_id=run_id)
    if not path.exists():
        raise HTTPException(404, "Output not found")
    data: bytes
    if tail_bytes and tail_bytes > 0:
        try:
            with path.open("rb") as handle:
                try:
                    size = path.stat().st_size
                    handle.seek(max(0, size - int(tail_bytes)))
                except OSError:
                    pass
                data = handle.read()
        except FileNotFoundError:
            raise HTTPException(404, "Output not found")
    else:
        data = path.read_bytes()
        if max_bytes and len(data) > max_bytes:
            data = data[-max_bytes:]
    return PlainTextResponse(data.decode("utf-8", errors="replace"))


@management_router.get(
    "/sessions/{session_id}/cli-runs/{provider}/{run_id}/prompt",
    tags=[TAG_MANAGEMENT_OBSERVABILITY],
    summary="Get captured CLI run prompt",
)
async def get_cli_run_prompt(
    session_id: str,
    provider: str,
    run_id: str,
):
    store = _cli_run_store()
    path = store.prompt_path(provider=provider, session_id=session_id, run_id=run_id)
    if not path.exists():
        raise HTTPException(404, "Prompt not found")
    return PlainTextResponse(path.read_text(encoding="utf-8"))


@management_router.get(
    "/sessions/{session_id}/cli-runs/{provider}/{run_id}/{stream_name}/stream",
    tags=[TAG_MANAGEMENT_OBSERVABILITY],
    summary="Stream captured CLI run output (stdout/stderr)",
)
async def stream_cli_run_output(
    session_id: str,
    provider: str,
    run_id: str,
    stream_name: str,
    tail_bytes: int = 65536,
    follow: bool = True,
    poll_ms: int = 250,
):
    store = _cli_run_store()
    if stream_name not in {"stdout", "stderr"}:
        raise HTTPException(400, "stream_name must be stdout or stderr")
    path = store.stdout_path(provider=provider, session_id=session_id, run_id=run_id) if stream_name == "stdout" else store.stderr_path(provider=provider, session_id=session_id, run_id=run_id)
    meta_path = store.meta_path(provider=provider, session_id=session_id, run_id=run_id)
    if not path.exists() and not meta_path.exists():
        raise HTTPException(404, "CLI run not found")
    if not follow:
        return StreamingResponse(
            _stream_file_tail(path=path, meta_path=meta_path, tail_bytes=tail_bytes, follow=follow, poll_ms=poll_ms),
            media_type="text/plain; charset=utf-8",
        )

    stream_iter = await _file_tail_hub().subscribe(path=path, meta_path=meta_path, tail_bytes=tail_bytes, poll_ms=poll_ms)
    return StreamingResponse(stream_iter, media_type="text/plain; charset=utf-8")


@management_router.get("/traces/{trace_id}", tags=[TAG_MANAGEMENT_OBSERVABILITY], summary="Get a trace alias", include_in_schema=False)
async def get_trace_alias(trace_id: str):
    return await get_trace(trace_id)


@management_router.get("/traces", tags=[TAG_MANAGEMENT_OBSERVABILITY], summary="List traces alias", include_in_schema=False)
async def list_traces_alias(
    limit: int = 50,
    after: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    search: Optional[str] = None,
):
    return await list_traces(limit=limit, after=after, since=since, until=until, search=search)


@management_router.get("/logs", tags=[TAG_MANAGEMENT_OBSERVABILITY], summary="List logs alias", include_in_schema=False)
async def list_logs_alias(
    limit: int = 100,
    after: Optional[str] = None,
    level: Optional[str] = None,
    search: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
):
    return await list_logs(limit=limit, after=after, level=level, search=search, since=since, until=until)


app.include_router(auth_router)
app.include_router(management_router)
app.include_router(user_router)
app.include_router(channel_router)

# --- Static Dashboard ---

def _dashboard_dist_path() -> Path:
    # 1. Check current working directory
    cwd_path = Path(os.getcwd()) / "ui" / "dist"
    if (cwd_path / "index.html").exists():
        return cwd_path
        
    # 2. Check relative to this file (development mode)
    dev_path = Path(__file__).parent.parent.parent.parent / "ui" / "dist"
    if (dev_path / "index.html").exists():
        return dev_path
        
    # 3. Check /app/ui/dist (Docker production mode)
    docker_path = Path("/app/ui/dist")
    if (docker_path / "index.html").exists():
        return docker_path
        
    return cwd_path


def _safe_dashboard_file(dashboard_path: str) -> Optional[Path]:
    dist_path = _dashboard_dist_path().resolve()
    # Remove leading slash and dashboard prefix if present
    clean_path = dashboard_path.lstrip("/")
    if clean_path.startswith("dashboard/"):
        clean_path = clean_path[len("dashboard/"):]
    
    candidate = (dist_path / clean_path).resolve()
    if candidate != dist_path and dist_path not in candidate.parents:
        return None
    if candidate.is_file():
        return candidate
    return None


def _is_dashboard_asset_path(dashboard_path: str) -> bool:
    normalized = dashboard_path.strip("/")
    return normalized.startswith("assets/") or bool(Path(normalized).suffix)


def _dashboard_response(dashboard_path: str = ""):
    dist_path = _dashboard_dist_path()
    index_file = dist_path / "index.html"
    
    if not index_file.exists():
        return PlainTextResponse(
            "Dashboard assets not found. Please run `npm run build` in the ui directory.",
            status_code=404
        )

    if dashboard_path:
        # Check if it's a direct file in dist (e.g. favicon.svg, or an asset)
        static_file = _safe_dashboard_file(dashboard_path)
        if static_file:
            # Let FastAPI/Starlette handle MIME types automatically
            return FileResponse(static_file)
        
        # If it looks like an asset but wasn't found, return 404
        if _is_dashboard_asset_path(dashboard_path):
             raise HTTPException(status_code=404, detail="Dashboard asset not found")

    # Fallback to index.html for SPA routing
    return FileResponse(index_file)


# (Optional) We can still keep the mount for performance in production, 
# but _dashboard_response now handles the logic for tests and dynamic paths.
def setup_dashboard(app: FastAPI):
    dist_root = _dashboard_dist_path()
    if (dist_root / "assets").exists():
        app.mount("/dashboard/assets", StaticFiles(directory=str(dist_root / "assets")), name="dashboard-assets")


@app.get("/dashboard", include_in_schema=False)
async def dashboard_root():
    return _dashboard_response()


@app.get("/dashboard/{dashboard_path:path}", include_in_schema=False)
async def dashboard_spa(dashboard_path: str):
    # This catch-all handles both sub-files and SPA routes
    return _dashboard_response(dashboard_path)


@app.get("/", include_in_schema=False)
async def root_dashboard():
    return _dashboard_response()
