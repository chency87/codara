from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Optional

from amesh.database.manager import DatabaseManager
from amesh.core.models import Workspace


class WorkspaceManager:
    def __init__(
        self,
        db_manager: DatabaseManager,
        *,
        workspaces_root: str,
    ) -> None:
        self.db_manager = db_manager
        self.workspaces_root = Path(workspaces_root).expanduser().resolve()

    def list_workspaces(self, user_id: Optional[str] = None) -> list[dict[str, Any]]:
        workspaces = self.db_manager.list_workspaces_v2(user_id=user_id)
        return [self._enrich_workspace(w) for w in workspaces]

    def get_workspace(self, workspace_id: str) -> Optional[dict[str, Any]]:
        # Handle both ID and path for backward compatibility in some tests
        workspace = self.db_manager.get_workspace_v2(workspace_id)
        if not workspace:
            # Try lookup by path
            all_w = self.db_manager.list_workspaces_v2()
            for w in all_w:
                if w.path == workspace_id:
                    workspace = w
                    break
        
        if not workspace:
            return None
        return self._enrich_workspace(workspace)

    def _enrich_workspace(self, workspace: Workspace) -> dict[str, Any]:
        from amesh.workspace.engine import WorkspaceEngine
        from amesh.workspace.service import load_workspace_metadata
        
        path = Path(workspace.path)
        engine = WorkspaceEngine(workspace.path)
        
        sessions = self.db_manager.get_all_sessions(workspace_id=workspace.workspace_id)
        
        # In this consolidated model, the owner is the workspace user
        owner = self.db_manager.get_user(workspace.user_id)
        users = [owner] if owner else []
        owners = [owner] if owner else []

        return {
            "workspace_id": workspace.workspace_id,
            "name": workspace.name,
            "path": workspace.path,
            "relative_path": str(path.relative_to(self.workspaces_root)) if path.is_relative_to(self.workspaces_root) else workspace.name,
            "exists": path.exists(),
            "scope": "user" if workspace.user_id else "system",
            "project": load_workspace_metadata(path),
            "git": {
                "is_git_repo": engine.is_git_repo(),
            },
            "sessions": sessions,
            "users": users,
            "owners": owners,
        }

    def delete_workspace(self, workspace_id: str) -> bool:
        workspace = self.get_workspace(workspace_id)
        if not workspace:
            return False
        
        path = Path(workspace["path"])
        if path.exists() and self._is_managed_workspace(path):
            shutil.rmtree(path)
        
        self.db_manager.delete_workspace_v2(workspace["workspace_id"])
        return True

    def reset_workspace_sessions(self, workspace_id: str) -> int:
        return self.db_manager.delete_workspace_sessions(workspace_id)

    def validate_inference_workspace(self, workspace_root: str) -> Path:
        path = Path(workspace_root).expanduser().resolve()
        if not self._is_managed_workspace(path) or path == self.workspaces_root:
            raise ValueError("Workspace path is outside the managed workspace safe zone")
        return path

    def _is_managed_workspace(self, path: Path) -> bool:
        try:
            common = os.path.commonpath([str(self.workspaces_root), str(path.resolve())])
        except ValueError:
            return False
        return common == str(self.workspaces_root)
