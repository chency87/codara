import shutil
import time
from typing import Any, Optional, Protocol

from amesh.core.models import Message, Session, TurnResult

_MODEL_LISTING_CACHE_TTL_SECONDS = 60

class ProviderAdapter(Protocol):
    async def send_turn(self, session: Session, messages: list[Message], provider_model: str) -> TurnResult:
        ...

    async def resume_session(self, backend_id: str) -> Session:
        ...

    async def terminate_session(self, backend_id: str) -> None:
        ...

    async def list_models(self, settings: Any) -> dict[str, Any]:
        ...

class CliRuntimeMixin:
    def __init__(self) -> None:
        self._model_listing_cache: Optional[dict[str, Any]] = None
        self._model_listing_cache_at: float = 0.0

    def _resolve_executable(self, name: str) -> str:
        resolved = shutil.which(name)
        return resolved or name

    def _get_cached_model_listing(self) -> Optional[dict[str, Any]]:
        if not self._model_listing_cache:
            return None
        age = time.monotonic() - self._model_listing_cache_at
        if age >= _MODEL_LISTING_CACHE_TTL_SECONDS:
            return None
        cached = dict(self._model_listing_cache)
        cached["cached"] = True
        return cached

    def _store_model_listing(self, payload: dict[str, Any]) -> dict[str, Any]:
        stored = dict(payload)
        stored.setdefault("cached", False)
        self._model_listing_cache = stored
        self._model_listing_cache_at = time.monotonic()
        return dict(stored)
