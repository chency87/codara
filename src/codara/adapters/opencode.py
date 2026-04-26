import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

from codara.adapters.base import ProviderAdapter, CliRuntimeMixin
from codara.adapters.cli_monitor import communicate_with_stall_detection, terminate_process
from codara.cli_run_store import CliRunStore
from codara.config import get_provider_default_model, get_settings
from codara.core.models import Message, ProviderType, Session, TurnResult
from codara.telemetry import current_trace_context

logger = logging.getLogger(__name__)


class OpenCodeAdapter(ProviderAdapter, CliRuntimeMixin):
    def __init__(self, stall_timeout_seconds: Optional[int] = None):
        CliRuntimeMixin.__init__(self)
        self.stall_timeout_seconds = (
            int(stall_timeout_seconds)
            if stall_timeout_seconds is not None
            else int(get_settings().opencode_stall_timeout_seconds)
        )

    async def send_turn(self, session: Session, messages: List[Message], provider_model: str) -> TurnResult:
        opencode_bin = self._resolve_executable("opencode")
        settings = get_settings()
        store = CliRunStore() if settings.cli_capture_enabled else None
        session_key = session.client_session_id or session.session_id
        prompt = self._messages_to_prompt(messages)
        backend_id = session.backend_id
        for allow_resume in (True, False):
            capture = store.allocate_run(provider=ProviderType.OPENCODE.value, session_id=session_key) if store else None
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
                if capture is not None:
                    context = current_trace_context()
                    store.write_meta(
                        capture.meta_path,
                        {
                            "run_id": capture.run_id,
                            "provider": ProviderType.OPENCODE.value,
                            "session_id": session_key,
                            "cwd": session.cwd_path,
                            "command": command,
                            "provider_model": provider_model,
                            "attempt": "resume" if allow_resume and backend_id else "fresh",
                            "status": "running",
                            "started_at": datetime.now(timezone.utc).isoformat(),
                            "ended_at": None,
                            "exit_code": None,
                            "trace_id": context.trace_id if context else None,
                            "request_id": context.request_id if context else None,
                            "error": None,
                        },
                    )
                    store.write_prompt(capture.prompt_path, prompt)
                # Note: We rely on the local system's CLI login.
                proc = await asyncio.create_subprocess_exec(
                    *command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=os.environ.copy(),
                    cwd=session.cwd_path,
                )
                stdout, stderr = await communicate_with_stall_detection(
                    proc,
                    stall_timeout_seconds=self.stall_timeout_seconds,
                    process_label="OpenCode CLI",
                    stdout_tee_path=capture.stdout_path if capture else None,
                    stderr_tee_path=capture.stderr_path if capture else None,
                )
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
                    if capture is not None:
                        store.end_run(capture, status="error", exit_code=proc.returncode, error=stderr_output or "unknown OpenCode CLI error")
                    raise RuntimeError(f"OpenCode CLI exec failed: {stderr_output or 'unknown OpenCode CLI error'}")

                resolved_backend_id, output_text = self._parse_exec_output(
                    stdout.decode(),
                    current_backend_id=backend_id if allow_resume else "",
                )
                if capture is not None:
                    store.end_run(capture, status="ok", exit_code=proc.returncode, error=None)
                return TurnResult(
                    output=output_text,
                    backend_id=resolved_backend_id,
                    finish_reason="stop",
                )
            except FileNotFoundError as exc:
                if capture is not None:
                    store.end_run(capture, status="error", exit_code=None, error="OpenCode CLI is not installed on the local system")
                raise RuntimeError("OpenCode CLI is not installed on the local system") from exc
            except Exception as exc:
                if capture is not None and proc is not None and proc.returncode is None:
                    store.end_run(capture, status="stalled", exit_code=None, error=str(exc))
                raise
            finally:
                if proc is not None and proc.returncode is None:
                    await terminate_process(proc)
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
            session_id="",  # Placeholder for resume
            workspace_id="", # Placeholder for resume
            client_session_id="",
            backend_id=backend_id,
            provider=ProviderType.OPENCODE,
            user_id="",
            cwd_path=os.getcwd(),
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(hours=24),
        )

    async def terminate_session(self, backend_id: str) -> None:
        return None

    def _messages_to_prompt(self, messages: List[Message]) -> str:
        parts = []
        for message in messages:
            role = getattr(message, "role", "user")
            content = getattr(message, "content", "")
            parts.append(f"{role.upper()}:\n{content}")
        return "\n\n".join(parts)

    def _parse_exec_output(self, stdout: str, current_backend_id: str) -> tuple[str, str]:
        backend_id = current_backend_id
        output_parts: list[str] = []
        final_output = ""
        seen_event_types: list[str] = []

        for payload in self._iter_json_objects(stdout):
            backend_id = (
                str(payload.get("session_id") or payload.get("sessionID") or payload.get("id") or backend_id)
                if payload.get("type") in {"session.created", "session.ready", "run.started", "turn.done"}
                or "session_id" in payload
                or "sessionID" in payload
                else backend_id
            )

            event_type = str(payload.get("type") or "")
            if event_type:
                seen_event_types.append(event_type)
            if event_type == "error":
                error_message = self._extract_error_message(payload)
                raise RuntimeError(f"OpenCode CLI error: {error_message or 'unknown OpenCode CLI error'}")
            if event_type in {"message.delta", "turn.delta", "text"}:
                text = self._extract_message_text(payload)
                if isinstance(text, str) and text:
                    output_parts.append(text)
            elif event_type in {"message.completed", "assistant.message", "assistant.completed"}:
                text = self._extract_message_text(payload)
                if isinstance(text, str) and text.strip():
                    final_output = text.strip()
            elif event_type in {"turn.done", "step_finish"}:
                pass

            if not final_output:
                response = self._extract_message_text(payload.get("response"))
                if isinstance(response, str) and response.strip():
                    final_output = response.strip()

        output_text = final_output or "".join(output_parts).strip()
        if not output_text:
            event_summary = ", ".join(seen_event_types[:8]) if seen_event_types else "none"
            raise RuntimeError(f"OpenCode CLI did not return an assistant message (events: {event_summary})")
        return backend_id, output_text

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

    def _extract_error_message(self, payload: Any) -> str:
        if isinstance(payload, str):
            return payload
        if isinstance(payload, list):
            messages = [self._extract_error_message(item).strip() for item in payload]
            return "; ".join(message for message in messages if message)
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict):
                data_message = self._extract_error_message(data)
                if data_message:
                    return data_message
            error = payload.get("error")
            if isinstance(error, (dict, list, str)):
                error_message = self._extract_error_message(error)
                if error_message:
                    return error_message
            for key in ("message", "detail", "name"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
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
