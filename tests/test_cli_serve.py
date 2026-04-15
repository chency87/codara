from click.testing import CliRunner
import os

import codara.cli.main as cli_main


def test_serve_skips_ui_build_by_default_when_assets_are_missing(tmp_path, monkeypatch):
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ui").mkdir()
    (tmp_path / "ui" / "package.json").write_text("{}", encoding="utf-8")

    def fail_subprocess_run(*args, **kwargs):
        raise AssertionError("subprocess.run should not be called without --build-ui")

    observed = {}

    monkeypatch.setattr("subprocess.run", fail_subprocess_run)
    monkeypatch.setattr(cli_main.uvicorn, "run", lambda app, host, port: observed.update({"host": host, "port": port}))

    result = runner.invoke(cli_main.cli, ["serve", "--host", "127.0.0.1", "--port", "8123"])

    assert result.exit_code == 0
    assert "Dashboard build not found." in result.output
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


def test_account_add_restricts_provider_to_codex():
    runner = CliRunner()

    result = runner.invoke(
        cli_main.cli,
        [
            "account",
            "add",
            "--id",
            "gemini-main",
            "--provider",
            "gemini",
            "--auth-type",
            "OAUTH_SESSION",
            "--label",
            "Gemini Main",
            "--credential-file",
            __file__,
        ],
    )

    assert result.exit_code != 0
    assert "Invalid value for '--provider': 'gemini' is not 'codex'" in result.output


def test_account_group_no_longer_exposes_non_codex_import_commands():
    runner = CliRunner()

    result = runner.invoke(cli_main.cli, ["account", "--help"])

    assert result.exit_code == 0
    assert "import-gemini" not in result.output
    assert "import-opencode" not in result.output
