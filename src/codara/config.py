"""Centralized configuration for the Codara program."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import tomllib
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from codara.core.models import ProviderType

DEFAULT_CONFIG_FILENAME = "codara.toml"
CONFIG_ENV_VAR = "UAG_CONFIG_PATH"


_FIELD_ENV_MAP: Dict[str, str] = {
    "app_name": "UAG_APP_NAME",
    "app_version": "UAG_APP_VERSION",
    "debug": "UAG_DEBUG",
    "host": "UAG_HOST",
    "port": "UAG_PORT",
    "secret_key": "UAG_MGMT_SECRET",
    "algorithm": "UAG_ALGORITHM",
    "database_path": "UAG_DATABASE_PATH",
    "max_concurrency": "UAG_MAX_CONCURRENCY",
    "session_ttl_hours": "UAG_SESSION_TTL_HOURS",
    "workspace_lock_timeout": "UAG_WORKSPACE_LOCK_TIMEOUT",
    "workspaces_root": "UAG_WORKSPACES_ROOT",
    "logs_root": "UAG_LOGS_ROOT",
    "log_max_bytes": "UAG_LOG_MAX_BYTES",
    "log_backup_count": "UAG_LOG_BACKUP_COUNT",
    "log_retention_days": "UAG_LOG_RETENTION_DAYS",
    "cli_capture_enabled": "UAG_CLI_CAPTURE_ENABLED",
    "cli_capture_root": "UAG_CLI_CAPTURE_ROOT",
    "framework_log_level": "UAG_FRAMEWORK_LOG_LEVEL",
    "codex_default_model": "UAG_CODEX_DEFAULT_MODEL",
    "codex_stall_timeout_seconds": "UAG_CODEX_STALL_TIMEOUT_SECONDS",
    "gemini_default_model": "UAG_GEMINI_DEFAULT_MODEL",
    "gemini_stall_timeout_seconds": "UAG_GEMINI_STALL_TIMEOUT_SECONDS",
    "opencode_default_model": "UAG_OPENCODE_DEFAULT_MODEL",
    "opencode_stall_timeout_seconds": "UAG_OPENCODE_STALL_TIMEOUT_SECONDS",
    "release_check_enabled": "UAG_RELEASE_CHECK_ENABLED",
    "release_repository": "UAG_RELEASE_REPOSITORY",
    "release_api_base_url": "UAG_RELEASE_API_BASE_URL",
    "release_check_timeout_seconds": "UAG_RELEASE_CHECK_TIMEOUT_SECONDS",
    "release_check_cache_ttl_seconds": "UAG_RELEASE_CHECK_CACHE_TTL_SECONDS",
    "redis_url": "REDIS_URL",
}

_CONFIG_BLOCK_FIELD_MAP: Dict[Tuple[str, ...], str] = {
    ("app", "name"): "app_name",
    ("app", "version"): "app_version",
    ("app", "debug"): "debug",
    ("server", "host"): "host",
    ("server", "port"): "port",
    ("server", "secret_key"): "secret_key",
    ("server", "algorithm"): "algorithm",
    ("database", "path"): "database_path",
    ("orchestrator", "max_concurrency"): "max_concurrency",
    ("orchestrator", "session_ttl_hours"): "session_ttl_hours",
    ("workspace", "lock_timeout"): "workspace_lock_timeout",
    ("workspace", "root"): "workspaces_root",
    ("logging", "root"): "logs_root",
    ("logging", "max_bytes"): "log_max_bytes",
    ("logging", "backup_count"): "log_backup_count",
    ("logging", "persistence_backend"): "log_persistence_backend",
    ("logging", "runtime_root"): "runtime_log_root",
    ("logging", "retention_days"): "log_retention_days",
    ("logging", "cli_capture_enabled"): "cli_capture_enabled",
    ("logging", "cli_capture_root"): "cli_capture_root",
    ("logging", "framework_level"): "framework_log_level",
    ("providers", "codex", "default_model"): "codex_default_model",
    ("providers", "codex", "stall_timeout_seconds"): "codex_stall_timeout_seconds",
    ("providers", "gemini", "default_model"): "gemini_default_model",
    ("providers", "gemini", "stall_timeout_seconds"): "gemini_stall_timeout_seconds",
    ("providers", "gemini", "base_url"): "gemini_base_url",
    ("providers", "opencode", "default_model"): "opencode_default_model",
    ("providers", "opencode", "stall_timeout_seconds"): "opencode_stall_timeout_seconds",
    ("release", "enabled"): "release_check_enabled",
    ("release", "repository"): "release_repository",
    ("release", "api_base_url"): "release_api_base_url",
    ("release", "check_timeout_seconds"): "release_check_timeout_seconds",
    ("release", "check_cache_ttl_seconds"): "release_check_cache_ttl_seconds",
    ("infra", "redis_url"): "redis_url",
    ("telemetry", "enabled"): "telemetry_enabled",
    ("telemetry", "persist_traces"): "telemetry_persist_traces",
    ("telemetry", "json_logs"): "telemetry_json_logs",
    ("telemetry", "max_attr_length"): "telemetry_max_attr_length",
    ("telemetry", "persistence_backend"): "telemetry_persistence_backend",
    ("telemetry", "trace_root"): "telemetry_trace_root",
    ("telemetry", "trace_retention_days"): "telemetry_trace_retention_days",
}

_SETTINGS_FIELDS = set(_FIELD_ENV_MAP) | {"channels"}


class TelegramBotSettings(BaseSettings):
    name: str
    enabled: bool = True
    token: str
    webhook_secret: Optional[str] = None
    username: Optional[str] = None
    mention_only: Optional[bool] = None


class TelegramChannelSettings(BaseSettings):
    enabled: bool = False
    receive_mode: str = "webhook"
    mention_only: bool = False
    api_base: str = "https://api.telegram.org"
    bots: List[TelegramBotSettings] = Field(default_factory=list)


class ChannelsSettings(BaseSettings):
    telegram: TelegramChannelSettings = Field(default_factory=TelegramChannelSettings)


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
    log_persistence_backend: str = Field(default="datetime_file")
    runtime_log_root: str = Field(default="runtime")
    audit_log_root: str = Field(default="audit")
    log_retention_days: int = Field(default=30, validation_alias="UAG_LOG_RETENTION_DAYS")
    cli_capture_enabled: bool = Field(default=True, validation_alias="UAG_CLI_CAPTURE_ENABLED")
    cli_capture_root: str = Field(default="cli-runs", validation_alias="UAG_CLI_CAPTURE_ROOT")
    framework_log_level: Optional[str] = Field(default=None, validation_alias="UAG_FRAMEWORK_LOG_LEVEL")

    # Provider configurations
    codex_default_model: str = Field(default="gpt-5-codex", validation_alias="UAG_CODEX_DEFAULT_MODEL")
    codex_stall_timeout_seconds: int = Field(default=600, validation_alias="UAG_CODEX_STALL_TIMEOUT_SECONDS")
    gemini_default_model: str = Field(default="gemini-2.5-pro", validation_alias="UAG_GEMINI_DEFAULT_MODEL")
    gemini_stall_timeout_seconds: int = Field(default=600, validation_alias="UAG_GEMINI_STALL_TIMEOUT_SECONDS")
    opencode_default_model: str = Field(default="opencode/big-pickle", validation_alias="UAG_OPENCODE_DEFAULT_MODEL")
    opencode_stall_timeout_seconds: int = Field(default=600, validation_alias="UAG_OPENCODE_STALL_TIMEOUT_SECONDS")
    gemini_base_url: str = Field(default="https://api.gemini.ai", validation_alias="GEMINI_BASE_URL")

    # Update checks
    release_check_enabled: bool = Field(default=False, validation_alias="UAG_RELEASE_CHECK_ENABLED")
    release_repository: str = Field(default="", validation_alias="UAG_RELEASE_REPOSITORY")
    release_api_base_url: str = Field(default="https://api.github.com", validation_alias="UAG_RELEASE_API_BASE_URL")
    release_check_timeout_seconds: int = Field(default=3, validation_alias="UAG_RELEASE_CHECK_TIMEOUT_SECONDS")
    release_check_cache_ttl_seconds: int = Field(default=21600, validation_alias="UAG_RELEASE_CHECK_CACHE_TTL_SECONDS")

    # Infrastructure
    redis_url: Optional[str] = Field(default=None, validation_alias="REDIS_URL")

    # Telemetry
    telemetry_enabled: bool = Field(default=True, validation_alias="UAG_TELEMETRY_ENABLED")
    telemetry_persist_traces: bool = Field(default=True, validation_alias="UAG_TELEMETRY_PERSIST_TRACES")
    telemetry_json_logs: bool = Field(default=True, validation_alias="UAG_TELEMETRY_JSON_LOGS")
    telemetry_max_attr_length: int = Field(default=512, validation_alias="UAG_TELEMETRY_MAX_ATTR_LENGTH")
    telemetry_persistence_backend: str = Field(default="file", validation_alias="UAG_TELEMETRY_PERSISTENCE_BACKEND")
    telemetry_trace_root: str = Field(default="traces", validation_alias="UAG_TELEMETRY_TRACE_ROOT")
    telemetry_trace_retention_days: int = Field(default=30, validation_alias="UAG_TELEMETRY_TRACE_RETENTION_DAYS")

    channels: ChannelsSettings = Field(default_factory=ChannelsSettings)


def get_config_dir() -> Path:
    """Resolve the central Codara config directory (shared vault & settings)."""
    return Path("~/.codara").expanduser()


def get_config_path() -> Path:
    """Resolve the central TOML config file location."""
    override = os.getenv(CONFIG_ENV_VAR)
    if override:
        return Path(override).expanduser()

    cwd_path = Path.cwd() / DEFAULT_CONFIG_FILENAME
    if cwd_path.exists():
        return cwd_path

    configs_path = Path.cwd() / "configs" / DEFAULT_CONFIG_FILENAME
    if configs_path.exists():
        return configs_path

    home_path = get_config_dir() / DEFAULT_CONFIG_FILENAME
    if home_path.exists():
        return home_path

    return cwd_path


def load_settings(config_path: Optional[Path] = None) -> Settings:
    """Load settings from environment variables and an optional TOML file."""
    if not config_path:
        config_path = get_config_path()

    config_values: Dict[str, Any] = {}
    if config_path.exists():
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
            config_values = _flatten_config(data)

    # Use resolve paths for directories mentioned in the config
    config_values = _resolve_path_like_settings(config_values, config_path)

    # Initialize Settings (Pydantic will handle ENV overrides)
    settings = Settings()

    # Manual update for fields found in TOML
    for key, value in config_values.items():
        if _is_override_present(key):
            continue
        if key == "channels":
            settings.channels = ChannelsSettings.model_validate(value)
            continue
        if hasattr(settings, key):
            setattr(settings, key, value)

    return settings


_settings: Optional[Settings] = None


def get_settings(force_reload: bool = False) -> Settings:
    """Get the global application settings instance."""
    global _settings
    if _settings is None or force_reload:
        _settings = load_settings()
    return _settings


def _flatten_config(data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert block-based TOML structure into a flat dict mapping to Settings fields."""
    flat: Dict[str, Any] = {}

    # Handle standard nested blocks
    for path, field_name in _CONFIG_BLOCK_FIELD_MAP.items():
        val = data
        for part in path:
            if isinstance(val, dict):
                val = val.get(part)
            else:
                val = None
                break
        if val is not None:
            flat[field_name] = val

    # Special case for channels (complex structure)
    if "channels" in data:
        flat["channels"] = data["channels"]

    return flat


def _resolve_path_like_settings(config_values: Dict[str, Any], config_path: Path) -> Dict[str, Any]:
    resolved = dict(config_values)
    project_root = Path.cwd().resolve()
    try:
        config_resolved = config_path.expanduser().resolve()
    except FileNotFoundError:
        config_resolved = config_path.expanduser().absolute()
    # If the config file lives in the current project (including `configs/`),
    # resolve relative paths from the project root instead of the config dir.
    # This keeps `data/`, `logs/`, and `workspaces/` stable regardless of whether
    # the user places `codara.toml` at repo root or under `configs/`.
    if config_resolved.is_relative_to(project_root):
        base_dir = project_root
    else:
        base_dir = config_resolved.parent.resolve()
    for key in ("database_path", "workspaces_root", "logs_root"):
        value = resolved.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = (base_dir / candidate).resolve()
        resolved[key] = str(candidate)
    return resolved


def _is_override_present(field_name: str) -> bool:
    env_var = _FIELD_ENV_MAP.get(field_name)
    return env_var is not None and env_var in os.environ


def get_provider_default_model(provider: ProviderType, current_settings: Optional[Settings] = None) -> str:
    s = current_settings or get_settings()
    if provider == ProviderType.CODEX:
        return s.codex_default_model
    if provider == ProviderType.GEMINI:
        return s.gemini_default_model
    if provider == ProviderType.OPENCODE:
        return s.opencode_default_model
    return ""


def update_settings(config_dict: Dict[str, Any]) -> None:
    """
    Force-update live settings. Useful for testing or dynamic reconfig.
    """
    global _settings, settings
    target = _settings or settings
    # Update settings with values from dict
    for key, value in config_dict.items():
        if hasattr(target, key):
            setattr(target, key, value)


def resolve_provider_model(
    provider: ProviderType,
    requested_model: Optional[str] = None,
    current_settings: Optional[Settings] = None,
) -> str:
    stripped = (requested_model or "").strip()
    if not stripped or stripped.lower().startswith("uag-"):
        return get_provider_default_model(provider, current_settings)
    return stripped


def get_telegram_bot_config(bot_name: str, current_settings: Optional[Settings] = None) -> Optional[TelegramBotSettings]:
    s = current_settings or get_settings()
    for bot in s.channels.telegram.bots:
        if bot.name == bot_name:
            return bot
    return None
