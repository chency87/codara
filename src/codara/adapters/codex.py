import json
import asyncio
import os
import tempfile
import shutil
from datetime import datetime, timedelta, timezone
import logging
from typing import List, Optional, Any
from pathlib import Path
from codara.core.models import Session, Message, TurnResult
from codara.adapters.base import ProviderAdapter, CliRuntimeMixin
from codara.adapters.cli_monitor import communicate_with_stall_detection, terminate_process
from codara.cli_run_store import CliRunStore
from codara.config import get_provider_default_model, get_settings
from codara.core.models import ProviderType
from codara.telemetry import current_trace_context

logger = logging.getLogger(__name__)

class CodexAdapter(ProviderAdapter, CliRuntimeMixin):
    _SESSION_STATE_PATTERNS = (
        "sessions",
        "history.jsonl",
        "state*.sqlite*",
        "logs*.sqlite*",
    )

    def __init__(
        self,
        stall_timeout_seconds: Optional[int] = None,
    ):
        CliRuntimeMixin.__init__(self)
        self.stall_timeout_seconds = (
            int(stall_timeout_seconds)
            if stall_timeout_seconds is not None
            else int(get_settings().codex_stall_timeout_seconds)
        )

    async def send_turn(self, session: Session, messages: List[Message], provider_model: str) -> TurnResult:
        codex_bin = self._resolve_executable("codex")
        # Note: We rely on the local system's CLI login.
        # No longer using setup_isolated_env with managed credentials.
        settings = get_settings()
        store = CliRunStore() if settings.cli_capture_enabled else None
        output_path = Path(tempfile.gettempdir()) / f"uag-codex-{session.session_id}.txt"
        prompt = self._messages_to_prompt(messages)
        backend_id = session.backend_id
        session_key = session.client_session_id or session.session_id
        
        for allow_resume in (True, False):
            capture = store.allocate_run(provider=ProviderType.CODEX.value, session_id=session_key) if store else None
            command = [
                codex_bin,
                "--search",
                "exec",
                "--skip-git-repo-check",
                "--dangerously-bypass-approvals-and-sandbox",
                "--json",
                "--model",
                provider_model,
                "-o",
                str(output_path),
                "-C",
                session.cwd_path,
            ]
            if allow_resume and backend_id:
                command.extend(["resume", backend_id, "-"])
            else:
                command.append("-")

            proc = None

            try:
                if capture is not None:
                    context = current_trace_context()
                    store.write_meta(
                        capture.meta_path,
                        {
                            "run_id": capture.run_id,
                            "provider": ProviderType.CODEX.value,
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
                    input_data=prompt.encode("utf-8"),
                    stall_timeout_seconds=self.stall_timeout_seconds,
                    process_label="Codex CLI",
                    stdout_tee_path=capture.stdout_path if capture else None,
                    stderr_tee_path=capture.stderr_path if capture else None,
                )
                stdout_output = stdout.decode()
                stderr_output = stderr.decode()
                
                if proc.returncode != 0:
                    error_detail = self._extract_exec_error(stdout_output, stderr_output)
                    lowered = error_detail.lower()
                    
                    if allow_resume and backend_id and self._looks_like_invalid_resume_error(lowered):
                        logger.warning("Codex session %s is no longer resumable; retrying without resume", backend_id)
                        continue
                    
                    if "429" in lowered or "rate limit" in lowered or "rate_limit" in lowered:
                        raise RuntimeError(f"Codex Rate Limit: {error_detail}")
                    
                    if self._looks_like_local_auth_failure(lowered):
                        raise RuntimeError(f"Codex CLI is not logged in on the local system: {error_detail}")
                        
                    if capture is not None:
                        store.end_run(capture, status="error", exit_code=proc.returncode, error=error_detail)
                    raise RuntimeError(error_detail)

                resolved_backend_id, output_text = self._parse_exec_output(
                    stdout_output,
                    output_path,
                    current_backend_id=backend_id if allow_resume else "",
                )

                if capture is not None:
                    # Keep a stable snapshot of the file-based output alongside stdout/stderr capture.
                    try:
                        if output_path.exists():
                            shutil.copy2(output_path, capture.run_dir / "output.txt")
                    except Exception:
                        pass
                    store.end_run(capture, status="ok", exit_code=proc.returncode, error=None)
                return TurnResult(
                    output=output_text,
                    backend_id=resolved_backend_id,
                    finish_reason="stop",
                )
            except FileNotFoundError as exc:
                if capture is not None:
                    store.end_run(capture, status="error", exit_code=None, error="Codex CLI is not installed on the local system")
                raise RuntimeError("Codex CLI is not installed on the local system") from exc
            except Exception as exc:
                if capture is not None and proc is not None and proc.returncode is None:
                    store.end_run(capture, status="stalled", exit_code=None, error=str(exc))
                raise
            finally:
                if proc is not None and proc.returncode is None:
                    await terminate_process(proc)
        
        raise RuntimeError("Codex CLI exec failed: unable to recover from invalid session state")

    async def resume_session(self, backend_id: str) -> Session:
        """Returns a session object representing the resumed backend_id."""
        now = datetime.now(timezone.utc)
        return Session(
            session_id="",
            workspace_id="",
            client_session_id="",
            backend_id=backend_id,
            provider=ProviderType.CODEX,
            user_id="",
            cwd_path=os.getcwd(),
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(hours=24),
        )

    async def terminate_session(self, backend_id: str) -> None:
        pass

    async def list_models(self, settings: Any) -> dict[str, Any]:
        cached = self._get_cached_model_listing()
        if cached is not None:
            return cached
        from codara.config import get_provider_default_model
        from codara.core.models import ProviderType
        default_model = get_provider_default_model(ProviderType.CODEX, settings)
        runtime_available = True
        detail = "Codex CLI does not expose a stable non-interactive models listing command."
        try:
            self._resolve_executable("codex")
        except RuntimeError as exc:
            runtime_available = False
            detail = str(exc)
        return self._store_model_listing(
            {
                "provider": ProviderType.CODEX.value,
                "default_model": default_model,
                "models": [default_model],
                "source": "config",
                "status": "fallback" if runtime_available else "unavailable",
                "runtime_available": runtime_available,
                "detail": detail,
            }
        )

    def _messages_to_prompt(self, messages: List[Message]) -> str:
        parts = []
        for message in messages:
            role = getattr(message, "role", "user")
            content = getattr(message, "content", "")
            parts.append(f"{role.upper()}:\n{content}")
        return "\n\n".join(parts)

    def _parse_exec_output(self, stdout: str, output_path: Path, current_backend_id: str) -> tuple[str, str]:
        backend_id = current_backend_id
        fallback_output = ""

        for line in stdout.splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "thread.started":
                backend_id = event.get("thread_id", backend_id)
            elif event.get("type") == "item.completed":
                item = event.get("item") or {}
                if item.get("type") == "agent_message":
                    fallback_output = item.get("text", fallback_output)

        output_text = output_path.read_text(encoding="utf-8").strip() if output_path.exists() else fallback_output
        if not output_text:
            output_text = fallback_output
        if not output_text:
            raise RuntimeError("Codex exec did not return an assistant message")
        return backend_id, output_text

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
            "thread not found",
            "failed to record rollout items",
        )
        return any(indicator in lowered_stderr for indicator in indicators)

    def _extract_exec_error(self, stdout: str, stderr: str) -> str:
        stderr_output = stderr.strip()
        if stderr_output:
            return stderr_output

        extracted: list[str] = []
        for line in stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            event = self._try_parse_json(stripped)
            if not isinstance(event, dict):
                continue
            for candidate in self._iter_error_candidates(event):
                normalized = candidate.strip()
                if normalized and normalized not in extracted:
                    extracted.append(normalized)

        if extracted:
            return " | ".join(extracted)

        stdout_output = stdout.strip()
        if stdout_output:
            return stdout_output
        return "Codex exec failed"

    def _iter_error_candidates(self, value: Any) -> list[str]:
        candidates: list[str] = []
        if isinstance(value, dict):
            event_type = value.get("type")
            if isinstance(event_type, str) and "error" in event_type.lower():
                for key in ("message", "error", "detail", "text"):
                    item = value.get(key)
                    if isinstance(item, str) and item.strip():
                        candidates.append(item)
                    elif isinstance(item, dict):
                        candidates.extend(self._iter_error_candidates(item))
            for key in ("error", "message", "detail"):
                item = value.get(key)
                if isinstance(item, str) and item.strip():
                    candidates.append(item)
                elif isinstance(item, dict):
                    candidates.extend(self._iter_error_candidates(item))
            item = value.get("item")
            if isinstance(item, dict):
                candidates.extend(self._iter_error_candidates(item))
        return candidates

    def _try_parse_json(self, value: str) -> Optional[Any]:
        stripped = value.strip()
        if not (stripped.startswith("{") or stripped.startswith("[")):
            return None
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return None
