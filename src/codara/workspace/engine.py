import os
import subprocess
import hashlib
import difflib
from typing import List, Dict, Optional, Tuple, Any
from pathlib import Path

class WorkspaceEngine:
    _INTERNAL_PREFIXES = (".git", ".uag")
    _INTERNAL_FILES = {".uag_lock"}
    _GIT_COMMIT_MESSAGE = "Initialize workspace"

    def __init__(self, workspace_root: str):
        self.workspace_root = Path(workspace_root).resolve()
        if not self.workspace_root.exists():
            raise ValueError(f"Workspace root {workspace_root} does not exist.")
        self._snapshot: Dict[str, str] = {}
        self._is_git_repo: Optional[bool] = None

    def is_git_repo(self) -> bool:
        if self._is_git_repo is None:
            self._is_git_repo = (self.workspace_root / ".git").exists()
        return self._is_git_repo

    def ensure_git_repository(self) -> None:
        if not self.is_git_repo():
            subprocess.run(
                ["git", "init", "-q"],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                check=True,
            )
            self._is_git_repo = True
        if not self._has_git_head():
            subprocess.run(
                ["git", "add", "-A"],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                check=True,
            )
            env = os.environ.copy()
            env.setdefault("GIT_AUTHOR_NAME", "Codara")
            env.setdefault("GIT_AUTHOR_EMAIL", "codara@local")
            env.setdefault("GIT_COMMITTER_NAME", env["GIT_AUTHOR_NAME"])
            env.setdefault("GIT_COMMITTER_EMAIL", env["GIT_AUTHOR_EMAIL"])
            subprocess.run(
                ["git", "commit", "--allow-empty", "-qm", self._GIT_COMMIT_MESSAGE],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )

    def _has_git_head(self) -> bool:
        if not self.is_git_repo():
            return False
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=self.workspace_root,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0

    def _iter_workspace_files(self) -> List[Path]:
        files: List[Path] = []
        for root, dirs, filenames in os.walk(self.workspace_root):
            dirs[:] = [d for d in dirs if d not in self._INTERNAL_PREFIXES]
            for filename in filenames:
                if filename in self._INTERNAL_FILES:
                    continue
                files.append(Path(root) / filename)
        return files

    def _is_internal_path(self, relative_path: str) -> bool:
        normalized = relative_path.strip()
        if normalized in self._INTERNAL_FILES:
            return True
        return any(normalized == prefix or normalized.startswith(f"{prefix}/") for prefix in self._INTERNAL_PREFIXES)

    def _git_tracked_and_untracked_files(self) -> Optional[List[str]]:
        try:
            result = subprocess.run(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError:
            return None
        return sorted(
            line
            for line in result.stdout.splitlines()
            if line.strip() and not self._is_internal_path(line.strip())
        )

    def take_snapshot(self) -> Dict[str, str]:
        """Capture hashes of all files in the workspace."""
        snapshot = {}
        for file_path in self._iter_workspace_files():
            relative_path = str(file_path.relative_to(self.workspace_root))
            try:
                with open(file_path, "rb") as f:
                    snapshot[relative_path] = hashlib.sha256(f.read()).hexdigest()
            except (IOError, OSError):
                continue
        self._snapshot = snapshot
        return snapshot

    def generate_diff(self) -> Tuple[List[str], Optional[str]]:
        """Generate diff and list of modified files since last snapshot."""
        if self.is_git_repo():
            return self._generate_git_diff()
        else:
            return self._generate_hash_diff()

    def _generate_git_diff(self) -> Tuple[List[str], Optional[str]]:
        try:
            # Get modified files
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                check=True
            )
            modified_files = []
            untracked_files: list[str] = []
            for line in result.stdout.splitlines():
                if line.strip():
                    # git status --porcelain output is "XY path"
                    path = line[3:]
                    if not self._is_internal_path(path):
                        modified_files.append(path)
                        if line.startswith("?? "):
                            untracked_files.append(path)

            # Get the actual diff
            diff_result = subprocess.run(
                ["git", "diff", "--no-color", "HEAD"],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                check=True
            )
            diff_chunks: list[str] = []
            if diff_result.stdout:
                diff_chunks.append(diff_result.stdout)
            for path in untracked_files:
                patch = self._build_untracked_file_diff(path)
                if patch:
                    diff_chunks.append(patch)
            combined_diff = "\n".join(chunk.rstrip("\n") for chunk in diff_chunks if chunk).strip()
            return modified_files, combined_diff or None
        except subprocess.CalledProcessError:
            return [], None

    def _build_untracked_file_diff(self, relative_path: str) -> Optional[str]:
        file_path = self.workspace_root / relative_path
        try:
            content = file_path.read_text(encoding="utf-8").splitlines(keepends=True)
        except (OSError, UnicodeDecodeError):
            return None
        diff_lines = difflib.unified_diff(
            [],
            content,
            fromfile=f"a/{relative_path}",
            tofile=f"b/{relative_path}",
            lineterm="",
        )
        diff_body = "\n".join(diff_lines).strip()
        if not diff_body:
            return None
        return f"diff --git a/{relative_path} b/{relative_path}\n{diff_body}\n"

    def _generate_hash_diff(self) -> Tuple[List[str], Optional[str]]:
        new_snapshot = self.take_snapshot()
        modified_files = []
        for path, hash_val in new_snapshot.items():
            if path not in self._snapshot or self._snapshot[path] != hash_val:
                modified_files.append(path)
        
        for path in self._snapshot:
            if path not in new_snapshot:
                modified_files.append(path)
        
        # Simple hash diff doesn't provide a unified diff string easily
        # For now, we just return the modified files.
        return modified_files, None

    def acquire_lock(self, timeout: int = 300) -> bool:
        """Acquire a workspace lock. (Simplified for dev)"""
        # In a real implementation, this would use SQLite or Redis as per SRDS
        # For now, we'll use a lock file.
        lock_file = self.workspace_root / ".uag_lock"
        if lock_file.exists():
            # Check for stale lock (simplified)
            import time
            if time.time() - lock_file.stat().st_mtime > timeout:
                lock_file.unlink()
            else:
                return False
        
        lock_file.touch()
        return True

    def release_lock(self):
        lock_file = self.workspace_root / ".uag_lock"
        if lock_file.exists():
            lock_file.unlink()

    def get_file_tree_metadata(self) -> str:
        """Returns a normalized string representing the file tree for prefix hashing."""
        if self.is_git_repo():
            git_files = self._git_tracked_and_untracked_files()
            if git_files is not None:
                return "\n".join(git_files)
        return "\n".join(
            sorted(str(path.relative_to(self.workspace_root)) for path in self._iter_workspace_files())
        )

    def get_git_metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "is_git_repo": self.is_git_repo(),
            "branch": None,
            "head_commit": None,
            "short_commit": None,
            "head_summary": None,
            "head_committed_at": None,
            "remote_url": None,
            "dirty": False,
        }
        if not metadata["is_git_repo"]:
            return metadata

        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.workspace_root,
            capture_output=True,
            text=True,
            check=False,
        )
        metadata["dirty"] = bool(status_result.stdout.strip())

        branch_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=self.workspace_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if branch_result.returncode == 0:
            metadata["branch"] = branch_result.stdout.strip() or None

        if not self._has_git_head():
            return metadata

        head_result = subprocess.run(
            ["git", "show", "-s", "--format=%H%n%h%n%s%n%cI", "HEAD"],
            cwd=self.workspace_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if head_result.returncode == 0:
            lines = head_result.stdout.splitlines()
            if len(lines) >= 4:
                metadata["head_commit"] = lines[0].strip() or None
                metadata["short_commit"] = lines[1].strip() or None
                metadata["head_summary"] = lines[2].strip() or None
                metadata["head_committed_at"] = lines[3].strip() or None

        remote_result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=self.workspace_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if remote_result.returncode == 0:
            metadata["remote_url"] = remote_result.stdout.strip() or None

        return metadata
