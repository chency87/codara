import pytest
from pathlib import Path
from amesh.workspace.manager import WorkspaceManager
from amesh.workspace.service import WorkspaceService
from amesh.database.manager import DatabaseManager

def test_apply_python_template(tmp_path):
    db_path = tmp_path / "test.db"
    db_manager = DatabaseManager(str(db_path))
    workspaces_root = tmp_path / "workspaces"
    workspaces_root.mkdir()
    
    manager = WorkspaceManager(db_manager, workspaces_root=str(workspaces_root))
    service = WorkspaceService(manager, db_manager)
    
    workspace_name = "test-python-app"
    user_id = "user-1"
    
    from amesh.core.models import User, UserStatus
    from datetime import datetime, timezone
    db_manager.save_user(User(
        user_id=user_id,
        email="test@example.com",
        display_name="Test User",
        status=UserStatus.ACTIVE,
        workspace_path=str(workspaces_root / user_id),
        created_at=datetime.now(timezone.utc),
        created_by="test",
        updated_at=datetime.now(timezone.utc)
    ))
    
    workspace = service.create_workspace(workspace_name, user_id, template="python")
    
    workspace_path = Path(workspace.path)
    
    # Check common files
    assert (workspace_path / "README.md").exists()
    assert "test-python-app" in (workspace_path / "README.md").read_text()
    assert (workspace_path / ".gitignore").exists()
    
    # Check python-specific files
    assert (workspace_path / "src" / "main.py").exists()
    assert "Hello from test-python-app!" in (workspace_path / "src" / "main.py").read_text()
    assert (workspace_path / "tests" / "test_main.py").exists()
    assert (workspace_path / "configs" / "config.yaml").exists()
    assert (workspace_path / "docs" / "index.md").exists()
    assert (workspace_path / "data" / ".gitkeep").exists()

def test_apply_empty_template(tmp_path):
    db_path = tmp_path / "test.db"
    db_manager = DatabaseManager(str(db_path))
    workspaces_root = tmp_path / "workspaces"
    workspaces_root.mkdir()
    
    manager = WorkspaceManager(db_manager, workspaces_root=str(workspaces_root))
    service = WorkspaceService(manager, db_manager)
    
    workspace_name = "test-empty"
    user_id = "user-2"
    
    from amesh.core.models import User, UserStatus
    from datetime import datetime, timezone
    db_manager.save_user(User(
        user_id=user_id,
        email="test2@example.com",
        display_name="Test User 2",
        status=UserStatus.ACTIVE,
        workspace_path=str(workspaces_root / user_id),
        created_at=datetime.now(timezone.utc),
        created_by="test",
        updated_at=datetime.now(timezone.utc)
    ))
    
    workspace = service.create_workspace(workspace_name, user_id, template="empty")
    
    workspace_path = Path(workspace.path)
    
    # Empty template should have nothing except .amesh/ metadata
    files = [f.name for f in workspace_path.iterdir() if f.is_file()]
    assert len(files) == 0
    assert (workspace_path / ".amesh").exists()
