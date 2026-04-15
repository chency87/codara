import asyncio
import json
import logging
import os
import pty
import re
import select
import subprocess
import time
from typing import Any, List, Optional

import httpx

from codara.core.models import Session, Message, TurnResult
from codara.adapters.base import ProviderAdapter, ConfigIsolationMixin, CliRuntimeMixin
from codara.database.manager import DatabaseManager
from codara.config import get_provider_default_model
from codara.core.models import ProviderType

logger = logging.getLogger(__name__)
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

class GeminiAdapter(ProviderAdapter, ConfigIsolationMixin, CliRuntimeMixin):
    def __init__(self, db_manager: Optional[DatabaseManager] = None, base_url: Optional[str] = None):
        ConfigIsolationMixin.__init__(self, db_manager)
        CliRuntimeMixin.__init__(self)
        self.base_url = base_url or os.getenv("GEMINI_BASE_URL", "https://api.gemini.ai")

    async def send_turn(self, session: Session, messages: List[Message], provider_model: str) -> TurnResult:
        self._resolve_executable("gemini")
        prompt = self._messages_to_prompt(messages)
        backend_id = session.backend_id
        for allow_resume in (True, False):
            proc = None
            try:
                command = self._build_turn_command(
                    backend_id=backend_id if allow_resume else "",
                    provider_model=provider_model,
                )
                proc = await asyncio.create_subprocess_exec(
                    *command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=os.environ.copy(),
                    cwd=session.cwd_path,
                )

                stdout, stderr = await proc.communicate(prompt.encode("utf-8"))
                stderr_output = self._sanitize_exec_stderr(stderr.decode())
                if proc.returncode != 0:
                    lowered = stderr_output.lower()
                    if allow_resume and backend_id and self._looks_like_invalid_resume_error(lowered):
                        logger.warning("Gemini session %s is no longer resumable; retrying without --resume", backend_id)
                        continue
                    if "429" in lowered or "rate limit" in lowered or "rate_limit" in lowered:
                        raise RuntimeError(f"Gemini Rate Limit: {stderr_output}")
                    if self._looks_like_local_auth_failure(lowered):
                        raise RuntimeError(f"Gemini CLI is not logged in on the local system: {stderr_output}")
                    detail = stderr_output or "unknown Gemini CLI error"
                    raise RuntimeError(f"Gemini CLI exec failed: {detail}")

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
                raise RuntimeError("Gemini CLI is not installed on the local system") from exc
            finally:
                if proc is not None and proc.returncode is None:
                    proc.terminate()
                    await proc.wait()
        raise RuntimeError("Gemini CLI exec failed: unable to recover from invalid session state")

    async def list_models(self, settings: Any) -> dict[str, Any]:
        cached = self._get_cached_model_listing()
        if cached is not None:
            return cached
        default_model = get_provider_default_model(ProviderType.GEMINI, settings)
        runtime_available = True
        detail = "Gemini CLI does not expose a stable non-interactive models listing command."
        try:
            self._resolve_executable("gemini")
        except RuntimeError as exc:
            runtime_available = False
            detail = str(exc)
        return self._store_model_listing(
            {
                "provider": ProviderType.GEMINI.value,
                "default_model": default_model,
                "models": [default_model],
                "source": "config",
                "status": "fallback" if runtime_available else "unavailable",
                "runtime_available": runtime_available,
                "detail": detail,
            }
        )

    async def resume_session(self, backend_id: str) -> Session:
        """Returns a session object representing the resumed backend_id."""
        return Session(
            session_id=None,
            backend_id=backend_id,
            provider="gemini",
            account_id="",
            cwd_path=os.getcwd()
        )

    async def terminate_session(self, backend_id: str) -> None:
        pass

    async def refresh_access_token(self, credential_blob: str) -> Optional[str]:
        # This is now handled by the local system's Gemini CLI configuration
        logger.warning("refresh_access_token called on GeminiAdapter, but this is now a system-level concern.")
        return None

    async def collect_usage(self, account: Any, credential: Optional[str], settings: Any) -> Optional[dict]:
        account_id = getattr(account, "account_id", "gemini-system")
        try:
            return await asyncio.to_thread(self._run_system_cli_stats)
        except FileNotFoundError:
            logger.warning("Gemini CLI not found while collecting usage for %s", account_id)
            return None
        except Exception as exc:
            logger.warning("Gemini CLI stats collection failed for %s: %s", account_id, exc)
            return None

    def _run_system_cli_stats(self) -> Optional[dict]:
        env = os.environ.copy()
        session_output, model_output = self._run_stats_commands(env, os.getcwd())
        return self._extract_cli_stats_usage(session_output, model_output)

    async def _collect_wham_usage(self, bearer_token: str, settings: Any) -> Optional[dict]:
        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "User-Agent": "Gemini-CLI/usage-probe",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            endpoints = self._usage_endpoints(settings)
            
            async def probe(url):
                try:
                    resp = await client.get(url, headers=headers)
                    if resp.status_code == 200:
                        return resp.json()
                    if resp.status_code in {401, 403}:
                        return "auth_failed"
                    if resp.status_code == 429:
                        return "rate_limited"
                except httpx.HTTPError:
                    pass
                return None

            results = await asyncio.gather(*(probe(url) for url in endpoints))
            
            auth_failed = False
            for res in results:
                if isinstance(res, dict):
                    extracted = self._extract_wham_usage(res)
                    if extracted:
                        return {"status": "ready", **extracted}
                elif res == "auth_failed":
                    auth_failed = True
                elif res == "rate_limited":
                    return {"status": "cooldown", "rate_limited": True}
                    
        if auth_failed:
            return {"status": "expired", "auth_failed": True}
        return {"status": "error"}

    def _usage_endpoints(self, settings: Any) -> tuple[str, ...]:
        configured = getattr(settings, "gemini_usage_endpoints", "")
        if configured:
            endpoints = tuple(url.strip() for url in str(configured).split(",") if url.strip())
            if endpoints:
                return endpoints
        return (f"{self.base_url}/v1/usage",)

    def _extract_wham_usage(self, payload: dict) -> Optional[dict]:
        if not isinstance(payload, dict):
            return None
        
        rate = payload.get("rate_limit") or {}
        primary = rate.get("primary_window") or {}
        secondary = rate.get("secondary_window") or {}
        credits = payload.get("credits") or {}

        hourly_pct_used = float(primary.get("used_percent", 0))
        weekly_pct_used = float(secondary.get("used_percent", 0))
        reset_after_5h = primary.get("reset_after_seconds")
        reset_after_week = secondary.get("reset_after_seconds")
        reset_at_5h = primary.get("reset_at")
        reset_at_week = secondary.get("reset_at")
        credit_balance = credits.get("balance")

        usage: dict[str, Any] = {
            "usage_source": "wham",
            "plan_type": payload.get("plan_type"),
            "rate_limit_allowed": rate.get("allowed"),
            "rate_limit_reached": rate.get("limit_reached"),
            "hourly_used_pct": hourly_pct_used,
            "weekly_used_pct": weekly_pct_used,
            "hourly_reset_after_seconds": int(reset_after_5h) if isinstance(reset_after_5h, (int, float)) else None,
            "weekly_reset_after_seconds": int(reset_after_week) if isinstance(reset_after_week, (int, float)) else None,
            "hourly_reset_at": int(reset_at_5h) if isinstance(reset_at_5h, (int, float)) else None,
            "weekly_reset_at": int(reset_at_week) if isinstance(reset_at_week, (int, float)) else None,
            "credits_has_credits": credits.get("has_credits"),
            "credits_unlimited": credits.get("unlimited"),
            "credits_overage_limit_reached": credits.get("overage_limit_reached"),
        }
        
        approx_local = credits.get("approx_local_messages")
        if isinstance(approx_local, list) and len(approx_local) >= 2:
            usage["approx_local_messages_min"] = int(approx_local[0])
            usage["approx_local_messages_max"] = int(approx_local[1])
            
        approx_cloud = credits.get("approx_cloud_messages")
        if isinstance(approx_cloud, list) and len(approx_cloud) >= 2:
            usage["approx_cloud_messages_min"] = int(approx_cloud[0])
            usage["approx_cloud_messages_max"] = int(approx_cloud[1])
            
        if isinstance(credit_balance, str):
            try:
                usage["credits_balance"] = float(credit_balance)
            except ValueError:
                pass
                
        return usage

    def _resolve_bearer_token(self, credential: Optional[str]) -> Optional[str]:
        if not credential:
            return None
        stripped = credential.strip()
        payload = self._try_parse_json(stripped)
        if payload is not None:
            return self._deep_find_token(
                payload,
                {
                    "GEMINI_API_KEY",
                    "api_key",
                    "apiKey",
                    "access_token",
                    "accessToken",
                    "token",
                    "bearer_token",
                    "bearerToken",
                    "id_token",
                },
            )
        if " " in stripped or "\n" in stripped:
            return None
        return stripped

    def _collect_cli_stats_usage(self, account_id: str, credential: Optional[str]) -> Optional[dict]:
        temp_dir, env = self.setup_isolated_env("gemini", account_id)
        self._seed_isolated_gemini_credential(temp_dir, env, credential)
        try:
            session_output, model_output = self._run_stats_commands(env, os.getcwd())
            return self._extract_cli_stats_usage(session_output, model_output)
        finally:
            self.cleanup_isolated_env(temp_dir)

    def _seed_isolated_gemini_credential(self, temp_dir: str, env: dict[str, str], credential: Optional[str]) -> None:
        if self.pool or not credential:
            return
        gemini_dir = Path(temp_dir) / ".gemini"
        gemini_dir.mkdir(parents=True, exist_ok=True)
        oauth_path = gemini_dir / "oauth_creds.json"
        if not oauth_path.exists():
            self._write_text_if_changed(oauth_path, credential)
        if not (gemini_dir / "settings.json").exists():
            self._write_text_if_changed(
                gemini_dir / "settings.json",
                json.dumps({"security": {"auth": {"selectedType": "oauth-personal"}}}),
            )
        if not (gemini_dir / "projects.json").exists():
            self._write_text_if_changed(gemini_dir / "projects.json", json.dumps({"projects": {}}))
        (gemini_dir / "history").mkdir(parents=True, exist_ok=True)
        (gemini_dir / "tmp").mkdir(parents=True, exist_ok=True)
        token = self._resolve_bearer_token(credential)
        if token:
            env["GEMINI_API_KEY"] = token

    def _run_stats_commands(self, env: dict[str, str], cwd: str) -> tuple[str, str]:
        command = ["gemini", "--screen-reader", "--yolo"]
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            command,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=cwd,
            env=env,
            close_fds=True,
        )
        os.close(slave_fd)
        try:
            # Allow more time for initial CLI startup and network fetch
            self._read_pty_until_idle(master_fd, total_timeout=15.0, idle_timeout=2.0)
            
            session_output = self._send_pty_command(master_fd, "/stats")
            model_output = ""
            
            if self._needs_model_stats_breakdown(session_output):
                model_output = self._send_pty_command(master_fd, "/stats model")
            
            self._send_pty_command(master_fd, "/quit", read_output=False)
            self._read_pty_until_idle(master_fd, total_timeout=5.0, idle_timeout=0.5)
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.terminate()
                proc.wait(timeout=5.0)
            return session_output, model_output
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5.0)
            os.close(master_fd)

    def _send_pty_command(self, master_fd: int, command: str, read_output: bool = True) -> str:
        cmd_bytes = f"{command}\n".encode("utf-8")
        total_sent = 0
        while total_sent < len(cmd_bytes):
            sent = os.write(master_fd, cmd_bytes[total_sent:])
            if sent == 0:
                break
            total_sent += sent
        
        if not read_output:
            return ""
        return self._read_pty_until_idle(master_fd, total_timeout=10.0, idle_timeout=1.5)

    def _read_pty_until_idle(self, master_fd: int, total_timeout: float, idle_timeout: float) -> str:
        deadline = time.monotonic() + total_timeout
        last_data_at: Optional[float] = None
        chunks: list[bytes] = []
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master_fd], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(master_fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
                last_data_at = time.monotonic()
                continue
            if last_data_at is not None and time.monotonic() - last_data_at >= idle_timeout:
                break
        return b"".join(chunks).decode("utf-8", errors="ignore")

    def _needs_model_stats_breakdown(self, output: str) -> bool:
        cleaned = self._normalize_terminal_output(output)
        if "For a full token breakdown, run `/stats model`." in cleaned:
            return True
        if re.search(r"(?m)^(?:\s*[>»]?\s*)?Input(?: Tokens)?\s+\d", cleaned):
            return False
        if re.search(r"(?m)^(?:\s*[>»]?\s*)?Total\s+\d", cleaned):
            return False
        return True

    def _extract_cli_stats_usage(self, session_output: str, model_output: str) -> Optional[dict]:
        usage: dict[str, Any] = {
            "status": "ready",
            "usage_source": "cli_stats",
        }
        parsed_any = False

        session_usage = self._parse_session_usage_panel(session_output)
        if session_usage:
            usage.update(session_usage)
            parsed_any = True

        token_usage = self._parse_model_token_usage(model_output or session_output)
        if token_usage:
            usage.update(token_usage)
            parsed_any = True

        return usage if parsed_any else None

    def _parse_session_usage_panel(self, output: str) -> dict[str, Any]:
        cleaned = self._normalize_terminal_output(output)
        usage: dict[str, Any] = {}

        credits_match = re.search(r"Google AI Credits:\s*([0-9][0-9,]*(?:\.\d+)?)", cleaned)
        if credits_match:
            usage["credits_balance"] = float(credits_match.group(1).replace(",", ""))

        pct_match = re.search(r"(\d+(?:\.\d+)?)%\s+used(?:\s*\(Limit resets in ([^)]+)\))?", cleaned)
        limit_match = re.search(r"Usage limit:\s*([0-9][0-9,]*)", cleaned)
        if pct_match:
            usage["hourly_used_pct"] = float(pct_match.group(1))
            reset_after = self._parse_reset_after_seconds(pct_match.group(2) or "")
            if reset_after is not None:
                usage["hourly_reset_after_seconds"] = reset_after
        if limit_match:
            limit = int(limit_match.group(1).replace(",", ""))
            usage["hourly_limit"] = limit
            if "hourly_used_pct" in usage:
                usage["usage_hourly"] = int(round(limit * float(usage["hourly_used_pct"]) / 100.0))

        return usage

    def _parse_model_token_usage(self, output: str) -> Optional[dict[str, Any]]:
        cleaned = self._normalize_terminal_output(output)
        summary = self._parse_model_stats_summary(cleaned)
        if summary:
            return summary
        table = self._parse_model_usage_table(cleaned)
        if table:
            return table
        return None

    def _parse_model_stats_summary(self, cleaned: str) -> Optional[dict[str, Any]]:
        requests_match = re.search(r"(?m)^\s*Requests\s+([0-9][0-9,]*)\s*$", cleaned)
        total_match = re.search(r"(?m)^\s*Total\s+([0-9][0-9,]*)\s*$", cleaned)
        input_match = re.search(r"(?m)^\s*(?:↳\s*)?Input(?: Tokens)?\s+([0-9][0-9,]*)\s*$", cleaned)
        output_match = re.search(r"(?m)^\s*(?:↳\s*)?Output(?: Tokens)?\s+([0-9][0-9,]*)\s*$", cleaned)

        if not any((requests_match, total_match, input_match, output_match)):
            return None

        total_tokens = self._parse_int_match(total_match) if total_match else None
        input_tokens = self._parse_int_match(input_match) if input_match else 0
        output_tokens = self._parse_int_match(output_match) if output_match else 0
        if total_tokens is None:
            total_tokens = input_tokens + output_tokens

        usage: dict[str, Any] = {}
        if total_tokens > 0:
            usage["usage_tpm"] = total_tokens
        if requests_match:
            usage["usage_rpd"] = self._parse_int_match(requests_match)
        return usage or None

    def _parse_model_usage_table(self, cleaned: str) -> Optional[dict[str, Any]]:
        total_requests = 0
        total_input = 0
        total_output = 0
        found = False

        for raw_line in cleaned.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("Model ") or line.startswith("Usage ") or line.startswith("For a full token breakdown"):
                continue
            columns = re.split(r"\s{2,}", line)
            if len(columns) < 5:
                continue
            reqs = self._try_parse_int(columns[1])
            input_tokens = self._try_parse_int(columns[2])
            output_tokens = self._try_parse_int(columns[4])
            if reqs is None or input_tokens is None or output_tokens is None:
                continue
            total_requests += reqs
            total_input += input_tokens
            total_output += output_tokens
            found = True

        if not found:
            return None

        return {
            "usage_tpm": total_input + total_output,
            "usage_rpd": total_requests,
        }

    def _normalize_terminal_output(self, output: str) -> str:
        normalized = output.replace("\r\n", "\n").replace("\r", "\n")
        normalized = ANSI_ESCAPE_RE.sub("", normalized)
        return "\n".join(line.rstrip() for line in normalized.splitlines())

    def _parse_reset_after_seconds(self, text: str) -> Optional[int]:
        if not text:
            return None
        total = 0
        found = False
        for amount, unit in re.findall(r"(\d+)\s*([dhms])", text.lower()):
            found = True
            value = int(amount)
            if unit == "d":
                total += value * 86400
            elif unit == "h":
                total += value * 3600
            elif unit == "m":
                total += value * 60
            elif unit == "s":
                total += value
        return total if found else None

    def _parse_int_match(self, match: re.Match[str]) -> int:
        return int(match.group(1).replace(",", ""))

    def _try_parse_int(self, value: str) -> Optional[int]:
        stripped = value.strip()
        match = re.search(r"[0-9][0-9,]*", stripped)
        if not match:
            return None
        return int(match.group(0).replace(",", ""))

    def _build_turn_command(self, backend_id: str, provider_model: str) -> list[str]:
        gemini_bin = self._resolve_executable("gemini")
        command = [
            gemini_bin,
            "--yolo",
            "--output-format",
            "json",
            "--model",
            provider_model,
            "--prompt",
            "",
        ]
        if backend_id:
            command.extend(["--resume", backend_id])
        return command

    def _sanitize_exec_stderr(self, stderr_output: str) -> str:
        lines = [
            line.strip()
            for line in stderr_output.splitlines()
            if line.strip() and "YOLO mode is enabled" not in line
        ]
        return "\n".join(lines).strip()

    def _looks_like_invalid_resume_error(self, lowered_stderr: str) -> bool:
        indicators = (
            "error resuming session",
            "invalid session identifier",
            "--list-sessions",
            "no such session",
        )
        return any(indicator in lowered_stderr for indicator in indicators)

    def _messages_to_prompt(self, messages: List[Message]) -> str:
        parts = []
        for message in messages:
            role = getattr(message, "role", "user")
            content = getattr(message, "content", "")
            parts.append(f"{role.upper()}:\n{content}")
        return "\n\n".join(parts)

    def _parse_exec_output(self, stdout: str, current_backend_id: str) -> tuple[str, Optional[int], str]:
        payload = self._parse_exec_payload(stdout)
        if not isinstance(payload, dict):
            raise RuntimeError("Gemini CLI returned invalid JSON output")

        backend_id = str(payload.get("session_id") or current_backend_id)
        response = payload.get("response")
        output_text = str(response).strip() if response is not None else ""
        if not output_text:
            raise RuntimeError("Gemini CLI did not return an assistant message")

        context_tokens: Optional[int] = None
        token_totals = self._extract_token_usage(payload)
        if token_totals is not None:
            context_tokens = token_totals["input_tokens"] + token_totals["output_tokens"]

        return backend_id, context_tokens, output_text

    def _parse_exec_payload(self, stdout: str) -> Optional[dict]:
        raw = stdout.strip()
        if not raw:
            return None
        try:
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else None
        except json.JSONDecodeError:
            for line in reversed(raw.splitlines()):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    payload = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    return payload
        return None

    def _extract_token_usage(self, payload: dict) -> Optional[dict[str, int]]:
        stats = payload.get("stats")
        if not isinstance(stats, dict):
            return None
        models = stats.get("models")
        if not isinstance(models, dict):
            return None

        input_tokens = 0
        output_tokens = 0
        found_usage = False
        for model_name, model_info in models.items():
            if model_name.lower() == "total" or not isinstance(model_info, dict):
                continue
            tokens = model_info.get("tokens")
            if not isinstance(tokens, dict):
                continue
            input_tokens += int(tokens.get("input") or tokens.get("prompt") or 0)
            output_tokens += int(tokens.get("candidates") or tokens.get("output") or 0)
            found_usage = True

        if not found_usage:
            return None
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }

    def _looks_like_local_auth_failure(self, lowered_stderr: str) -> bool:
        indicators = (
            "please set an auth method",
            "login",
            "logged in",
            "oauth",
            "settings.json",
            "gemini_api_key",
            "google_genai_use_vertexai",
            "google_genai_use_gca",
        )
        return any(indicator in lowered_stderr for indicator in indicators)
