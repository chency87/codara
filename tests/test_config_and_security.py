from fastapi.testclient import TestClient

import amesh.gateway.app as gateway_app
from amesh.config import get_settings
from amesh.core.security import SecretStore
from tests.helpers import operator_headers


def test_toml_config_is_loaded_and_env_overrides(tmp_path, monkeypatch):
    config_file = tmp_path / "amesh.toml"
    config_file.write_text(
        "\n".join(
            [
                "[app]",
                'name = "Codara Test"',
                "",
                "[server]",
                "port = 9001",
                "",
                "[database]",
                'path = "custom.db"',
                "",
                "[workspace]",
                'root = "workspaces-root"',
                "",
                "[logging]",
                'runtime_root = "runtime-store"',
                "retention_days = 12",
                "",
                "[providers.codex]",
                "stall_timeout_seconds = 111",
                "",
                "[providers.gemini]",
                "stall_timeout_seconds = 222",
                "",
                "[providers.opencode]",
                "stall_timeout_seconds = 333",
                "",
                "[release]",
                "enabled = true",
                'repository = "amesh/amesh"',
                'api_base_url = "https://api.github.test"',
                "check_timeout_seconds = 7",
                "check_cache_ttl_seconds = 99",
                "",
                "[infra]",
                'redis_url = "redis://localhost:6379/0"',
                "",
                "[telemetry]",
                'persistence_backend = "file"',
                'trace_root = "trace-store"',
                "trace_retention_days = 9",
                "",
                "[channels.telegram]",
                "enabled = true",
                'receive_mode = "webhook"',
                "mention_only = true",
                "",
                "[[channels.telegram.bots]]",
                'name = "engineering-bot"',
                'token = "bot-token-1"',
                'webhook_secret = "bot-secret-1"',
                'username = "eng_bot"',
                "",
                "[[channels.telegram.bots]]",
                'name = "ops-bot"',
                'token = "bot-token-2"',
            ]
        )
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("UAG_PORT", "9002")

    gateway_app.clear_auth_caches()
    settings = get_settings(force_reload=True)

    assert settings.app_name == "Codara Test"
    assert settings.database_path == str((tmp_path / "custom.db").resolve())
    assert settings.workspaces_root == str((tmp_path / "workspaces-root").resolve())
    assert settings.port == 9002
    assert settings.codex_stall_timeout_seconds == 111
    assert settings.gemini_stall_timeout_seconds == 222
    assert settings.opencode_stall_timeout_seconds == 333
    assert settings.release_check_enabled is True
    assert settings.release_repository == "amesh/amesh"
    assert settings.release_api_base_url == "https://api.github.test"
    assert settings.release_check_timeout_seconds == 7
    assert settings.release_check_cache_ttl_seconds == 99
    assert settings.telemetry_persistence_backend == "file"
    assert settings.telemetry_trace_root == "trace-store"
    assert settings.telemetry_trace_retention_days == 9
    assert settings.runtime_log_root == "runtime-store"
    assert settings.log_retention_days == 12
    assert settings.channels.telegram.enabled is True
    assert settings.channels.telegram.receive_mode == "webhook"
    assert settings.channels.telegram.mention_only is True
    assert [bot.name for bot in settings.channels.telegram.bots] == ["engineering-bot", "ops-bot"]
    assert settings.channels.telegram.bots[0].webhook_secret == "bot-secret-1"

def test_secret_store_persists_generated_key(tmp_path):
    key_path = tmp_path / "master.key"

    first_store = SecretStore(key_path=str(key_path))
    ciphertext = first_store.encrypt("super-secret-value")

    second_store = SecretStore(key_path=str(key_path))

    assert second_store.decrypt(ciphertext) == "super-secret-value"
    assert key_path.exists()


def test_management_routes_require_operator_token(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "operator-passkey")
    gateway_app.settings.secret_key = "operator-passkey"
    gateway_app.clear_auth_caches()
    client = TestClient(gateway_app.app)

    unauthorized = client.get("/management/v1/health")
    assert unauthorized.status_code == 401

    missing_passkey = client.post("/management/v1/auth/token")
    assert missing_passkey.status_code == 401

    headers = operator_headers(client, secret="operator-passkey")
    authorized = client.get(
        "/management/v1/health",
        headers=headers,
    )

    assert authorized.status_code == 200
    assert authorized.json()["ok"] is True

    # After login, the server sets HttpOnly cookies, so browser-style requests without
    # an Authorization header should also be authorized.
    cookie_authorized = client.get("/management/v1/health")
    assert cookie_authorized.status_code == 200


def test_management_login_reads_api_token_from_dotenv_without_restart(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("API_TOKEN=dotenv-passkey\n", encoding="utf-8")
    monkeypatch.delenv("API_TOKEN", raising=False)
    monkeypatch.delenv("UAG_MGMT_SECRET", raising=False)
    gateway_app.settings.secret_key = "stale-secret"
    gateway_app.clear_auth_caches()

    client = TestClient(gateway_app.app)
    headers = operator_headers(client, secret="dotenv-passkey")

    authorized = client.get("/management/v1/health", headers=headers)
    assert authorized.status_code == 200


def test_management_login_reads_api_token_next_to_active_config(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / "amesh.toml"
    config_path.write_text("[server]\nsecret_key = \"config-secret\"\n", encoding="utf-8")
    (config_dir / ".env").write_text("API_TOKEN=config-dir-passkey\n", encoding="utf-8")

    monkeypatch.setenv("UAG_CONFIG_PATH", str(config_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("API_TOKEN", raising=False)
    monkeypatch.delenv("UAG_MGMT_SECRET", raising=False)
    gateway_app.settings.secret_key = "stale-secret"
    gateway_app.clear_auth_caches()

    client = TestClient(gateway_app.app)
    headers = operator_headers(client, secret="config-dir-passkey")

    authorized = client.get("/management/v1/health", headers=headers)
    assert authorized.status_code == 200
