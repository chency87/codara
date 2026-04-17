import base64
import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import codara.gateway.app as gateway_app
from codara.accounts.pool import AccountPool
from codara.core.models import Account, AuthType, ProviderType
from codara.database.manager import DatabaseManager
from tests.helpers import operator_headers


def test_cli_account_selection_updates_pool_preference(tmp_path, monkeypatch):
    db_path = tmp_path / "codara.db"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.db_manager = DatabaseManager(str(db_path))

    low_usage = Account(
        account_id="codex-low",
        provider=ProviderType.CODEX,
        auth_type=AuthType.API_KEY,
        label="Low usage",
        usage_tpm=5_000,
        hourly_limit=50_000,
        weekly_limit=1_000_000,
    )
    selected = Account(
        account_id="codex-selected",
        provider=ProviderType.CODEX,
        auth_type=AuthType.API_KEY,
        label="Selected for CLI",
        usage_tpm=25_000,
        hourly_limit=50_000,
        weekly_limit=1_000_000,
    )
    pool = AccountPool(gateway_app.db_manager)
    pool.register_account(low_usage, "sk-low-credential")
    pool.register_account(selected, "sk-selected-credential")

    client = TestClient(gateway_app.app)
    headers = operator_headers(client)

    select_resp = client.post("/management/v1/accounts/codex-selected/select", headers=headers)
    assert select_resp.status_code == 200
    assert select_resp.json()["data"]["cli_primary"] is True
    assert select_resp.json()["data"]["allocation"] == "cli-primary"
    assert select_resp.json()["data"]["cli_name"] == "codex"

    acquired = pool.acquire_account(ProviderType.CODEX)
    assert acquired is not None
    assert acquired.account_id == "codex-selected"


def test_cli_account_selection_does_not_materialize_managed_codex_to_host_auth_path(tmp_path, monkeypatch):
    db_path = tmp_path / "codara.db"
    codex_auth_path = tmp_path / "codex-home" / ".codex" / "auth.json"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    monkeypatch.setenv("UAG_CODEX_AUTH_PATH", str(codex_auth_path))
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.db_manager = DatabaseManager(str(db_path))

    account = Account(
        account_id="codex-uploaded",
        provider=ProviderType.CODEX,
        auth_type=AuthType.OAUTH_SESSION,
        label="Codex Uploaded",
    )
    pool = AccountPool(gateway_app.db_manager)
    pool.register_account(account, '{"auth_mode":"chatgpt","tokens":{"access_token":"abc"}}')

    client = TestClient(gateway_app.app)
    headers = operator_headers(client)

    select_resp = client.post("/management/v1/accounts/codex-uploaded/select", headers=headers)
    assert select_resp.status_code == 200
    data = select_resp.json()["data"]
    assert data["activated_auth_path"] is None
    assert not codex_auth_path.exists()


def test_updating_cli_primary_credential_does_not_re_materialize_host_auth_path(tmp_path, monkeypatch):
    db_path = tmp_path / "codara.db"
    codex_auth_path = tmp_path / "codex-home" / ".codex" / "auth.json"

    monkeypatch.setenv("UAG_CODEX_AUTH_PATH", str(codex_auth_path))
    db = DatabaseManager(str(db_path))
    pool = AccountPool(db)
    account = Account(
        account_id="codex-primary",
        provider=ProviderType.CODEX,
        auth_type=AuthType.OAUTH_SESSION,
        label="Primary",
        cli_primary=True,
    )

    pool.register_account(account, '{"tokens":{"access_token":"old-token"}}')
    assert not codex_auth_path.exists()

    updated = pool.update_credential("codex-primary", '{"tokens":{"access_token":"new-token"}}')

    assert updated is not None
    assert not codex_auth_path.exists()


def test_account_pool_registers_metadata_from_json_blob(tmp_path):
    db_path = tmp_path / "codara.db"
    db = DatabaseManager(str(db_path))
    pool = AccountPool(db)
    account = Account(
        account_id="codex-oauth",
        provider=ProviderType.CODEX,
        auth_type=AuthType.OAUTH_SESSION,
        label="Codex OAuth",
    )
    pool.register_account(
        account,
        """
        {
          "email": "alice@example.com",
          "auth_index": "a1b2c3d4e5f67890",
          "nested": {
            "account": "workspace-1"
          }
        }
        """,
    )

    stored = db.get_account("codex-oauth")
    assert stored is not None
    assert stored.credential_id == "alice@example.com"
    assert stored.auth_index == "a1b2c3d4e5f67890"


def test_account_pool_extracts_access_token_expiry_from_jwt(tmp_path):
    db_path = tmp_path / "codara.db"
    db = DatabaseManager(str(db_path))
    pool = AccountPool(db)
    account = Account(
        account_id="codex-exp-jwt",
        provider=ProviderType.CODEX,
        auth_type=AuthType.OAUTH_SESSION,
        label="Codex Exp JWT",
    )
    exp = int((datetime.now(timezone.utc) + timedelta(hours=2)).timestamp())
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    token = f"header.{payload}.sig"
    pool.register_account(account, json.dumps({"tokens": {"access_token": token, "refresh_token": "rtk"}}))

    stored = db.get_account("codex-exp-jwt")
    assert stored is not None
    assert stored.access_token_expires_at is not None
    assert int(stored.access_token_expires_at.timestamp()) == exp


def test_account_pool_keeps_wham_oauth_accounts_selectable(tmp_path):
    db_path = tmp_path / "codara.db"
    db = DatabaseManager(str(db_path))
    pool = AccountPool(db)
    account = Account(
        account_id="codex-wham",
        provider=ProviderType.CODEX,
        auth_type=AuthType.OAUTH_SESSION,
        label="Codex WHAM",
        cli_primary=True,
        usage_source="wham",
        hourly_used_pct=25.0,
        weekly_used_pct=40.0,
        usage_tpm=50_000,
        usage_rpd=999_999,
    )

    pool.register_account(account, '{"tokens":{"access_token":"oauth-token"}}')

    acquired = pool.acquire_account(ProviderType.CODEX)

    assert acquired is not None
    assert acquired.account_id == "codex-wham"


def test_cli_primary_falls_back_when_headroom_is_nearly_exhausted(tmp_path):
    db_path = tmp_path / "codara.db"
    db = DatabaseManager(str(db_path))
    pool = AccountPool(db)
    depleted_primary = Account(
        account_id="codex-primary",
        provider=ProviderType.CODEX,
        auth_type=AuthType.OAUTH_SESSION,
        label="Primary",
        cli_primary=True,
        usage_source="wham",
        hourly_used_pct=97.0,
        weekly_used_pct=91.0,
    )
    healthier_backup = Account(
        account_id="codex-backup",
        provider=ProviderType.CODEX,
        auth_type=AuthType.OAUTH_SESSION,
        label="Backup",
        usage_source="wham",
        hourly_used_pct=22.0,
        weekly_used_pct=35.0,
    )

    pool.register_account(depleted_primary, '{"tokens":{"access_token":"primary-token","refresh_token":"refresh"}}')
    pool.register_account(healthier_backup, '{"tokens":{"access_token":"backup-token","refresh_token":"refresh"}}')

    acquired = pool.acquire_account(ProviderType.CODEX)

    assert acquired is not None
    assert acquired.account_id == "codex-backup"
    promoted = db.get_cli_primary_account(ProviderType.CODEX)
    assert promoted is not None
    assert promoted.account_id == "codex-backup"


def test_account_pool_promotes_single_active_account_when_none_selected(tmp_path):
    db = DatabaseManager(str(tmp_path / "codara.db"))
    pool = AccountPool(db)
    lower = Account(
        account_id="codex-lower",
        provider=ProviderType.CODEX,
        auth_type=AuthType.API_KEY,
        label="Lower",
        usage_tpm=30,
        tpm_limit=100,
    )
    higher = Account(
        account_id="codex-higher",
        provider=ProviderType.CODEX,
        auth_type=AuthType.API_KEY,
        label="Higher",
        usage_tpm=10,
        tpm_limit=100,
    )
    pool.register_account(lower, "sk-lower")
    pool.register_account(higher, "sk-higher")

    acquired = pool.acquire_account(ProviderType.CODEX)

    assert acquired is not None
    assert acquired.account_id == "codex-higher"
    promoted = db.get_cli_primary_account(ProviderType.CODEX)
    assert promoted is not None
    assert promoted.account_id == "codex-higher"


def test_release_account_rotates_single_active_account_when_headroom_drops_below_threshold(tmp_path):
    db = DatabaseManager(str(tmp_path / "codara.db"))
    pool = AccountPool(db)
    primary = Account(
        account_id="codex-primary",
        provider=ProviderType.CODEX,
        auth_type=AuthType.API_KEY,
        label="Primary",
        cli_primary=True,
        usage_tpm=94,
        tpm_limit=100,
    )
    backup = Account(
        account_id="codex-backup",
        provider=ProviderType.CODEX,
        auth_type=AuthType.API_KEY,
        label="Backup",
        usage_tpm=10,
        tpm_limit=100,
    )
    pool.register_account(primary, "sk-primary")
    pool.register_account(backup, "sk-backup")

    pool.release_account("codex-primary", tokens_used=2)

    promoted = db.get_cli_primary_account(ProviderType.CODEX)
    assert promoted is not None
    assert promoted.account_id == "codex-backup"


def test_account_pool_ignores_system_inventory_accounts(tmp_path):
    db_path = tmp_path / "codara.db"
    db = DatabaseManager(str(db_path))
    pool = AccountPool(db)
    system_account = Account(
        account_id="codex-oauth",
        provider=ProviderType.CODEX,
        auth_type=AuthType.OAUTH_SESSION,
        label="Codex OAuth Account",
        inventory_source="system",
    )
    vault_account = Account(
        account_id="codex-vault",
        provider=ProviderType.CODEX,
        auth_type=AuthType.OAUTH_SESSION,
        label="Codex Vault",
    )

    pool.register_account(system_account, '{"tokens":{"access_token":"system"}}')
    pool.register_account(vault_account, '{"tokens":{"access_token":"vault","refresh_token":"refresh"}}')

    acquired = pool.acquire_account(ProviderType.CODEX)

    assert acquired is not None
    assert acquired.account_id == "codex-vault"


def test_codex_oauth_keeps_managed_credential_without_using_host_auth_file(tmp_path, monkeypatch):
    db_path = tmp_path / "codara.db"
    cli_auth_path = tmp_path / "codex-home" / ".codex" / "auth.json"
    cli_auth_path.parent.mkdir(parents=True, exist_ok=True)
    cli_auth_path.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {
                    "access_token": "real-access",
                    "refresh_token": "real-refresh",
                    "id_token": "real-id",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("UAG_CODEX_AUTH_PATH", str(cli_auth_path))

    db = DatabaseManager(str(db_path))
    pool = AccountPool(db)
    account = Account(
        account_id="codex-sync",
        provider=ProviderType.CODEX,
        auth_type=AuthType.OAUTH_SESSION,
        label="Codex Sync",
        cli_primary=True,
    )

    pool.register_account(account, '{"tokens":{"access_token":"stale-access"}}')

    credential = pool.get_credential("codex-sync")

    assert credential is not None
    payload = json.loads(credential)
    assert payload["tokens"]["access_token"] == "stale-access"
    assert "refresh_token" not in payload["tokens"]
    assert "id_token" not in payload["tokens"]


def test_activate_for_cli_is_noop_for_managed_codex_accounts(tmp_path, monkeypatch):
    db_path = tmp_path / "codara.db"
    cli_auth_path = tmp_path / "codex-home" / ".codex" / "auth.json"
    cli_auth_path.parent.mkdir(parents=True, exist_ok=True)
    cli_auth_path.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {
                    "access_token": "real-access",
                    "refresh_token": "real-refresh",
                    "id_token": "real-id",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("UAG_CODEX_AUTH_PATH", str(cli_auth_path))

    db = DatabaseManager(str(db_path))
    pool = AccountPool(db)
    account = Account(
        account_id="codex-select",
        provider=ProviderType.CODEX,
        auth_type=AuthType.OAUTH_SESSION,
        label="Codex Select",
    )
    pool.register_account(account, '{"tokens":{"access_token":"stale-access"}}')
    db.set_cli_primary_account("codex-select")

    path = pool.activate_for_cli("codex-select")

    assert path is None
    payload = json.loads(cli_auth_path.read_text(encoding="utf-8"))
    assert payload["tokens"]["access_token"] == "real-access"
    assert payload["tokens"]["refresh_token"] == "real-refresh"
    assert payload["tokens"]["id_token"] == "real-id"


def test_get_credential_does_not_restore_host_auth_when_stored_copy_is_richer(tmp_path, monkeypatch):
    db_path = tmp_path / "codara.db"
    cli_auth_path = tmp_path / "codex-home" / ".codex" / "auth.json"
    cli_auth_path.parent.mkdir(parents=True, exist_ok=True)
    cli_auth_path.write_text('{"tokens":{"access_token":"stale-access"}}', encoding="utf-8")
    monkeypatch.setenv("UAG_CODEX_AUTH_PATH", str(cli_auth_path))

    db = DatabaseManager(str(db_path))
    pool = AccountPool(db)
    account = Account(
        account_id="codex-heal",
        provider=ProviderType.CODEX,
        auth_type=AuthType.OAUTH_SESSION,
        label="Codex Heal",
        cli_primary=True,
    )
    rich = json.dumps(
        {
            "auth_mode": "chatgpt",
            "tokens": {
                "access_token": "real-access",
                "refresh_token": "real-refresh",
                "id_token": "real-id",
            },
        }
    )
    pool.register_account(account, rich)
    cli_auth_path.write_text('{"tokens":{"access_token":"stale-access"}}', encoding="utf-8")

    credential = pool.get_credential("codex-heal")

    assert credential == rich
    healed = json.loads(cli_auth_path.read_text(encoding="utf-8"))
    assert healed["tokens"]["access_token"] == "stale-access"
    assert "refresh_token" not in healed["tokens"]
    assert "id_token" not in healed["tokens"]
