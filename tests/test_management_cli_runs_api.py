from fastapi.testclient import TestClient

import amesh.gateway.app as gateway_app
from amesh.database.manager import DatabaseManager
from amesh.cli_run_store import CliRunStore
from tests.helpers import operator_headers


def test_management_cli_runs_list_and_stream(tmp_path, monkeypatch):
    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()

    # Point capture root at a temp directory for this test.
    logs_root = tmp_path / "logs"
    logs_root.mkdir()
    monkeypatch.setattr(gateway_app.settings, "logs_root", str(logs_root))
    monkeypatch.setattr(gateway_app.settings, "cli_capture_enabled", True)
    monkeypatch.setattr(gateway_app.settings, "cli_capture_root", "cli-runs")

    gateway_app.db_manager = DatabaseManager(str(tmp_path / "amesh.db"))

    store = CliRunStore(settings=gateway_app.settings)
    session_id = "ses_test"
    provider = "codex"
    capture = store.allocate_run(provider=provider, session_id=session_id)
    store.write_meta(
        capture.meta_path,
        {
            "run_id": capture.run_id,
            "provider": provider,
            "session_id": session_id,
            "cwd": str(tmp_path),
            "command": ["codex", "exec", "-"],
            "status": "running",
            "started_at": "2026-04-26T00:00:00+00:00",
            "ended_at": None,
            "exit_code": None,
            "error": None,
        },
    )
    capture.stdout_path.write_text("hello stdout\n", encoding="utf-8")
    capture.stderr_path.write_text("hello stderr\n", encoding="utf-8")

    client = TestClient(gateway_app.app)
    headers = operator_headers(client, secret="unit-test-secret")

    listing = client.get(f"/management/v1/sessions/{session_id}/cli-runs", headers=headers)
    assert listing.status_code == 200
    rows = listing.json()["data"]
    assert len(rows) == 1
    assert rows[0]["provider"] == provider
    assert rows[0]["run_id"] == capture.run_id

    stdout = client.get(
        f"/management/v1/sessions/{session_id}/cli-runs/{provider}/{capture.run_id}/stdout",
        headers=headers,
    )
    assert stdout.status_code == 200
    assert "hello stdout" in stdout.text

    streamed = client.get(
        f"/management/v1/sessions/{session_id}/cli-runs/{provider}/{capture.run_id}/stderr/stream?follow=false",
        headers=headers,
    )
    assert streamed.status_code == 200
    assert "hello stderr" in streamed.text


def test_management_cli_runs_stream_follow_ends_when_meta_ended(tmp_path, monkeypatch):
    monkeypatch.setenv("UAG_MGMT_SECRET", "unit-test-secret")
    gateway_app.settings.secret_key = "unit-test-secret"
    gateway_app.clear_auth_caches()

    logs_root = tmp_path / "logs"
    logs_root.mkdir()
    monkeypatch.setattr(gateway_app.settings, "logs_root", str(logs_root))
    monkeypatch.setattr(gateway_app.settings, "cli_capture_enabled", True)
    monkeypatch.setattr(gateway_app.settings, "cli_capture_root", "cli-runs")

    gateway_app.db_manager = DatabaseManager(str(tmp_path / "amesh.db"))

    store = CliRunStore(settings=gateway_app.settings)
    session_id = "ses_test_follow"
    provider = "codex"
    capture = store.allocate_run(provider=provider, session_id=session_id)
    store.write_meta(
        capture.meta_path,
        {
            "run_id": capture.run_id,
            "provider": provider,
            "session_id": session_id,
            "cwd": str(tmp_path),
            "command": ["codex", "exec", "-"],
            "status": "success",
            "started_at": "2026-04-26T00:00:00+00:00",
            "ended_at": "2026-04-26T00:00:01+00:00",
            "exit_code": 0,
            "error": None,
        },
    )
    capture.stdout_path.write_text("final stdout\n", encoding="utf-8")

    client = TestClient(gateway_app.app)
    headers = operator_headers(client, secret="unit-test-secret")

    streamed = client.get(
        f"/management/v1/sessions/{session_id}/cli-runs/{provider}/{capture.run_id}/stdout/stream?follow=true&poll_ms=50",
        headers=headers,
    )
    assert streamed.status_code == 200
    assert "final stdout" in streamed.text
