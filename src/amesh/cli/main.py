import click
import uvicorn
from datetime import datetime
from pathlib import Path

from amesh.gateway.app import app, db_manager
from amesh.core.models import SessionStatus, ProviderType, Workspace, Session
from amesh.config import get_settings
from amesh.logging_setup import configure_logging
from amesh.version import check_for_update, get_version
from amesh.workspace.manager import WorkspaceManager
from amesh.workspace.service import WORKSPACE_TEMPLATES, WorkspaceService

settings = get_settings()


def _run_uvicorn(app, host: str, port: int) -> None:
    uvicorn.run(app, host=host, port=port)


def _run_uvicorn_reload(app, host: str, port: int, project_root: Path) -> None:
    uvicorn.run(
        app,
        host=host,
        port=port,
        reload=True,
        reload_dirs=[str(project_root / "src" / "amesh")],
    )


def _dashboard_build_is_stale(project_root: Path) -> bool:
    ui_root = project_root / "ui"
    dist_index = ui_root / "dist" / "index.html"
    src_root = ui_root / "src"
    if not dist_index.exists() or not src_root.exists():
        return True

    built_at = dist_index.stat().st_mtime
    watch_paths = [
        ui_root / "package.json",
        ui_root / "vite.config.ts",
        ui_root / "tsconfig.json",
        ui_root / "tsconfig.app.json",
        ui_root / "index.html",
    ]
    watch_paths.extend(path for path in src_root.rglob("*") if path.is_file())
    return any(path.exists() and path.stat().st_mtime > built_at for path in watch_paths)


def _workspace_service() -> WorkspaceService:
    return WorkspaceService(
        WorkspaceManager(
            db_manager,
            workspaces_root=settings.workspaces_root,
        ),
        db_manager
    )


@click.group()
def cli():
    """Unified Agent Gateway (UAG) - Central CLI Layer"""
    pass


@cli.command(name="version")
@click.option("--check/--no-check", default=False, help="Check the configured GitHub release for updates.")
def version_command(check):
    """Show the Ameshe framework version."""
    current = get_version()
    click.echo(f"Ameshe {current}")
    if not check:
        return
    if not settings.release_check_enabled:
        click.echo("Update check disabled. Set [release].enabled = true to enable it.")
        return
    result = check_for_update(
        repository=settings.release_repository,
        current_version=current,
        api_base_url=settings.release_api_base_url,
        timeout_seconds=settings.release_check_timeout_seconds,
    )
    if result.status != "ok":
        click.echo(f"Update check unavailable: {result.error or result.status}")
        return
    if result.update_available:
        click.echo(f"Update available: {result.latest_version}")
        if result.release_url:
            click.echo(f"Release: {result.release_url}")
        return
    click.echo("Codara is up to date.")


@cli.command(name="serve")
@click.option('--host', default=None, help='Host to bind the gateway to.')
@click.option('--port', default=None, type=int, help='Port to bind the gateway to.')
@click.option('--build-ui/--no-build-ui', default=False, help='Build ui/dist before starting.')
@click.option('--dev', 'dev_mode', is_flag=True, help='Start with hot-reload for backend code.')
def serve(host, port, build_ui, dev_mode):
    """Start the Ameshe Gateway server (serves API + Dashboard UI).

    The Dashboard UI is served from ui/dist at /dashboard.
    """
    import os
    import subprocess

    project_root = Path(os.getcwd())
    ui_root = project_root / "ui"
    ui_dist = ui_root / "dist"

    # Build UI if requested or if dist doesn't exist
    if build_ui or not ui_dist.exists():
        if (ui_root / "package.json").exists():
            click.echo("Building dashboard...")
            try:
                subprocess.run(["npm", "run", "build"], cwd=ui_root, check=True)
                click.echo("Dashboard built.")
            except Exception as e:
                click.echo(f"Warning: UI build failed. {e}")
        else:
            click.echo("Warning: ui/ not found. Dashboard won't be available.")

    # Warn if UI might be stale
    if ui_dist.exists() and _dashboard_build_is_stale(project_root):
        click.echo("Note: UI build may be stale. Run with --build-ui to rebuild.")

    host = host or settings.host
    port = port or settings.port
    log_path = configure_logging(settings)

    click.echo(f"Ameshe Gateway: http://{host}:{port}")
    click.echo(f"Dashboard:    http://{host}:{port}/dashboard")
    click.echo(f"Logs:       {log_path}")

    if dev_mode:
        click.echo("Running in dev mode (backend reload enabled)...")
        _run_uvicorn_reload(app, host=host, port=port, project_root=project_root)
    else:
        _run_uvicorn(app, host=host, port=port)


@cli.command(name="dev")
@click.option('--host', default='127.0.0.1', help='Host to bind to.')
@click.option('--port', default=8000, type=int, help='Port to bind to.')
def dev(host, port):
    """Start Ameshe in development mode with hot-reload.

    Runs both backend (with reload) and frontend dev servers concurrently.
    """
    import os
    import subprocess

    project_root = Path(os.getcwd())
    ui_root = project_root / "ui"

    if not (ui_root / "package.json").exists():
        click.echo("Error: ui/ directory not found.")
        return

    # Start frontend dev server
    click.echo("Starting frontend dev server...")
    frontend_proc = subprocess.Popen(
        ["npm", "run", "dev"],
        cwd=ui_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Start backend with reload
    click.echo("Starting backend with reload...")
    backend_proc = subprocess.Popen(
        [
            "uvicorn", "amesh.gateway.app:app",
            "--host", host,
            "--port", str(port),
            "--reload",
            "--reload-dir", str(project_root / "src" / "amesh"),
        ],
        cwd=project_root,
    )

    log_path = configure_logging(settings)
    click.echo(f"\nDev servers running:")
    click.echo(f"  Frontend: http://{host}:5173/dashboard")
    click.echo(f"  Backend:  http://{host}:{port}")
    click.echo(f"  Logs:     {log_path}")
    click.echo("\nPress Ctrl+C to stop.")

    try:
        frontend_proc.wait()
    except KeyboardInterrupt:
        click.echo("\nStopping...")
        frontend_proc.terminate()
        backend_proc.terminate()


@cli.group()
def workspace():
    """Manage user-facing Ameshe workspaces."""
    pass


@workspace.command(name="create")
@click.argument("name")
@click.option("--template", "template_name", type=click.Choice(sorted(WORKSPACE_TEMPLATES)), default="default", show_default=True)
@click.option("--provider", type=click.Choice(["codex", "gemini", "opencode"]), default=None, help="Optional default provider metadata.")
@click.option("--force/--no-force", default=False, help="Initialize an existing folder if needed.")
@click.option("--user-id", required=True, help="Owner user ID.")
def create_workspace(name, template_name, provider, force, user_id):
    """Create a managed workspace with a predefined layout."""
    try:
        result = _workspace_service().create_workspace(
            name,
            user_id,
            template=template_name,
            default_provider=provider,
            force=force,
        )
    except (ValueError, FileExistsError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Successfully created workspace: {result.name}")
    click.echo(f"ID:   {result.workspace_id}")
    click.echo(f"Path: {result.path}")


@workspace.command(name="list")
@click.option("--user-id", help="Filter by owner user ID.")
def list_workspaces(user_id):
    """List managed Codara workspaces."""
    records = _workspace_service().list_workspaces_v2(user_id=user_id)
    if not records:
        click.echo("No managed workspaces found.")
        return

    click.echo(f"{'ID':<24} {'Name':<20} {'User ID':<20} {'Template'}")
    click.echo("-" * 75)
    for record in records:
        click.echo(f"{record.workspace_id:<24} {record.name:<20} {record.user_id:<20} {record.template}")


@workspace.command(name="info")
@click.argument("workspace_id")
def workspace_info(workspace_id):
    """Show one Codara workspace's details."""
    record = _workspace_service().get_workspace_v2(workspace_id)
    if not record:
        raise click.ClickException(f"Workspace not found: {workspace_id}")
    click.echo(f"ID:      {record.workspace_id}")
    click.echo(f"Name:    {record.name}")
    click.echo(f"Path:    {record.path}")
    click.echo(f"User ID: {record.user_id}")
    click.echo(f"Template: {record.template}")
    click.echo(f"Default provider: {record.default_provider or 'n/a'}")


@cli.group()
def session():
    """Manage Session Registry."""
    pass


@session.command(name='list')
def list_sessions():
    """List all active/idle sessions."""
    rows = db_manager.get_all_sessions()
    if not rows:
        click.echo("No active sessions found.")
        return
    
    click.echo(f"{'Session ID':<36} {'Provider':<10} {'Status':<10} {'Updated At'}")
    click.echo("-" * 75)
    for row in rows:
        click.echo(f"{row.session_id:<36} {row.provider:<10} {row.status:<10} {row.updated_at}")


@session.command(name='reset')
@click.argument('session_id')
def reset_session(session_id):
    """Force reset a session's status to IDLE (clears DIRTY flag)."""
    session = db_manager.get_session(session_id)
    if not session:
        click.echo(f"Error: Session {session_id} not found.")
        return
    
    session.status = SessionStatus.IDLE
    db_manager.save_session(session)
    click.echo(f"Successfully reset session {session_id} to IDLE.")


if __name__ == "__main__":
    cli()