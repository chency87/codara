from __future__ import annotations

import re
import os
import tomllib
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import jinja2

from codara.core.models import Workspace, ProviderType
from codara.database.manager import DatabaseManager
from codara.workspace.engine import WorkspaceEngine

if TYPE_CHECKING:
    from codara.workspace.manager import WorkspaceManager


WORKSPACE_METADATA_DIR = ".codara"
WORKSPACE_METADATA_FILE = "workspace.toml"
WORKSPACE_TEMPLATES = {"default", "python", "docs", "empty"}


@dataclass(frozen=True)
class WorkspaceCreateResult:
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


class WorkspaceService:
    def __init__(self, workspace_manager: "WorkspaceManager", db_manager: DatabaseManager) -> None:
        self.workspace_manager = workspace_manager
        self.db = db_manager
        self.workspaces_root = workspace_manager.workspaces_root

    def create_workspace(
        self,
        name: str,
        user_id: str,
        *,
        template: str = "default",
        default_provider: Optional[str] = None,
        force: bool = False,
    ) -> Workspace:
        slug = normalize_workspace_name(name)
        template = normalize_workspace_template(template)
        
        # Use user-specific subdirectory for isolation
        user_root = self.workspaces_root / user_id
        if slug == "default":
            path = user_root.resolve(strict=False)
        else:
            path = (user_root / slug).resolve(strict=False)
        
        # Ensure path exists
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._validate_workspace_path(path)

        # Apply template
        self._apply_template(path, slug, template)
        
        now = datetime.now(timezone.utc)
        workspace = Workspace(
            workspace_id=self.db._generate_ulid_like("wsk"),
            name=slug,
            path=str(path),
            user_id=user_id,
            template=template,
            default_provider=ProviderType(default_provider) if default_provider else None,
            created_at=now,
            updated_at=now,
        )
        self.db.save_workspace(workspace)
        
        metadata_path = workspace_metadata_path(path)
        self._write_metadata(
            metadata_path,
            name=slug,
            template=template,
            default_provider=default_provider,
            created_by=user_id,
        )
        WorkspaceEngine(str(path)).ensure_git_repository()

        return workspace

    def get_workspace_v2(self, workspace_id: str) -> Optional[Workspace]:
        return self.db.get_workspace_v2(workspace_id)

    def list_workspaces_v2(self, user_id: Optional[str] = None) -> list[Workspace]:
        return self.db.list_workspaces_v2(user_id=user_id)

    def _validate_workspace_path(self, path: Path) -> None:
        # Check if it's under the workspaces_root
        try:
            resolved_path = str(path.resolve())
            root_path = str(self.workspaces_root.resolve())
            common = os.path.commonpath([root_path, resolved_path])
            if common != root_path:
                raise ValueError("Workspace path is outside the managed workspace safe zone")
        except ValueError:
            raise ValueError("Workspace path is outside the managed workspace safe zone")

    def _apply_template(self, path: Path, name: str, template: str) -> None:
        if template == "empty":
            return

        template_base_dir = Path(__file__).parent / "templates"
        
        context = {
            "name": name,
            "description": f"Codara workspace for {name}.",
            "template": template,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        # 1. Render common files
        common_dir = template_base_dir / "common"
        if common_dir.exists():
            self._render_template_dir(common_dir, path, context)

        # 2. Render template-specific files
        specific_dir = template_base_dir / template
        if specific_dir.exists():
            self._render_template_dir(specific_dir, path, context)

    def _render_template_dir(self, template_dir: Path, target_dir: Path, context: dict[str, Any]) -> None:
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(template_dir)))
        
        for template_path in template_dir.rglob("*"):
            if template_path.is_dir():
                continue
            
            relative_path = template_path.relative_to(template_dir)
            
            # Handle .j2 extension
            if relative_path.suffix == ".j2":
                target_file_path = target_dir / relative_path.with_suffix("")
                template_str = template_path.read_text(encoding="utf-8")
                rendered = env.from_string(template_str).render(**context)
                
                target_file_path.parent.mkdir(parents=True, exist_ok=True)
                target_file_path.write_text(rendered, encoding="utf-8")
            else:
                target_file_path = target_dir / relative_path
                target_file_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(template_path, target_file_path)

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
        existing = load_workspace_metadata(metadata_path.parent.parent) or {}
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


def normalize_workspace_name(name: str) -> str:
    value = str(name or "").strip()
    if not value:
        raise ValueError("Workspace name is required")
    if value.startswith(".") or value in {".", ".."}:
        raise ValueError("Workspace name cannot be hidden or relative")
    if "/" in value or "\\" in value:
        raise ValueError("Workspace name must be a single path segment")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", value):
        raise ValueError("Workspace name may contain letters, numbers, dots, underscores, and hyphens")
    return value


def normalize_workspace_template(template: str) -> str:
    value = str(template or "default").strip().lower()
    if value not in WORKSPACE_TEMPLATES:
        raise ValueError(f"Unsupported workspace template: {template}")
    return value


def workspace_metadata_path(workspace_path: Path) -> Path:
    return workspace_path / WORKSPACE_METADATA_DIR / WORKSPACE_METADATA_FILE


def load_workspace_metadata(workspace_path: Path) -> Optional[dict[str, Any]]:
    path = workspace_metadata_path(workspace_path)
    if not path.exists():
        return None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return {
        "name": data.get("name") or workspace_path.name,
        "template": data.get("template") or data.get("layout", {}).get("template") or "default",
        "created_by": data.get("created_by"),
        "created_at": data.get("created_at"),
        "default_provider": data.get("default_provider"),
        "metadata_path": str(path),
    }


def _escape_toml(value: Optional[str]) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"')
