import click
import uvicorn
from datetime import datetime
from pathlib import Path

from codara.gateway.app import app, db_manager
from codara.core.models import Account, ProviderType, AuthType, SessionStatus
from codara.config import get_settings
from codara.logging_setup import configure_logging

settings = get_settings()


def _run_uvicorn(app, host: str, port: int) -> None:
    try:
        uvicorn.run(app, host=host, port=port, log_config=None)
    except TypeError:
        uvicorn.run(app, host=host, port=port)


def _dashboard_build_is_stale(project_root: Path) -> bool:
    ui_root = project_root / "ui"
    dist_index = ui_root / "dist" / "index.html"
    src_root = ui_root / "src"
    if not dist_index.exists() or not src_root.exists():
        return False

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

@click.group()
def cli():
    """Unified Agent Gateway (UAG) - Central CLI Layer"""
    pass

@cli.command()
@click.option('--host', default=None, help='Host to bind the gateway to.')
@click.option('--port', default=None, type=int, help='Port to bind the gateway to.')
@click.option('--build-ui/--no-build-ui', default=False, help='Build ui/dist before starting the gateway.')
def serve(host, port, build_ui):
    """Start the UAG API Gateway server and the Dashboard."""
    import os
    import subprocess

    project_root = Path(os.getcwd())
    ui_root = project_root / "ui"
    ui_dist = ui_root / "dist"
    if build_ui and (ui_root / "package.json").exists():
        click.echo("Building dashboard assets before startup...")
        try:
            subprocess.run(["npm", "run", "build"], cwd="ui", check=True)
            click.echo("Dashboard built successfully.")
        except Exception as e:
            click.echo(f"Warning: Failed to build dashboard. {e}")
    elif not ui_dist.exists() and (ui_root / "package.json").exists():
        click.echo("Dashboard build not found. Run `cd ui && npm run build` or start with `--build-ui` if you need /dashboard.")
    elif _dashboard_build_is_stale(project_root):
        click.echo("Dashboard build appears stale. Run `cd ui && npm run build` or start with `--build-ui` so /dashboard uses the current UI source.")

    host = host or settings.host
    port = port or settings.port
    log_path = configure_logging(settings)
    click.echo(f"Starting Unified Agent Gateway on {host}:{port}...")
    click.echo(f"Dashboard available at http://{host}:{port}/dashboard")
    click.echo(f"Centralized logs: {log_path}")
    _run_uvicorn(app, host=host, port=port)

@cli.group()
def account():
    """Manage the Account Pool."""
    pass

@account.command(name='add')
@click.option('--id', 'account_id', prompt=True, help='Unique ID for the account.')
@click.option('--provider', type=click.Choice(['codex']), prompt=True, help='Target provider.')
@click.option('--auth-type', type=click.Choice(['API_KEY', 'OAUTH_SESSION']), default='API_KEY', help='Authentication type.')
@click.option('--label', prompt=True, help='Human-readable label for the account.')
@click.option('--credential-file', type=click.Path(exists=True, dir_okay=False, readable=True), required=True, help='Path to credential payload (API key text or auth JSON).')
def add_account(account_id, provider, auth_type, label, credential_file):
    """Add a new registered account to the pool."""
    from pathlib import Path
    from codara.accounts.pool import AccountPool

    raw_credential = Path(credential_file).read_text(encoding="utf-8")
    if not raw_credential.strip():
        raise click.ClickException("Credential file is empty.")

    account = Account(
        account_id=account_id,
        provider=ProviderType(provider),
        auth_type=AuthType(auth_type),
        label=label
    )
    AccountPool(db_manager).register_account(account, raw_credential)
    db_manager.record_audit(
        actor="cli",
        action="account.registered",
        target_type="account",
        target_id=account_id,
        after=account.dict()
    )
    click.echo(f"Successfully added registered account: {account_id} ({label})")

@account.command(name='list')
def list_accounts():
    """List registered accounts in the pool."""
    with db_manager._get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM accounts
            WHERE encrypted_credential IS NOT NULL
            ORDER BY account_id ASC
            """
        ).fetchall()
        if not rows:
            click.echo("No registered accounts found in the pool.")
            return
        
        click.echo(f"{'ID':<20} {'Provider':<10} {'Status':<10} {'Label'}")
        click.echo("-" * 60)
        for row in rows:
            click.echo(f"{row['account_id']:<20} {row['provider']:<10} {row['status']:<10} {row['label']}")

@cli.group()
def session():
    """Manage Session Registry."""
    pass

@session.command(name='list')
def list_sessions():
    """List all active/idle sessions."""
    with db_manager._get_connection() as conn:
        rows = conn.execute("SELECT * FROM sessions").fetchall()
        if not rows:
            click.echo("No active sessions found.")
            return
        
        click.echo(f"{'Session ID':<36} {'Provider':<10} {'Status':<10} {'Updated At'}")
        click.echo("-" * 75)
        for row in rows:
            updated_at = datetime.fromtimestamp(row['updated_at']).strftime('%Y-%m-%d %H:%M:%S')
            click.echo(f"{row['client_session_id']:<36} {row['provider']:<10} {row['status']:<10} {updated_at}")

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
