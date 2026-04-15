from fastapi.testclient import TestClient

import codara.gateway.app as gateway_app
from codara.config import get_settings
from codara.core.security import SecretStore
from tests.helpers import operator_headers


def test_toml_config_is_loaded_and_env_overrides(tmp_path, monkeypatch):
    config_file = tmp_path / "codara.toml"
    config_file.write_text(
        "\n".join(
            [
                'app_name = "Codara Test"',
                "port = 9001",
                'database_path = "custom.db"',
                'workspaces_root = "workspaces-root"',
                'isolated_envs_root = "shared-isolated"',
                'codex_billing_api_key = "sk-billing-test"',
                'codex_usage_endpoints = "https://example.test/usage-a,https://example.test/usage-b"',
                'codex_oauth_url = "https://example.test/oauth"',
                'gemini_usage_endpoints = "https://example.test/gemini-a,https://example.test/gemini-b"',
                'redis_url = "redis://localhost:6379/0"',
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
    assert settings.isolated_envs_root == str((tmp_path / "shared-isolated").resolve())
    assert settings.port == 9002
    assert settings.codex_billing_api_key == "sk-billing-test"
    assert settings.codex_usage_endpoints == "https://example.test/usage-a,https://example.test/usage-b"
    assert settings.codex_oauth_url == "https://example.test/oauth"
    assert settings.gemini_usage_endpoints == "https://example.test/gemini-a,https://example.test/gemini-b"

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
    config_path = config_dir / "codara.toml"
    config_path.write_text('secret_key = "config-secret"\n', encoding="utf-8")
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
