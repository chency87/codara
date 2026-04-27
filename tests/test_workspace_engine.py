from pathlib import Path
import subprocess

from amesh.workspace.engine import WorkspaceEngine


def test_workspace_engine_ignores_internal_runtime_artifacts(tmp_path):
    (tmp_path / "tracked.txt").write_text("keep me", encoding="utf-8")
    (tmp_path / ".uag_lock").write_text("lock", encoding="utf-8")
    internal_dir = tmp_path / ".uag" / "isolated_envs" / "codex" / "session-a"
    internal_dir.mkdir(parents=True, exist_ok=True)
    (internal_dir / "auth.json").write_text("{}", encoding="utf-8")

    engine = WorkspaceEngine(str(tmp_path))
    snapshot = engine.take_snapshot()

    assert "tracked.txt" in snapshot
    assert ".uag_lock" not in snapshot
    assert all(not path.startswith(".uag/") for path in snapshot)


def test_workspace_engine_initializes_git_head_and_diffs_untracked_files(tmp_path):
    engine = WorkspaceEngine(str(tmp_path))

    engine.ensure_git_repository()

    head = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert head.returncode == 0

    (tmp_path / "new.txt").write_text("hello\n", encoding="utf-8")

    modified_files, diff = engine.generate_diff()

    assert modified_files == ["new.txt"]
    assert diff is not None
    assert "diff --git a/new.txt b/new.txt" in diff
    assert "+++ b/new.txt" in diff
    assert "+hello" in diff
