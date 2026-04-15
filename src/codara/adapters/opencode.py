import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

from codara.adapters.base import ProviderAdapter, CliRuntimeMixin
from codara.config import get_provider_default_model
from codara.core.models import Message, ProviderType, Session, TurnResult

logger = logging.getLogger(__name__)


class OpenCodeAdapter(ProviderAdapter, CliRuntimeMixin):
    def __init__(self):
        CliRuntimeMixin.__init__(self)

    async def send_turn(self, session: Session, messages: List[Message], provider_model: str) -> TurnResult:
        opencode_bin = self._resolve_executable("opencode")
        prompt = self._messages_to_prompt(messages)
        backend_id = session.backend_id
        for allow_resume in (True, False):
            command = [
                opencode_bin,
                "run",
                "--format",
                "json",
                "--model",
                provider_model,
                "--dir",
                session.cwd_path,
                "--dangerously-skip-permissions",
            ]
            if allow_resume and backend_id:
                command.extend(["--session", backend_id])
            command.append(prompt)

            proc = None
            try:
                proc = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=os.environ.copy(),
                    cwd=session.cwd_path,
                )
                stdout, stderr = await proc.communicate()
                stderr_output = stderr.decode().strip()
                if proc.returncode != 0:
                    lowered = stderr_output.lower()
                    if allow_resume and backend_id and self._looks_like_invalid_resume_error(lowered):
                        logger.warning("OpenCode session %s is no longer resumable; retrying without --session", backend_id)
                        continue
                    if "429" in lowered or "rate limit" in lowered or "rate_limit" in lowered:
                        raise RuntimeError(f"OpenCode Rate Limit: {stderr_output}")
                    if self._looks_like_local_auth_failure(lowered):
                        raise RuntimeError(f"OpenCode CLI is not logged in on the local system: {stderr_output}")
                    raise RuntimeError(f"OpenCode CLI exec failed: {stderr_output or 'unknown OpenCode CLI error'}")

                resolved_backend_id, context_tokens, output_text = self._parse_exec_output(
                    stdout.decode(),
                    current_backend_id=backend_id if allow_resume else "",
                )
                return TurnResult(
                    output=output_text,
                    backend_id=resolved_backend_id,
                    finish_reason="stop",
                    context_tokens=context_tokens,
                )
            except FileNotFoundError as exc:
                raise RuntimeError("OpenCode CLI is not installed on the local system") from exc
            finally:
                if proc is not None and proc.returncode is None:
                    proc.terminate()
                    await proc.wait()
        raise RuntimeError("OpenCode CLI exec failed: unable to recover from invalid session state")

    async def list_models(self, settings: Any) -> dict[str, Any]:
        cached = self._get_cached_model_listing()
        if cached is not None:
            return cached

        default_model = get_provider_default_model(ProviderType.OPENCODE, settings)
        runtime_available = True
        try:
            executable = self._resolve_executable("opencode")
        except RuntimeError as exc:
            runtime_available = False
            return self._store_model_listing(
                {
                    "provider": ProviderType.OPENCODE.value,
                    "default_model": default_model,
                    "models": [default_model],
                    "source": "config",
                    "status": "unavailable",
                    "runtime_available": False,
                    "detail": str(exc),
                }
            )

        command = [executable, "models"]
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=os.environ.copy(),
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                models = self._parse_models_output(stdout.decode(), default_model)
                return self._store_model_listing(
                    {
                        "provider": ProviderType.OPENCODE.value,
                        "default_model": default_model,
                        "models": models,
                        "source": "cli",
                        "status": "ok",
                        "runtime_available": True,
                        "detail": None,
                    }
                )
            detail = stderr.decode().strip() or "OpenCode models command failed"
        except RuntimeError as exc:
            detail = str(exc)
            runtime_available = False
        except FileNotFoundError:
            detail = "OpenCode CLI is not installed on the local system"
            runtime_available = False

        return self._store_model_listing(
            {
                "provider": ProviderType.OPENCODE.value,
                "default_model": default_model,
                "models": [default_model],
                "source": "config",
                "status": "fallback",
                "runtime_available": runtime_available,
                "detail": detail,
            }
        )

    async def resume_session(self, backend_id: str) -> Session:
        now = datetime.now(timezone.utc)
        return Session(
            client_session_id="",
            backend_id=backend_id,
            provider=ProviderType.OPENCODE,
            account_id="opencode-system",
            cwd_path=os.getcwd(),
            prefix_hash="",
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(hours=24),
        )

    async def terminate_session(self, backend_id: str) -> None:
        return None

    async def collect_usage(self, account: Any, credential: Optional[str], settings: Any) -> Optional[dict]:
        logger.warning("Background usage collection for OpenCode is not yet supported.")
        return None

    def _messages_to_prompt(self, messages: List[Message]) -> str:
        parts = []
        for message in messages:
            role = getattr(message, "role", "user")
            content = getattr(message, "content", "")
            parts.append(f"{role.upper()}:\n{content}")
        return "\n\n".join(parts)

    def _parse_exec_output(self, stdout: str, current_backend_id: str) -> tuple[str, Optional[int], str]:
        backend_id = current_backend_id
        output_parts: list[str] = []
        final_output = ""
        context_tokens: Optional[int] = None

        for payload in self._iter_json_objects(stdout):
            backend_id = (
                str(payload.get("session_id") or payload.get("sessionID") or payload.get("id") or backend_id)
                if payload.get("type") in {"session.created", "session.ready", "run.started", "turn.done"}
                or "session_id" in payload
                or "sessionID" in payload
                else backend_id
            )

            event_type = str(payload.get("type") or "")
            if event_type in {"message.delta", "turn.delta", "text"}:
                text = self._extract_message_text(payload)
                if isinstance(text, str) and text:
                    output_parts.append(text)
            elif event_type in {"message.completed", "assistant.message", "assistant.completed"}:
                text = self._extract_message_text(payload)
                if isinstance(text, str) and text.strip():
                    final_output = text.strip()
            elif event_type in {"turn.done", "step_finish"}:
                usage = payload.get("usage")
                if not isinstance(usage, dict):
                    part = payload.get("part")
                    if isinstance(part, dict):
                        usage = part.get("tokens")
                if isinstance(usage, dict):
                    input_tokens = int(usage.get("input_tokens") or usage.get("inputTokens") or usage.get("input") or 0)
                    output_tokens = int(usage.get("output_tokens") or usage.get("outputTokens") or usage.get("output") or 0)
                    if input_tokens or output_tokens:
                        context_tokens = input_tokens + output_tokens

            if not final_output:
                response = self._extract_message_text(payload.get("response"))
                if isinstance(response, str) and response.strip():
                    final_output = response.strip()

        output_text = final_output or "".join(output_parts).strip()
        if not output_text:
            raise RuntimeError("OpenCode CLI did not return an assistant message")
        return backend_id, context_tokens, output_text

    def _iter_json_objects(self, stdout: str) -> list[dict[str, Any]]:
        raw = stdout.strip()
        if not raw:
            return []
        objects: list[dict[str, Any]] = []
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            return [payload]
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]

        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                objects.append(payload)
        return objects

    def _looks_like_local_auth_failure(self, lowered_stderr: str) -> bool:
        indicators = (
            "login",
            "logged in",
            "provider",
            "credential",
            "auth",
            "api key",
        )
        return any(indicator in lowered_stderr for indicator in indicators)

    def _looks_like_invalid_resume_error(self, lowered_stderr: str) -> bool:
        indicators = (
            "session not found",
            "invalid session",
            "unknown session",
            "no session",
        )
        return any(indicator in lowered_stderr for indicator in indicators)

    def _extract_message_text(self, payload: Any) -> str:
        if isinstance(payload, str):
            return payload
        if isinstance(payload, list):
            parts = [self._extract_message_text(item).strip() for item in payload]
            return "\n".join(part for part in parts if part)
        if isinstance(payload, dict):
            for key in ("text", "content", "message", "response", "delta", "part"):
                value = payload.get(key)
                extracted = self._extract_message_text(value)
                if extracted.strip():
                    return extracted
            if payload.get("type") == "text" and isinstance(payload.get("text"), str):
                return payload["text"]
        return ""

    def _parse_models_output(self, stdout: str, default_model: str) -> list[str]:
        raw = stdout.strip()
        models: list[str] = []
        if raw:
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, list):
                for item in payload:
                    if isinstance(item, dict):
                        candidate = item.get("id") or item.get("name") or item.get("model")
                        if isinstance(candidate, str) and candidate.strip():
                            models.append(candidate.strip())
                    elif isinstance(item, str) and item.strip():
                        models.append(item.strip())
            else:
                for line in raw.splitlines():
                    stripped = line.strip()
                    if not stripped or stripped.lower().startswith(("provider", "model", "id", "name")):
                        continue
                    candidate = stripped.split()[0]
                    if candidate and candidate not in {"-", "|"}:
                        models.append(candidate)
        if default_model not in models:
            models.insert(0, default_model)
        deduped: list[str] = []
        for model in models:
            if model not in deduped:
                deduped.append(model)
        return deduped
