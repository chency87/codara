import logging
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import codara.gateway.app as gateway_app
from codara.config import get_settings
from codara.logging_setup import configure_logging
from codara.database.manager import DatabaseManager
from codara.orchestrator.engine import Orchestrator
from codara.runtime_log_store import RuntimeLogStore
from codara.trace_store import FileTraceStore
from codara.telemetry import record_event, start_trace
from tests.helpers import operator_headers


def test_trace_events_are_persisted_with_redaction(tmp_path, monkeypatch):
    monkeypatch.setenv("UAG_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("UAG_CONFIG_PATH", str(tmp_path / "missing.toml"))
    get_settings(force_reload=True)
    db = DatabaseManager(str(tmp_path / "telemetry.db"))

    with start_trace("unit.trace", component="tests.telemetry", db=db, request_id="req_test_1"):
        record_event(
            "unit.event",
            component="tests.telemetry",
            db=db,
            attributes={"api_token": "secret-value", "nested": {"credential_text": "raw-secret"}},
        )

    db.wait_for_traces()
    shard_files = sorted((tmp_path / "logs" / "traces" / "events").glob("**/*.jsonl"))
    index_files = sorted((tmp_path / "logs" / "traces" / "index").glob("**/root-spans.jsonl"))
    assert shard_files
    assert index_files
    traces = db.list_traces(limit=10)
    assert len(traces) == 1
    trace_id = traces[0]["trace_id"]
    events = db.get_trace_events(trace_id)
    event = next(row for row in events if row["kind"] == "event" and row["name"] == "unit.event")
    assert event["attributes"]["api_token"] == "***REDACTED***"
    assert event["attributes"]["nested"]["credential_text"] == "***REDACTED***"


def test_management_trace_endpoints_and_headers(tmp_path, monkeypatch):
    db_path = tmp_path / "telemetry-api.db"
    monkeypatch.setenv("API_TOKEN", "unit-test-secret")
    monkeypatch.setenv("UAG_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("UAG_CONFIG_PATH", str(tmp_path / "missing.toml"))
    get_settings(force_reload=True)
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.db_manager = DatabaseManager(str(db_path))
    gateway_app.orchestrator = Orchestrator(gateway_app.db_manager)
    gateway_app.clear_auth_caches()

    client = TestClient(gateway_app.app)
    headers = operator_headers(client, secret="unit-test-secret")

    health = client.get("/management/v1/health", headers=headers)
    assert health.status_code == 200
    trace_id = health.headers["X-Trace-Id"]
    request_id = health.headers["X-Request-Id"]
    assert trace_id.startswith("trc_")
    assert request_id.startswith("req_")

    gateway_app.db_manager.wait_for_traces()
    traces = client.get(
        "/management/v1/traces",
        headers=headers,
        params={"trace_id": trace_id},
    )
    assert traces.status_code == 200
    rows = traces.json()["data"]
    assert len(rows) == 1
    assert rows[0]["trace_id"] == trace_id
    assert rows[0]["request_id"] == request_id

    search_resp = client.get(
        "/management/v1/traces",
        headers=headers,
        params={"search": "http.request"},
    )
    assert search_resp.status_code == 200
    assert any(row["trace_id"] == trace_id for row in search_resp.json()["data"])

    future_resp = client.get(
        "/management/v1/traces",
        headers=headers,
        params={"since": (datetime.now(tz=timezone.utc) + timedelta(days=1)).isoformat()},
    )
    assert future_resp.status_code == 200
    assert future_resp.json()["data"] == []

    detail = client.get(f"/management/v1/traces/{trace_id}", headers=headers)
    assert detail.status_code == 200
    gateway_app.db_manager.wait_for_traces()
    events = detail.json()["data"]["events"]
    names = [row["name"] for row in events]
    assert "http.request" in names
    assert "http.request.received" in names
    assert "http.request.completed" in names


def test_dashboard_poll_header_suppresses_successful_http_trace(tmp_path, monkeypatch):
    db_path = tmp_path / "quiet-poll.db"
    monkeypatch.setenv("API_TOKEN", "unit-test-secret")
    monkeypatch.setenv("UAG_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("UAG_CONFIG_PATH", str(tmp_path / "missing.toml"))
    get_settings(force_reload=True)
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.db_manager = DatabaseManager(str(db_path))
    gateway_app.orchestrator = Orchestrator(gateway_app.db_manager)
    gateway_app.clear_auth_caches()

    client = TestClient(gateway_app.app)
    headers = {
        "Authorization": "Bearer unit-test-secret",
        "X-Codara-Dashboard-Poll": "true",
    }

    quiet_resp = client.get("/management/v1/health", headers=headers)
    assert quiet_resp.status_code == 200
    assert quiet_resp.headers["X-Request-Id"].startswith("req_")
    assert quiet_resp.headers["X-Trace-Id"] == ""

    gateway_app.db_manager.wait_for_traces()
    assert gateway_app.db_manager.list_traces(limit=10) == []

    normal_resp = client.get("/management/v1/health", headers={"Authorization": "Bearer unit-test-secret"})
    assert normal_resp.status_code == 200
    normal_trace_id = normal_resp.headers["X-Trace-Id"]
    assert normal_trace_id.startswith("trc_")

    gateway_app.db_manager.wait_for_traces()
    traces = gateway_app.db_manager.list_traces(limit=10, trace_id=normal_trace_id)
    assert len(traces) == 1


def test_management_runtime_logs_endpoint_reads_datetime_shards(tmp_path, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "unit-test-secret")
    monkeypatch.setenv("UAG_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("UAG_CONFIG_PATH", str(tmp_path / "missing.toml"))
    settings = get_settings(force_reload=True)
    gateway_app.settings = settings
    configure_logging(settings, force=True)

    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.db_manager = DatabaseManager(str(tmp_path / "logs-api.db"))
    gateway_app.orchestrator = Orchestrator(gateway_app.db_manager)
    gateway_app.clear_auth_caches()

    with start_trace("logs.api.test", component="tests.logs"):
        logging.getLogger("codara.tests.logs").warning("runtime logs query test")

    client = TestClient(gateway_app.app)
    headers = operator_headers(client, secret="unit-test-secret")
    resp = client.get("/management/v1/logs", headers=headers, params={"search": "runtime logs query test"})

    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert rows
    assert any(row["message"] == "runtime logs query test" for row in rows)

    future = (datetime.now(tz=timezone.utc) + timedelta(days=1)).isoformat()
    filtered = client.get("/management/v1/logs", headers=headers, params={"since": future})
    assert filtered.status_code == 200
    assert not any(row["message"] == "runtime logs query test" for row in filtered.json()["data"])


def test_relative_observability_roots_resolve_under_logs_root(tmp_path, monkeypatch):
    config_path = tmp_path / "codara.toml"
    config_path.write_text(
        "\n".join(
            [
                "[logging]",
                'root = "logs-root"',
                'runtime_root = "runtime-store"',
                "",
                "[telemetry]",
                'trace_root = "trace-store"',
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("UAG_CONFIG_PATH", str(config_path))
    settings = get_settings(force_reload=True)

    runtime_root = configure_logging(settings, force=True)
    db = DatabaseManager(str(tmp_path / "telemetry.db"))
    db.wait_for_traces()

    assert runtime_root == (tmp_path / "logs-root" / "runtime-store").resolve()
    assert db._trace_store is not None
    assert db._trace_store.root == (tmp_path / "logs-root" / "trace-store").resolve()


def test_file_backed_observability_pruning_removes_old_records(tmp_path):
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    old_ms = now_ms - 10 * 24 * 60 * 60 * 1000
    cutoff_ms = now_ms - 24 * 60 * 60 * 1000

    trace_store = FileTraceStore(str(tmp_path / "traces"))
    trace_store.append_batch([
        {
            "event_id": "evt_old",
            "trace_id": "trc_old",
            "span_id": "spn_old",
            "parent_span_id": None,
            "kind": "span",
            "name": "old.trace",
            "component": "tests",
            "level": "INFO",
            "status": "ok",
            "request_id": "req_old",
            "started_at": old_ms,
            "ended_at": old_ms + 10,
            "duration_ms": 10,
            "attributes": {},
        },
        {
            "event_id": "evt_new",
            "trace_id": "trc_new",
            "span_id": "spn_new",
            "parent_span_id": None,
            "kind": "span",
            "name": "new.trace",
            "component": "tests",
            "level": "INFO",
            "status": "ok",
            "request_id": "req_new",
            "started_at": now_ms,
            "ended_at": now_ms + 10,
            "duration_ms": 10,
            "attributes": {},
        },
    ])

    result = trace_store.prune_older_than(cutoff_ms)

    assert result["records_deleted"] == 2
    remaining = trace_store.list_traces(limit=10)
    assert [row["trace_id"] for row in remaining] == ["trc_new"]

    runtime_store = RuntimeLogStore(str(tmp_path / "runtime"))
    old_dt = datetime.fromtimestamp(old_ms / 1000, tz=timezone.utc)
    new_dt = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
    log_path = runtime_store.root / f"{old_dt.year:04d}" / f"{old_dt.month:02d}" / f"{old_dt.day:02d}" / f"{old_dt.hour:02d}.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "\n".join([
            '{"timestamp":"' + old_dt.isoformat() + '","level":"INFO","logger":"tests","message":"old log"}',
            '{"timestamp":"' + new_dt.isoformat() + '","level":"INFO","logger":"tests","message":"new log"}',
        ]) + "\n",
        encoding="utf-8",
    )

    runtime_result = runtime_store.prune_older_than(cutoff_ms)

    assert runtime_result["records_deleted"] == 1
    rows = runtime_store.list_logs(limit=10)
    assert [row["message"] for row in rows] == ["new log"]
