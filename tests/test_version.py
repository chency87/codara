import json

from codara.version import check_for_update, is_newer_version, normalize_github_repository


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_version_comparison_handles_v_tags_and_prereleases():
    assert is_newer_version("v0.2.0", "0.1.9") is True
    assert is_newer_version("v0.1.0", "0.1.0") is False
    assert is_newer_version("v1.0.0", "1.0.0-rc.1") is True


def test_normalize_github_repository_accepts_urls():
    assert normalize_github_repository("codara/codara") == "codara/codara"
    assert normalize_github_repository("https://github.com/codara/codara.git") == "codara/codara"
    assert normalize_github_repository("git@github.com:codara/codara.git") == "codara/codara"
    assert normalize_github_repository("invalid") is None


def test_check_for_update_reads_latest_github_release(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        return FakeResponse({"tag_name": "v0.2.0", "html_url": "https://github.com/codara/codara/releases/tag/v0.2.0"})

    monkeypatch.setattr("codara.version.request.urlopen", fake_urlopen)

    result = check_for_update(
        repository="https://github.com/codara/codara",
        current_version="0.1.0",
        timeout_seconds=2,
    )

    assert result.status == "ok"
    assert result.current_version == "0.1.0"
    assert result.latest_version == "0.2.0"
    assert result.update_available is True
    assert result.release_url == "https://github.com/codara/codara/releases/tag/v0.2.0"
    assert captured == {
        "url": "https://api.github.com/repos/codara/codara/releases/latest",
        "timeout": 2,
    }


def test_check_for_update_reports_unconfigured_repository():
    result = check_for_update(repository=None, current_version="0.1.0")

    assert result.status == "unconfigured"
    assert result.update_available is False
    assert result.error == "release repository is not configured"

