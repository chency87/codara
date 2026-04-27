import uvicorn
from amesh.gateway.app import app
from amesh.config import get_settings
from amesh.logging_setup import configure_logging

settings = get_settings()

def _run_uvicorn():
    uvicorn.run(app, host=settings.host, port=settings.port)

if __name__ == "__main__":
    configure_logging(settings)
    _run_uvicorn()
