from click.testing import CliRunner
import os

import amesh.cli.main as cli_main


def test_serve_skips_ui_build_by_default_when_assets_are_missing(tmp_path, monkeypatch):
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    # Intentionally leave ui directory missing to test "assets missing" scenario

    def fail_subprocess_run(*args, **kwargs):
        raise AssertionError("subprocess.run should not be called without --build-ui")

    observed = {}

    monkeypatch.setattr("subprocess.run", fail_subprocess_run)
    monkeypatch.setattr(cli_main.uvicorn, "run", lambda app, host, port: observed.update({"host": host, "port": port}))

    result = runner.invoke(cli_main.cli, ["serve", "--host", "127.0.0.1", "--port", "8123"])

    assert result.exit_code == 0
    assert "Warning: ui/ not found. Dashboard won't be available." in result.output
    assert observed == {"host": "127.0.0.1", "port": 8123}


def test_serve_warns_when_dashboard_build_is_stale(tmp_path, monkeypatch):
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ui" / "src").mkdir(parents=True)
    (tmp_path / "ui" / "dist").mkdir(parents=True)
    (tmp_path / "ui" / "package.json").write_text("{}", encoding="utf-8")

    dist_index = tmp_path / "ui" / "dist" / "index.html"
    src_file = tmp_path / "ui" / "src" / "App.tsx"
    dist_index.write_text("<html></html>", encoding="utf-8")
    src_file.write_text("export default function App() { return null; }\n", encoding="utf-8")
    os.utime(dist_index, (1, 1))
    os.utime(src_file, (2, 2))

    def fail_subprocess_run(*args, **kwargs):
        raise AssertionError("subprocess.run should not be called without --build-ui")

    observed = {}

    monkeypatch.setattr("subprocess.run", fail_subprocess_run)
    monkeypatch.setattr(cli_main.uvicorn, "run", lambda app, host, port: observed.update({"host": host, "port": port}))

    result = runner.invoke(cli_main.cli, ["serve", "--host", "127.0.0.1", "--port", "8123"])

    assert result.exit_code == 0
    assert "Dashboard build appears stale." in result.output
    assert observed == {"host": "127.0.0.1", "port": 8123}


def test_version_command_prints_current_version(monkeypatch):
    runner = CliRunner()
    monkeypatch.setattr(cli_main, "get_version", lambda: "9.8.7")

    result = runner.invoke(cli_main.cli, ["version"])

    assert result.exit_code == 0
    assert "Codara 9.8.7" in result.output


def test_version_command_checks_configured_release(monkeypatch):
    runner = CliRunner()
    monkeypatch.setattr(cli_main, "get_version", lambda: "1.0.0")
    monkeypatch.setattr(cli_main.settings, "release_check_enabled", True)
    monkeypatch.setattr(cli_main.settings, "release_repository", "amesh/amesh")
    monkeypatch.setattr(cli_main.settings, "release_api_base_url", "https://api.github.test")
    monkeypatch.setattr(cli_main.settings, "release_check_timeout_seconds", 1)

    class Result:
        status = "ok"
        update_available = True
        latest_version = "1.1.0"
        release_url = "https://github.test/release"
        error = None

    monkeypatch.setattr(cli_main, "check_for_update", lambda **kwargs: Result())

    result = runner.invoke(cli_main.cli, ["version", "--check"])

    assert result.exit_code == 0
    assert "Codara 1.0.0" in result.output
    assert "Update available: 1.1.0" in result.output
