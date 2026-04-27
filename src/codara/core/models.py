from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime

class ProviderType(str, Enum):
    CODEX = "codex"
    GEMINI = "gemini"
    OPENCODE = "opencode"

class SessionStatus(str, Enum):
    IDLE = "idle"
    ACTIVE = "active"
    DIRTY = "dirty"
    EXPIRED = "expired"

class UserStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class Message(BaseModel):

    role: str
    content: str


class UagOptions(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "provider": "codex",
                    "workspace_id": "project-a/feature-x",
                    "client_session_id": "thread-1",
                },
                {
                    "provider": "codex",
                    "workspace_root": "/absolute/path/to/project",
                    "client_session_id": "thread-1",
                    "manual_mode": False,
                },
            ]
        }
    )

    provider: ProviderType = Field(description="Target provider runtime.")
    workspace_root: Optional[str] = Field(
        default=None,
        description=(
            "Operator/internal only. Absolute workspace path for direct operator requests. "
            "Verified user-key requests should omit this because the gateway injects the user's bound workspace root."
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
    user_id: Optional[str] = Field(
        default=None,
        description="Internal field injected by the gateway after authentication.",
    )
    api_key_id: Optional[str] = Field(
        default=None,
        description="Internal field injected by the gateway after API-key validation.",
    )

class User(BaseModel):
    user_id: str
    email: str
    display_name: str
    status: UserStatus = UserStatus.ACTIVE
    workspace_path: str
    created_at: datetime
    created_by: str
    updated_at: datetime
    max_api_keys: int = 1
    max_concurrency: int = 3


class ApiKey(BaseModel):
    key_id: str
    user_id: str
    key_hash: str
    key_prefix: str
    label: Optional[str] = None
    status: str = "active"
    last_used_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    created_at: datetime
    revoked_at: Optional[datetime] = None


class Workspace(BaseModel):
    workspace_id: str
    name: str
    path: str
    user_id: str
    template: str = "default"
    default_provider: Optional[ProviderType] = None
    created_at: datetime
    updated_at: datetime


class Session(BaseModel):
    session_id: str
    workspace_id: str
    client_session_id: Optional[str] = None
    backend_id: str
    provider: ProviderType
    user_id: str
    api_key_id: Optional[str] = None
    cwd_path: str
    status: SessionStatus = SessionStatus.IDLE
    created_at: datetime
    updated_at: datetime
    expires_at: datetime


class TurnResult(BaseModel):
    output: str
    backend_id: str
    finish_reason: str
    modified_files: List[str] = []
    diff: Optional[str] = None
    actions: List[Dict[str, Any]] = [] # For ATR module
    dirty: bool = False
    is_retry: bool = False


class Task(BaseModel):
    task_id: str
    session_id: str
    workspace_id: str
    user_id: str
    prompt: str
    status: str = "pending" # pending, running, completed, failed
    result: Optional[TurnResult] = None
    created_at: datetime
    updated_at: datetime

class WorkspaceReset(BaseModel):
    reset_id: str
    user_id: str
    triggered_by: str
    actor_id: str
    sessions_wiped: int
    reset_at: datetime
