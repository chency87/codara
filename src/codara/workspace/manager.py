from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Iterable, Optional

from codara.database.manager import DatabaseManager
from codara.core.models import Session, User
from codara.workspace.engine import WorkspaceEngine


class WorkspaceManager:
    _INTERNAL_DIRS = {".git", ".uag"}

    def __init__(
        self,
        db_manager: DatabaseManager,
        *,
        workspaces_root: str,
        isolated_envs_root: Optional[str] = None,
    ) -> None:
        self.db_manager = db_manager
        self.workspaces_root = Path(workspaces_root).expanduser().resolve()
        self.isolated_envs_root = (
            Path(isolated_envs_root).expanduser().resolve() if isolated_envs_root else None
        )

    def list_workspaces(self) -> list[dict[str, Any]]:
        users = self.db_manager.list_users(limit=10_000, offset=0)
        sessions = self.db_manager.get_all_sessions(limit=10_000)
        candidates = self._discover_workspace_paths(users, sessions)
        records = [self._build_workspace_record(path, users) for path in candidates]
        return sorted(
            records,
            key=lambda item: (
                0 if item["scope"] == "base" else 1 if item["scope"] == "subworkspace" else 2,
                item["relative_path"] or item["path"],
            ),
        )

    def get_workspace(self, workspace_path: str) -> Optional[dict[str, Any]]:
        path = Path(workspace_path).expanduser().resolve(strict=False)
        if not self._is_managed_workspace(path):
            return None
        users = self.db_manager.list_users(limit=10_000, offset=0)
        record = self._build_workspace_record(path, users)
        if record["exists"] or record["owners"] or record["sessions"]:
            return record
        return None

    def reset_workspace_sessions(self, workspace_path: str) -> int:
        path = self._validate_actionable_path(workspace_path)
        return self.db_manager.delete_workspace_sessions(str(path))

    def delete_workspace(self, workspace_path: str) -> int:
        path = self._validate_actionable_path(workspace_path)
        wiped = self.db_manager.delete_workspace_sessions(str(path))
        if path.exists():
            shutil.rmtree(path)
        return wiped

    def _validate_actionable_path(self, workspace_path: str) -> Path:
        path = Path(workspace_path).expanduser().resolve(strict=False)
        if not self._is_managed_workspace(path) or path == self.workspaces_root:
            raise ValueError("Workspace path is not managed by Codara")
        return path

    def _discover_workspace_paths(self, users: list[User], sessions: list[Session]) -> list[Path]:
        candidates: set[Path] = set()
        if self.workspaces_root.exists():
            for root, dirs, _files in os.walk(self.workspaces_root):
                current = Path(root).resolve()
                dirs[:] = [
                    name
                    for name in dirs
                    if name not in self._INTERNAL_DIRS and not self._is_hidden_or_isolated(current / name)
                ]
                if current == self.workspaces_root:
                    continue
                if (current / ".git").exists():
                    candidates.add(current)

        for user in users:
            path = Path(user.workspace_path).expanduser().resolve(strict=False)
            if self._is_managed_workspace(path):
                candidates.add(path)

        for session in sessions:
            path = Path(session.cwd_path).expanduser().resolve(strict=False)
            if self._is_managed_workspace(path):
                candidates.add(path)

        return list(candidates)

    def _build_workspace_record(self, workspace_path: Path, users: list[User]) -> dict[str, Any]:
        resolved = workspace_path.expanduser().resolve(strict=False)
        owners = self._owners_for_path(resolved, users)
        sessions = self.db_manager.get_workspace_sessions(str(resolved))
        bound_users = self._bound_users(owners, sessions, users)
        scope = "orphan"
        if any(Path(user.workspace_path).expanduser().resolve(strict=False) == resolved for user in owners):
            scope = "base"
        elif owners:
            scope = "subworkspace"

        git = WorkspaceEngine(str(resolved)).get_git_metadata() if resolved.exists() else {
            "is_git_repo": False,
            "branch": None,
            "head_commit": None,
            "short_commit": None,
            "head_summary": None,
            "head_committed_at": None,
            "remote_url": None,
            "dirty": False,
        }

        try:
            relative_path = str(resolved.relative_to(self.workspaces_root))
        except ValueError:
            relative_path = None

        return {
            "path": str(resolved),
            "relative_path": relative_path,
            "name": resolved.name,
            "exists": resolved.exists(),
            "scope": scope,
            "owners": owners,
            "users": bound_users,
            "sessions": sessions,
            "git": git,
        }

    def _owners_for_path(self, workspace_path: Path, users: Iterable[User]) -> list[User]:
        owners: list[User] = []
        target = str(workspace_path)
        for user in users:
            base = Path(user.workspace_path).expanduser().resolve(strict=False)
            try:
                common = os.path.commonpath([str(base), target])
            except ValueError:
                continue
            if common == str(base):
                owners.append(user)
        return owners

    def _bound_users(self, owners: list[User], sessions: list[Session], all_users: list[User]) -> list[User]:
        user_map = {user.user_id: user for user in all_users}
        bound = {user.user_id: user for user in owners}
        for session in sessions:
            if session.user_id and session.user_id in user_map:
                bound[session.user_id] = user_map[session.user_id]
        return sorted(bound.values(), key=lambda user: (user.display_name.lower(), user.user_id))

    def _is_managed_workspace(self, path: Path) -> bool:
        try:
            common = os.path.commonpath([str(self.workspaces_root), str(path)])
        except ValueError:
            return False
        if common != str(self.workspaces_root):
            return False
        if self.isolated_envs_root is not None:
            try:
                isolated_common = os.path.commonpath([str(self.isolated_envs_root), str(path)])
            except ValueError:
                isolated_common = None
            if isolated_common == str(self.isolated_envs_root):
                return False
        return True

    def _is_hidden_or_isolated(self, path: Path) -> bool:
        if path.name.startswith("."):
            return True
        if self.isolated_envs_root is None:
            return False
        try:
            common = os.path.commonpath([str(self.isolated_envs_root), str(path.resolve(strict=False))])
        except ValueError:
            return False
        return common == str(self.isolated_envs_root)
