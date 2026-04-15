import hashlib
import json
import logging
import os
import time
import shutil
import tempfile
from pathlib import Path
from typing import Protocol, List, Optional, Any
from codara.core.models import Session, Message, TurnResult, AuthType
from codara.database.manager import DatabaseManager
from codara.accounts.pool import AccountPool
from codara.config import get_settings

logger = logging.getLogger(__name__)

class ProviderAdapter(Protocol):
    async def send_turn(self, session: Session, messages: List[Message], provider_model: str) -> TurnResult:
        ...

    async def resume_session(self, backend_id: str) -> Session:
        ...

    async def terminate_session(self, backend_id: str) -> None:
        ...

    async def collect_usage(self, account: Any, credential: Optional[str], settings: Any) -> Optional[dict]:
        ...

    async def list_models(self, settings: Any) -> dict[str, Any]:
        ...

class CliRuntimeMixin:
    _MODEL_CACHE_TTL_SECONDS = 300.0

    def __init__(self):
        self._executable_paths: dict[str, str] = {}
        self._models_cache: Optional[tuple[float, dict[str, Any]]] = None

    def _resolve_executable(self, executable_name: str) -> str:
        cached = self._executable_paths.get(executable_name)
        if cached and Path(cached).exists():
            return cached
        resolved = shutil.which(executable_name)
        if resolved is None:
            raise RuntimeError(f"{executable_name} CLI is not installed on the local system")
        self._executable_paths[executable_name] = resolved
        return resolved

    def _get_cached_model_listing(self) -> Optional[dict[str, Any]]:
        if not self._models_cache:
            return None
        expires_at, payload = self._models_cache
        if expires_at <= time.monotonic():
            self._models_cache = None
            return None
        cached_payload = dict(payload)
        cached_payload["cached"] = True
        return cached_payload

    def _store_model_listing(self, payload: dict[str, Any]) -> dict[str, Any]:
        stored = dict(payload)
        stored["cached"] = False
        self._models_cache = (time.monotonic() + self._MODEL_CACHE_TTL_SECONDS, dict(stored))
        return stored

class ConfigIsolationMixin:
    """Provides file-system isolation for CLI adapters by redirecting HOME."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager
        self.pool = AccountPool(db_manager) if db_manager else None

    def setup_isolated_env(
        self,
        provider_name: str,
        account_id: str,
        session: Optional[Session] = None,
    ) -> tuple[str, dict]:
        """Create an isolated HOME for CLI execution.

        Session-bound executions use a stable provider/account-scoped home so
        pooled accounts can reuse provider helper state across users without
        touching the operator's real login directory.
        """
        home_path = self._resolve_isolated_home(provider_name, account_id, session=session)
        home_path.mkdir(parents=True, exist_ok=True)
        config_dir = home_path / f".{provider_name}"
        config_dir.mkdir(parents=True, exist_ok=True)

        filename = "oauth_creds.json" if provider_name == "gemini" else "auth.json"

        env = os.environ.copy()
        env["HOME"] = str(home_path)

        if self.pool:
            credential = self.pool.get_credential(account_id)
            if credential:
                self._write_provider_credential(
                    provider_name,
                    account_id,
                    credential,
                    config_dir / filename,
                    env,
                )

        return str(home_path), env

    def cleanup_isolated_env(self, temp_dir: str):
        shutil.rmtree(temp_dir, ignore_errors=True)

    def _resolve_isolated_home(
        self,
        provider_name: str,
        account_id: str,
        session: Optional[Session] = None,
    ) -> Path:
        if session is not None:
            return self._resolve_account_scoped_home(provider_name, account_id)

        settings = get_settings()
        if settings.isolated_envs_root:
            base_dir = Path(settings.isolated_envs_root).resolve()
            base_dir.mkdir(parents=True, exist_ok=True)
            temp_dir = tempfile.mkdtemp(prefix=f"{provider_name}-", dir=str(base_dir))
            return Path(temp_dir)
        return Path(tempfile.mkdtemp(prefix=f"uag-{provider_name}-"))

    def _resolve_account_scoped_home(self, provider_name: str, account_id: str) -> Path:
        settings = get_settings()
        base_dir = Path(settings.isolated_envs_root).resolve()
        base_dir.mkdir(parents=True, exist_ok=True)
        scope = hashlib.sha256(
            f"{provider_name}:{account_id}".encode("utf-8")
        ).hexdigest()[:16]
        return base_dir / provider_name / scope

    def sync_provider_state(
        self,
        provider_name: str,
        source_account_id: str,
        target_account_id: str,
        patterns: tuple[str, ...],
    ) -> bool:
        source_home = self._resolve_account_scoped_home(provider_name, source_account_id)
        if not source_home.exists():
            return False
        target_home = self._resolve_account_scoped_home(provider_name, target_account_id)
        source_config = source_home / f".{provider_name}"
        if not source_config.exists():
            return False
        target_config = target_home / f".{provider_name}"
        target_config.mkdir(parents=True, exist_ok=True)

        copied = False
        for pattern in patterns:
            for source_path in source_config.glob(pattern):
                relative_path = source_path.relative_to(source_config)
                destination = target_config / relative_path
                if source_path.is_dir():
                    shutil.copytree(source_path, destination, dirs_exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source_path, destination)
                copied = True
        return copied

    def _write_provider_credential(
        self,
        provider_name: str,
        account_id: str,
        credential: str,
        destination: Path,
        env: dict[str, str],
    ) -> None:
        if provider_name != "codex":
            self._write_text_if_changed(destination, credential)
            return

        normalized = self._normalize_codex_credential(account_id, credential, env)
        if normalized is not None:
            self._write_text_if_changed(destination, normalized)

    def _normalize_codex_credential(
        self,
        account_id: str,
        credential: str,
        env: dict[str, str],
    ) -> Optional[str]:
        stripped = credential.strip()
        if not stripped:
            return None
        if not (stripped.startswith("{") or stripped.startswith("[")):
            env["OPENAI_API_KEY"] = stripped
            return None

        payload = self._try_parse_json(stripped)
        if not isinstance(payload, dict):
            return stripped

        access_token = self._deep_find_token(payload, {"access_token", "accessToken"})
        refresh_token = self._deep_find_token(payload, {"refresh_token", "refreshToken"})
        id_token = self._deep_find_token(payload, {"id_token", "idToken"})
        account_token_id = self._deep_find_token(payload, {"account_id", "accountId"})

        if not any([access_token, refresh_token, id_token]):
            api_key = self._deep_find_token(payload, {"api_key", "apiKey", "openai_api_key"})
            if api_key:
                env["OPENAI_API_KEY"] = api_key
                return None
            return stripped

        normalized = dict(payload)
        tokens = normalized.get("tokens")
        if not isinstance(tokens, dict):
            tokens = {}
        else:
            tokens = dict(tokens)

        if access_token:
            tokens["access_token"] = access_token
        if refresh_token:
            tokens["refresh_token"] = refresh_token
        if id_token:
            tokens["id_token"] = id_token
        if account_token_id:
            tokens["account_id"] = account_token_id

        normalized["tokens"] = tokens
        normalized.setdefault("auth_mode", "chatgpt")

        account = self.db.get_account(account_id) if self.db else None
        auth_type_value = getattr(getattr(account, "auth_type", None), "value", str(getattr(account, "auth_type", "")))
        if auth_type_value == AuthType.OAUTH_SESSION.value and not tokens.get("id_token"):
            raise RuntimeError(
                "Codex OAuth credential is missing id_token. Re-import the full Codex CLI auth.json from a logged-in session."
            )

        return json.dumps(normalized)

    def _write_text_if_changed(self, destination: Path, content: str) -> None:
        if destination.exists():
            try:
                if destination.read_text(encoding="utf-8") == content:
                    return
            except OSError:
                pass
        destination.write_text(content, encoding="utf-8")

    def _try_parse_json(self, value: str) -> Optional[Any]:
        stripped = value.strip()
        if not (stripped.startswith("{") or stripped.startswith("[")):
            return None
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return None

    def _deep_find_token(self, payload: Any, keys: set[str]) -> Optional[str]:
        if isinstance(payload, dict):
            # Check all keys at the current level first
            for key in keys:
                item = payload.get(key)
                if isinstance(item, str) and item.strip():
                    return item.strip()
            
            # If not found, recurse into values
            for item in payload.values():
                found = self._deep_find_token(item, keys)
                if found:
                    return found
        elif isinstance(payload, list):
            for item in payload:
                found = self._deep_find_token(item, keys)
                if found:
                    return found
        return None
