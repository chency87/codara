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

class AuthType(str, Enum):
    OAUTH_SESSION = "OAUTH_SESSION"
    API_KEY = "API_KEY"
    SERVICE_ACCOUNT = "SERVICE_ACCOUNT"


class AccountStatus(str, Enum):
    ACTIVE = "active"
    READY = "ready"
    COOLDOWN = "cooldown"
    EXPIRED = "expired"
    DISABLED = "disabled"
    ERROR = "error"


ENABLED_ACCOUNT_STATUSES = {
    AccountStatus.ACTIVE.value,
    AccountStatus.READY.value,
}


def is_account_enabled_status(status: str) -> bool:
    return status in ENABLED_ACCOUNT_STATUSES


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

class Account(BaseModel):
    account_id: str
    credential_id: Optional[str] = None
    inventory_source: str = "vault"
    provider: ProviderType
    auth_type: AuthType
    label: str
    encrypted_credential: Optional[str] = None
    status: str = AccountStatus.ACTIVE.value
    auth_index: Optional[str] = None
    cooldown_until: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    cli_primary: bool = False
    usage_tpm: int = 0
    usage_rpd: int = 0
    usage_hourly: int = 0
    usage_weekly: int = 0
    tpm_limit: int = 100000
    rpd_limit: int = 5000
    hourly_limit: int = 50000
    weekly_limit: int = 1000000
    remaining_compute_hours: float = 0.0 # e.g. "5h left"
    hourly_used_pct: Optional[float] = None
    weekly_used_pct: Optional[float] = None
    hourly_reset_after_seconds: Optional[int] = None
    weekly_reset_after_seconds: Optional[int] = None
    hourly_reset_at: Optional[datetime] = None
    weekly_reset_at: Optional[datetime] = None
    access_token_expires_at: Optional[datetime] = None
    usage_source: Optional[str] = None
    plan_type: Optional[str] = None
    rate_limit_allowed: Optional[bool] = None
    rate_limit_reached: Optional[bool] = None
    credits_has_credits: Optional[bool] = None
    credits_unlimited: Optional[bool] = None
    credits_overage_limit_reached: Optional[bool] = None
    approx_local_messages_min: Optional[int] = None
    approx_local_messages_max: Optional[int] = None
    approx_cloud_messages_min: Optional[int] = None
    approx_cloud_messages_max: Optional[int] = None


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


class UserUsage(BaseModel):
    user_id: str
    period: str
    provider: ProviderType
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_tokens: int = 0
    request_count: int = 0


class WorkspaceReset(BaseModel):
    reset_id: str
    user_id: str
    triggered_by: str
    actor_id: str
    sessions_wiped: int
    reset_at: datetime

class Session(BaseModel):
    client_session_id: str
    backend_id: str
    provider: ProviderType
    account_id: str
    user_id: Optional[str] = None
    api_key_id: Optional[str] = None
    cwd_path: str
    prefix_hash: str
    status: SessionStatus = SessionStatus.IDLE
    fence_token: int = 0
    last_context_tokens: int = 0
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
    context_tokens: Optional[int] = None
    is_retry: bool = False
