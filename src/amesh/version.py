from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, Optional
from urllib import error, request


PACKAGE_NAME = "amesh"
GITHUB_API_VERSION = "2022-11-28"


@dataclass(frozen=True)
class VersionCheckResult:
    current_version: str
    latest_version: Optional[str]
    update_available: bool
    status: str
    repository: Optional[str] = None
    release_url: Optional[str] = None
    checked_url: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_version": self.current_version,
            "latest_version": self.latest_version,
            "update_available": self.update_available,
            "status": self.status,
            "repository": self.repository,
            "release_url": self.release_url,
            "checked_url": self.checked_url,
            "error": self.error,
        }


def get_version() -> str:
    """Return the installed Codara package version with a source-tree fallback."""
    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        project_root = Path(__file__).resolve().parents[2]
        pyproject = project_root / "pyproject.toml"
        if not pyproject.exists():
            return "0.0.0"
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        return str(data.get("project", {}).get("version") or "0.0.0")


def normalize_version_tag(value: str) -> str:
    value = str(value or "").strip()
    if value.lower().startswith("refs/tags/"):
        value = value[10:]
    if value[:1].lower() == "v":
        value = value[1:]
    return value.strip()


def _version_key(value: str) -> tuple[tuple[int, ...], tuple[str, ...]]:
    normalized = normalize_version_tag(value)
    release_part = re.split(r"[-+]", normalized, maxsplit=1)[0]
    numbers = tuple(int(part) for part in re.findall(r"\d+", release_part))
    suffix = tuple(part for part in re.split(r"[.\-+_]", normalized[len(release_part):].strip(".-+_")) if part)
    return numbers, suffix


def is_newer_version(candidate: str, current: str) -> bool:
    candidate_numbers, candidate_suffix = _version_key(candidate)
    current_numbers, current_suffix = _version_key(current)
    width = max(len(candidate_numbers), len(current_numbers), 1)
    candidate_numbers = candidate_numbers + (0,) * (width - len(candidate_numbers))
    current_numbers = current_numbers + (0,) * (width - len(current_numbers))
    if candidate_numbers != current_numbers:
        return candidate_numbers > current_numbers
    # A stable release is newer than a local prerelease for the same numeric version.
    return bool(current_suffix) and not candidate_suffix


def normalize_github_repository(repository: Optional[str]) -> Optional[str]:
    if not repository:
        return None
    value = repository.strip()
    if not value:
        return None
    if value.startswith("git@github.com:"):
        value = value.removeprefix("git@github.com:")
    elif "github.com/" in value:
        value = value.split("github.com/", 1)[1]
    value = value.removesuffix(".git").strip("/")
    parts = [part for part in value.split("/") if part]
    if len(parts) < 2:
        return None
    return f"{parts[0]}/{parts[1]}"


def github_latest_release_url(repository: str, api_base_url: str = "https://api.github.com") -> str:
    repo = normalize_github_repository(repository)
    if repo is None:
        raise ValueError("release repository must be OWNER/REPO or a GitHub repository URL")
    return f"{api_base_url.rstrip('/')}/repos/{repo}/releases/latest"


def check_for_update(
    *,
    repository: Optional[str],
    current_version: Optional[str] = None,
    api_base_url: str = "https://api.github.com",
    timeout_seconds: int = 3,
) -> VersionCheckResult:
    current = current_version or get_version()
    repo = normalize_github_repository(repository)
    if repo is None:
        return VersionCheckResult(
            current_version=current,
            latest_version=None,
            update_available=False,
            status="unconfigured",
            error="release repository is not configured",
        )

    url = github_latest_release_url(repo, api_base_url)
    req = request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"amesh/{current}",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        },
    )
    try:
        with request.urlopen(req, timeout=max(1, int(timeout_seconds))) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = f"GitHub release check returned HTTP {exc.code}"
        return VersionCheckResult(current, None, False, "unavailable", repo, checked_url=url, error=detail)
    except Exception as exc:
        return VersionCheckResult(current, None, False, "unavailable", repo, checked_url=url, error=str(exc))

    tag = str(payload.get("tag_name") or payload.get("name") or "").strip()
    latest = normalize_version_tag(tag) if tag else None
    return VersionCheckResult(
        current_version=current,
        latest_version=latest,
        update_available=bool(latest and is_newer_version(latest, current)),
        status="ok",
        repository=repo,
        release_url=payload.get("html_url"),
        checked_url=url,
    )


__version__ = get_version()

