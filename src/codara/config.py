"""Centralized configuration for the Codara program."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

import tomllib
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from codara.core.models import ProviderType

CONFIG_ENV_VAR = "UAG_CONFIG_PATH"
CONFIG_DIR_ENV_VAR = "UAG_CONFIG_DIR"
DEFAULT_CONFIG_FILENAME = "codara.toml"
DEFAULT_CONFIG_DIRNAME = "codara"

_FIELD_ENV_MAP = {
    "app_name": "UAG_APP_NAME",
    "app_version": "UAG_APP_VERSION",
    "debug": "UAG_DEBUG",
    "host": "UAG_HOST",
    "port": "UAG_PORT",
    "secret_key": "API_TOKEN",
    "algorithm": "UAG_ALGORITHM",
    "database_path": "UAG_DATABASE_PATH",
    "max_concurrency": "UAG_MAX_CONCURRENCY",
    "session_ttl_hours": "UAG_SESSION_TTL_HOURS",
    "workspace_lock_timeout": "UAG_WORKSPACE_LOCK_TIMEOUT",
    "workspaces_root": "UAG_WORKSPACES_ROOT",
    "logs_root": "UAG_LOGS_ROOT",
    "log_max_bytes": "UAG_LOG_MAX_BYTES",
    "log_backup_count": "UAG_LOG_BACKUP_COUNT",
    "default_tpm_limit": "UAG_DEFAULT_TPM_LIMIT",
    "default_rpd_limit": "UAG_DEFAULT_RPD_LIMIT",
    "default_hourly_limit": "UAG_DEFAULT_HOURLY_LIMIT",
    "default_weekly_limit": "UAG_DEFAULT_WEEKLY_LIMIT",
    "codex_billing_api_key": "UAG_CODEX_BILLING_API_KEY",
    "codex_usage_endpoints": "UAG_CODEX_USAGE_ENDPOINTS",
    "codex_oauth_url": "UAG_CODEX_OAUTH_URL",
    "codex_default_model": "UAG_CODEX_DEFAULT_MODEL",
    "gemini_billing_api_key": "UAG_GEMINI_BILLING_API_KEY",
    "gemini_usage_endpoints": "UAG_GEMINI_USAGE_ENDPOINTS",
    "gemini_default_model": "UAG_GEMINI_DEFAULT_MODEL",
    "opencode_default_model": "UAG_OPENCODE_DEFAULT_MODEL",
    "gemini_base_url": "GEMINI_BASE_URL",
    "isolated_envs_root": "UAG_ISOLATED_ENVS_ROOT",
    "redis_url": "REDIS_URL",
}


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = Field(default="Codara", validation_alias="UAG_APP_NAME")
    app_version: str = Field(default="0.1.0", validation_alias="UAG_APP_VERSION")
    debug: bool = Field(default=False, validation_alias="UAG_DEBUG")

    # Server
    host: str = Field(default="0.0.0.0", validation_alias="UAG_HOST")
    port: int = Field(default=8000, validation_alias="UAG_PORT")
    secret_key: str = Field(
        default="super-secret-key",
        validation_alias=AliasChoices("API_TOKEN", "UAG_MGMT_SECRET"),
    )
    algorithm: str = Field(default="HS256", validation_alias="UAG_ALGORITHM")

    # Database
    database_path: str = Field(default="codara.db", validation_alias="UAG_DATABASE_PATH")

    # Orchestrator
    max_concurrency: int = Field(default=10, validation_alias="UAG_MAX_CONCURRENCY")
    session_ttl_hours: int = Field(default=24, validation_alias="UAG_SESSION_TTL_HOURS")

    # Workspace
    workspace_lock_timeout: int = Field(default=300, validation_alias="UAG_WORKSPACE_LOCK_TIMEOUT")
    workspaces_root: str = Field(default="workspaces", validation_alias="UAG_WORKSPACES_ROOT")
    logs_root: str = Field(default="logs", validation_alias="UAG_LOGS_ROOT")
    log_max_bytes: int = Field(default=20 * 1024 * 1024, validation_alias="UAG_LOG_MAX_BYTES")
    log_backup_count: int = Field(default=5, validation_alias="UAG_LOG_BACKUP_COUNT")

    # Rate Limits (defaults - can be overridden per account)
    default_tpm_limit: int = Field(default=100000, validation_alias="UAG_DEFAULT_TPM_LIMIT")
    default_rpd_limit: int = Field(default=5000, validation_alias="UAG_DEFAULT_RPD_LIMIT")
    default_hourly_limit: int = Field(default=50000, validation_alias="UAG_DEFAULT_HOURLY_LIMIT")
    default_weekly_limit: int = Field(default=1000000, validation_alias="UAG_DEFAULT_WEEKLY_LIMIT")

    # Isolation
    isolated_envs_root: Optional[str] = Field(default=None, validation_alias="UAG_ISOLATED_ENVS_ROOT")

    # Billing credentials (fallback only; OAuth session tokens are preferred when present)
    codex_billing_api_key: Optional[str] = Field(default=None, validation_alias="UAG_CODEX_BILLING_API_KEY")
    codex_usage_endpoints: str = Field(
        default="https://chatgpt.com/backend-api/wham/usage,https://api.openai.com/dashboard/codex/usage",
        validation_alias="UAG_CODEX_USAGE_ENDPOINTS",
    )
    codex_oauth_url: str = Field(default="https://auth0.openai.com/oauth/token", validation_alias="UAG_CODEX_OAUTH_URL")
    codex_default_model: str = Field(default="gpt-5-codex", validation_alias="UAG_CODEX_DEFAULT_MODEL")
    gemini_billing_api_key: Optional[str] = Field(default=None, validation_alias="UAG_GEMINI_BILLING_API_KEY")
    gemini_usage_endpoints: str = Field(
        default="https://gemini.google.com/backend-api/wham/usage,https://aistudio.google.com/backend-api/wham/usage,https://api.gemini.ai/v1/usage",
        validation_alias="UAG_GEMINI_USAGE_ENDPOINTS",
    )
    gemini_default_model: str = Field(default="gemini-2.5-pro", validation_alias="UAG_GEMINI_DEFAULT_MODEL")
    opencode_default_model: str = Field(default="openai/gpt-5", validation_alias="UAG_OPENCODE_DEFAULT_MODEL")

    # Provider-specific settings
    gemini_base_url: str = Field(default="https://api.gemini.ai", validation_alias="GEMINI_BASE_URL")

    # Redis (for production deployment)
    redis_url: Optional[str] = Field(default=None, validation_alias="REDIS_URL")

def load_config_from_file(config_path: str) -> Dict[str, Any]:
    """
    Load configuration from a TOML, YAML, or JSON file.

    Args:
        config_path: Path to the configuration file

    Returns:
        Dictionary with configuration values
    """
    path = Path(config_path)
    if not path.exists():
        return {}

    if path.suffix.lower() == ".toml":
        with path.open("rb") as handle:
            data = tomllib.load(handle)
        return _flatten_config(data) if isinstance(data, dict) else {}
    if path.suffix.lower() in [".yaml", ".yml"]:
        try:
            import yaml

            return yaml.safe_load(path.read_text()) or {}
        except ImportError:
            raise ImportError("PyYAML is required to load YAML config files")
    if path.suffix.lower() == ".json":
        import json

        return json.loads(path.read_text())
    raise ValueError(f"Unsupported config file format: {path.suffix}")


def get_config_dir() -> Path:
    """Return the directory used for persistent Codara config state."""
    override = os.getenv(CONFIG_DIR_ENV_VAR)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / DEFAULT_CONFIG_DIRNAME


def get_config_path() -> Path:
    """Resolve the central TOML config file location."""
    override = os.getenv(CONFIG_ENV_VAR)
    if override:
        return Path(override).expanduser()

    cwd_path = Path.cwd() / DEFAULT_CONFIG_FILENAME
    if cwd_path.exists():
        return cwd_path

    home_path = get_config_dir() / DEFAULT_CONFIG_FILENAME
    if home_path.exists():
        return home_path

    return cwd_path


def _flatten_config(data: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten nested TOML tables into the flat Settings schema."""
    flattened: Dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            flattened.update(_flatten_config(value))
        else:
            flattened[key] = value
    return flattened


def _resolve_path_like_settings(config_values: Dict[str, Any], config_path: Path) -> Dict[str, Any]:
    resolved = dict(config_values)
    base_dir = config_path.parent.resolve()
    for key in ("database_path", "workspaces_root", "isolated_envs_root", "logs_root"):
        value = resolved.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            resolved[key] = str((base_dir / candidate).resolve())
    for key in ("codex_billing_api_key", "gemini_billing_api_key", "redis_url"):
        if resolved.get(key) == "":
            resolved[key] = None
    return resolved


def _env_override_present(key: str) -> bool:
    env_name = _FIELD_ENV_MAP.get(key)
    if key == "secret_key":
        return os.getenv("API_TOKEN") is not None or os.getenv("UAG_MGMT_SECRET") is not None
    return bool(env_name and os.getenv(env_name) is not None)


def load_settings(config_path: Optional[str] = None) -> "Settings":
    """Load settings from env vars and the central config file."""
    settings = Settings()

    path = Path(config_path).expanduser() if config_path else get_config_path()
    config_values = load_config_from_file(str(path)) if path.exists() else {}
    if path.exists():
        config_values = _resolve_path_like_settings(config_values, path)

    for key, value in config_values.items():
        if _env_override_present(key):
            continue
        if hasattr(settings, key):
            setattr(settings, key, value)

    # Default isolated_envs_root to be inside workspaces_root if not set
    if not settings.isolated_envs_root:
        settings.isolated_envs_root = str(Path(settings.workspaces_root) / "isolated_envs")

    return settings


_settings: Optional[Settings] = None


def get_settings(force_reload: bool = False) -> Settings:
    """Get the cached settings instance, reloading if requested."""
    global _settings, settings
    if force_reload or _settings is None:
        _settings = load_settings()
        settings = _settings
    return _settings


# Global settings instance
settings = get_settings()


_PROVIDER_DEFAULT_MODEL_FIELDS = {
    ProviderType.CODEX: "codex_default_model",
    ProviderType.GEMINI: "gemini_default_model",
    ProviderType.OPENCODE: "opencode_default_model",
}


def get_provider_default_model(provider: ProviderType, current_settings: Optional[Settings] = None) -> str:
    settings_obj = current_settings or get_settings()
    return str(getattr(settings_obj, _PROVIDER_DEFAULT_MODEL_FIELDS[provider]))


def resolve_provider_model(
    provider: ProviderType,
    requested_model: Optional[str],
    current_settings: Optional[Settings] = None,
) -> str:
    stripped = (requested_model or "").strip()
    if not stripped or stripped.lower().startswith("uag-"):
        return get_provider_default_model(provider, current_settings)
    return stripped


def update_settings_from_dict(config_dict: Dict[str, Any]) -> None:
    """
    Update global settings from a dictionary.

    Args:
        config_dict: Dictionary with configuration values
    """
    global _settings, settings
    target = _settings or settings
    # Update settings with values from dict
    for key, value in config_dict.items():
        if hasattr(target, key):
            setattr(target, key, value)
