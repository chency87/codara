from fastapi.testclient import TestClient

import codara.gateway.app as gateway_app
from codara.core.models import ProviderType
from codara.database.manager import DatabaseManager
from tests.helpers import operator_headers


def test_usage_timeseries_endpoint_returns_daily_aggregates(tmp_path, monkeypatch):
    db_path = tmp_path / "codara.db"

    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()
    gateway_app.db_manager = DatabaseManager(str(db_path))

    user = gateway_app.db_manager.create_user(
        email="usage@example.com",
        display_name="Usage User",
        workspace_path=str(tmp_path / "workspaces" / "usage-user"),
        created_by="operator:test",
    )
    gateway_app.db_manager.record_user_usage(user.user_id, ProviderType.CODEX, 10, 30, cache_hit_tokens=5, request_count=2, period="2026-04-10")
    gateway_app.db_manager.record_user_usage(user.user_id, ProviderType.GEMINI, 20, 40, cache_hit_tokens=3, request_count=1, period="2026-04-10")
    gateway_app.db_manager.record_user_usage(user.user_id, ProviderType.CODEX, 5, 15, cache_hit_tokens=2, request_count=1, period="2026-04-11")

    client = TestClient(gateway_app.app)
    headers = operator_headers(client)

    resp = client.get("/management/v1/usage/timeseries", headers=headers, params={"days": 30})

    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert rows == [
        {
            "period": "2026-04-10",
            "input_tokens": 30,
            "output_tokens": 70,
            "cache_hit_tokens": 8,
            "request_count": 3,
        },
        {
            "period": "2026-04-11",
            "input_tokens": 5,
            "output_tokens": 15,
            "cache_hit_tokens": 2,
            "request_count": 1,
        },
    ]
