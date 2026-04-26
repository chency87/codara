import asyncio
import os
import re
import json
from datetime import datetime, timedelta, timezone
import logging
from typing import List, Optional, Any
from pathlib import Path
from codara.core.models import Session, Message, TurnResult, ProviderType
from codara.adapters.base import ProviderAdapter, CliRuntimeMixin
from codara.adapters.cli_monitor import communicate_with_stall_detection, terminate_process
from codara.cli_run_store import CliRunStore
from codara.config import get_settings
from codara.telemetry import current_trace_context

logger = logging.getLogger(__name__)

class GeminiAdapter(ProviderAdapter, CliRuntimeMixin):
    def __init__(
        self,
        stall_timeout_seconds: Optional[int] = None
    ):
        CliRuntimeMixin.__init__(self)
        settings = get_settings()
        self.stall_timeout_seconds = (
            int(stall_timeout_seconds)
            if stall_timeout_seconds is not None
            else int(settings.gemini_stall_timeout_seconds)
        )

    async def send_turn(self, session: Session, messages: List[Message], provider_model: str) -> TurnResult:
        self._resolve_executable("gemini")
        settings = get_settings()
        store = CliRunStore() if settings.cli_capture_enabled else None
        session_key = session.client_session_id or session.session_id
        prompt = self._messages_to_prompt(messages)
        backend_id = session.backend_id
        for allow_resume in (True, False):
            capture = store.allocate_run(provider=ProviderType.GEMINI.value, session_id=session_key) if store else None
            proc = None
            try:
                command = self._build_turn_command(
                    backend_id=backend_id if allow_resume else "",
                    provider_model=provider_model,
                )
                if capture is not None:
                    context = current_trace_context()
                    store.write_meta(
                        capture.meta_path,
                        {
                            "run_id": capture.run_id,
                            "provider": ProviderType.GEMINI.value,
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
                # No longer using setup_isolated_env with managed credentials.
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
                    process_label="Gemini CLI",
                    stdout_tee_path=capture.stdout_path if capture else None,
                    stderr_tee_path=capture.stderr_path if capture else None,
                )
                stdout_output = stdout.decode()
                stderr_output = stderr.decode()
                
                if proc.returncode != 0:
                    lowered = stderr_output.lower()
                    
                    if allow_resume and backend_id and self._looks_like_invalid_resume_error(lowered):
                        logger.warning("Gemini session %s is no longer resumable; retrying without resume", backend_id)
                        continue
                        
                    if "429" in lowered or "rate limit" in lowered or "rate_limit" in lowered:
                        raise RuntimeError(f"Gemini Rate Limit: {stderr_output}")
                        
                    if self._looks_like_local_auth_failure(lowered):
                        raise RuntimeError(f"Gemini CLI is not logged in on the local system: {stderr_output}")
                        
                    if capture is not None:
                        store.end_run(capture, status="error", exit_code=proc.returncode, error=stderr_output.strip() or "Gemini CLI failed")
                    raise RuntimeError(f"Gemini CLI failed: {stderr_output}")

                new_backend_id = self._extract_backend_id(stdout_output) or backend_id
                if capture is not None:
                    store.end_run(capture, status="ok", exit_code=proc.returncode, error=None)
                return TurnResult(
                    output=self._clean_output(stdout_output),
                    backend_id=new_backend_id,
                    finish_reason="stop"
                )
            except Exception as e:
                if capture is not None and proc is not None and proc.returncode is None:
                    store.end_run(capture, status="stalled", exit_code=None, error=str(e))
                if proc and proc.returncode is None:
                    await terminate_process(proc)
                raise e
        raise RuntimeError("Failed to execute Gemini turn")

    async def resume_session(self, backend_id: str) -> Session:
        raise NotImplementedError("Resuming gemini session metadata not implemented")

    async def terminate_session(self, backend_id: str) -> None:
        pass

    async def list_models(self, settings: Any) -> dict[str, Any]:
        return {
            "provider": "gemini",
            "default_model": settings.gemini_default_model,
            "models": [settings.gemini_default_model],
            "source": "config",
            "status": "fallback",
            "runtime_available": True,
        }

    def _build_turn_command(self, backend_id: str, provider_model: str) -> List[str]:
        cmd = ["gemini", "exec", "--model", provider_model, "--yolo"]
        if backend_id:
            cmd.extend(["--resume", backend_id])
        return cmd

    def _messages_to_prompt(self, messages: List[Message]) -> str:
        if not messages:
            return ""
        return messages[-1].content

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

    def _extract_backend_id(self, stdout: str) -> Optional[str]:
        try:
            data = json.loads(stdout)
            if isinstance(data, dict):
                return data.get("session_id") or data.get("sessionID")
        except json.JSONDecodeError:
            pass
            
        match = re.search(r"Session ID:\s*([a-zA-Z0-9_-]+)", stdout)
        return match.group(1) if match else None

    def _clean_output(self, stdout: str) -> str:
        try:
            data = json.loads(stdout)
            if isinstance(data, dict) and "response" in data:
                return str(data["response"]).strip()
        except json.JSONDecodeError:
            pass
            
        lines = []
        for line in stdout.splitlines():
            if "Session ID:" in line or "Input Tokens:" in line or "Output Tokens:" in line:
                continue
            lines.append(line)
        return "\n".join(lines).strip()
