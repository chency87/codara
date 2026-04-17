from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from codara.workspace.engine import WorkspaceEngine

if TYPE_CHECKING:
    from codara.workspace.manager import WorkspaceManager


PROJECT_METADATA_DIR = ".codara"
PROJECT_METADATA_FILE = "project.toml"
PROJECT_TEMPLATES = {"default", "python", "docs", "empty"}


@dataclass(frozen=True)
class ProjectCreateResult:
    name: str
    path: str
    relative_path: str
    template: str
    created: bool
    metadata_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "relative_path": self.relative_path,
            "template": self.template,
            "created": self.created,
            "metadata_path": self.metadata_path,
        }


class ProjectService:
    def __init__(self, workspace_manager: "WorkspaceManager") -> None:
        self.workspace_manager = workspace_manager
        self.workspaces_root = workspace_manager.workspaces_root

    def create_project(
        self,
        name: str,
        *,
        template: str = "default",
        default_provider: Optional[str] = None,
        force: bool = False,
        created_by: str = "cli",
    ) -> ProjectCreateResult:
        slug = normalize_project_name(name)
        template = normalize_project_template(template)
        path = (self.workspaces_root / slug).resolve(strict=False)
        self._validate_project_path(path)

        existed = path.exists()
        metadata_path = path / PROJECT_METADATA_DIR / PROJECT_METADATA_FILE
        if existed and not force and not metadata_path.exists():
            raise FileExistsError(
                f"Project path already exists and is not initialized by Codara: {path}. Use --force to initialize it."
            )

        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._apply_template(path, slug, template)
        self._write_metadata(
            metadata_path,
            name=slug,
            template=template,
            default_provider=default_provider,
            created_by=created_by,
        )
        WorkspaceEngine(str(path)).ensure_git_repository()

        return ProjectCreateResult(
            name=slug,
            path=str(path),
            relative_path=str(path.relative_to(self.workspaces_root)),
            template=template,
            created=not existed,
            metadata_path=str(metadata_path),
        )

    def get_project(self, name_or_path: str) -> Optional[dict[str, Any]]:
        path = self._resolve_project_reference(name_or_path)
        record = self.workspace_manager.get_workspace(str(path))
        if not record:
            return None
        project = load_project_metadata(path)
        if not project:
            return None
        record["project"] = project
        return record

    def list_projects(self) -> list[dict[str, Any]]:
        projects: list[dict[str, Any]] = []
        for record in self.workspace_manager.list_workspaces():
            metadata = load_project_metadata(Path(record["path"]))
            if not metadata:
                continue
            record["project"] = metadata
            projects.append(record)
        return projects

    def _resolve_project_reference(self, name_or_path: str) -> Path:
        raw = str(name_or_path or "").strip()
        if not raw:
            raise ValueError("Project name is required")
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute() and len(candidate.parts) == 1:
            candidate = self.workspaces_root / normalize_project_name(raw)
        return candidate.resolve(strict=False)

    def _validate_project_path(self, path: Path) -> None:
        if not self.workspace_manager._is_managed_workspace(path) or path == self.workspaces_root:
            raise ValueError("Project path is outside the managed workspace safe zone")

    def _apply_template(self, path: Path, name: str, template: str) -> None:
        if template == "empty":
            return

        (path / "README.md").touch(exist_ok=True)
        readme = path / "README.md"
        if not readme.read_text(encoding="utf-8").strip():
            readme.write_text(f"# {name}\n\nCodara project workspace.\n", encoding="utf-8")

        folders = {
            "default": ["docs", "src", "scripts", "tests"],
            "python": ["docs", "src", "scripts", "tests"],
            "docs": ["docs", "notes", "assets"],
        }[template]
        for folder in folders:
            (path / folder).mkdir(parents=True, exist_ok=True)

        if template == "python":
            package_name = name.replace("-", "_")
            package_root = path / "src" / package_name
            package_root.mkdir(parents=True, exist_ok=True)
            (package_root / "__init__.py").touch(exist_ok=True)
            (path / "tests" / f"test_{package_name}.py").touch(exist_ok=True)
        elif template == "docs":
            index = path / "docs" / "index.md"
            if not index.exists():
                index.write_text(f"# {name} Docs\n", encoding="utf-8")

    def _write_metadata(
        self,
        metadata_path: Path,
        *,
        name: str,
        template: str,
        default_provider: Optional[str],
        created_by: str,
    ) -> None:
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        existing = load_project_metadata(metadata_path.parent.parent) or {}
        created_at = existing.get("created_at") or datetime.now(timezone.utc).isoformat()
        provider_line = f'default_provider = "{_escape_toml(default_provider)}"\n' if default_provider else ""
        metadata_path.write_text(
            "".join(
                [
                    f'name = "{_escape_toml(name)}"\n',
                    f'template = "{_escape_toml(template)}"\n',
                    f'created_by = "{_escape_toml(created_by)}"\n',
                    f'created_at = "{_escape_toml(created_at)}"\n',
                    provider_line,
                    "\n[layout]\n",
                    f'template = "{_escape_toml(template)}"\n',
                ]
            ),
            encoding="utf-8",
        )


def normalize_project_name(name: str) -> str:
    value = str(name or "").strip()
    if not value:
        raise ValueError("Project name is required")
    if value.startswith(".") or value in {".", ".."}:
        raise ValueError("Project name cannot be hidden or relative")
    if "/" in value or "\\" in value:
        raise ValueError("Project name must be a single path segment")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", value):
        raise ValueError("Project name may contain letters, numbers, dots, underscores, and hyphens")
    return value


def normalize_project_template(template: str) -> str:
    value = str(template or "default").strip().lower()
    if value not in PROJECT_TEMPLATES:
        raise ValueError(f"Unsupported project template: {template}")
    return value


def project_metadata_path(project_path: Path) -> Path:
    return project_path / PROJECT_METADATA_DIR / PROJECT_METADATA_FILE


def load_project_metadata(project_path: Path) -> Optional[dict[str, Any]]:
    path = project_metadata_path(project_path)
    if not path.exists():
        return None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return {
        "name": data.get("name") or project_path.name,
        "template": data.get("template") or data.get("layout", {}).get("template") or "default",
        "created_by": data.get("created_by"),
        "created_at": data.get("created_at"),
        "default_provider": data.get("default_provider"),
        "metadata_path": str(path),
    }


def _escape_toml(value: Optional[str]) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"')
