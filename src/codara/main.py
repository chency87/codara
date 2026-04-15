import uvicorn
import os
from pathlib import Path
from codara.gateway.app import app, db_manager
from codara.core.models import Account, ProviderType, AuthType
from codara.accounts.pool import AccountPool
from codara.config import get_settings
from codara.logging_setup import configure_logging

settings = get_settings()

def _run_uvicorn():
    try:
        uvicorn.run(app, host=settings.host, port=settings.port, log_config=None)
    except TypeError:
        uvicorn.run(app, host=settings.host, port=settings.port)

def auto_import_provider_auth():
    """Automatically import provider credentials if they exist."""
    pool = AccountPool(db_manager)
    
    providers = {
        "codex": Path(os.path.expanduser("~/.codex/auth.json")),
        "gemini": Path(os.path.expanduser("~/.gemini/oauth_creds.json")),
    }

    for name, path in providers.items():
        if path.exists():
            try:
                content = path.read_text()
                # Check if already imported
                existing = db_manager.get_account(f"{name}-oauth")
                if not existing:
                    account = Account(
                        account_id=f"{name}-oauth",
                        provider=ProviderType(name),
                        auth_type=AuthType.OAUTH_SESSION,
                        label=f"{name.capitalize()} OAuth Account",
                        remaining_compute_hours=5.0, # Default for imported
                        weekly_limit=1000000
                    )
                    pool.register_account(account, content)
                    print(f"Successfully auto-imported {name} auth.")
            except Exception as e:
                print(f"Warning: Failed to auto-import {name} auth: {e}")

if __name__ == "__main__":
    # auto_import_provider_auth()
    configure_logging(settings)
    _run_uvicorn()
