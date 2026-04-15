from __future__ import annotations

import os
from pathlib import Path

from codara.core.models import ProviderType
from codara.config import get_config_dir


class CredentialVault:
    """Filesystem-backed credential vault and CLI auth materializer."""

    def __init__(self):
        self.root = get_config_dir() / "credentials"
        self.root.mkdir(parents=True, exist_ok=True)

    def _provider_dir(self, provider: ProviderType) -> Path:
        path = self.root / provider.value
        path.mkdir(parents=True, exist_ok=True)
        return path

    def account_path(self, provider: ProviderType, account_id: str) -> Path:
        return self._provider_dir(provider) / f"{account_id}.cred"

    def save_credential(self, provider: ProviderType, account_id: str, credential: str) -> Path:
        path = self.account_path(provider, account_id)
        path.write_text(credential, encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return path

    def load_credential(self, provider: ProviderType, account_id: str) -> str | None:
        path = self.account_path(provider, account_id)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def cli_auth_path(self, provider: ProviderType) -> Path:
        return self._target_auth_path(provider)

    def load_cli_credential(self, provider: ProviderType) -> str | None:
        path = self.cli_auth_path(provider)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def materialize_to_cli(self, provider: ProviderType, credential: str) -> Path:
        target = self._target_auth_path(provider)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(credential, encoding="utf-8")
        try:
            target.chmod(0o600)
        except OSError:
            pass
        return target

    def _target_auth_path(self, provider: ProviderType) -> Path:
        env_map = {
            ProviderType.CODEX: "UAG_CODEX_AUTH_PATH",
            ProviderType.GEMINI: "UAG_GEMINI_AUTH_PATH",
            ProviderType.OPENCODE: "UAG_OPENCODE_AUTH_PATH",
        }
        default_map = {
            ProviderType.CODEX: Path.home() / ".codex" / "auth.json",
            ProviderType.GEMINI: Path.home() / ".gemini" / "oauth_creds.json",
            ProviderType.OPENCODE: Path.home() / ".opencode" / "auth.json",
        }
        override = os.getenv(env_map[provider])
        if override:
            return Path(override).expanduser()
        return default_map[provider]
