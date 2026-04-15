import json
import asyncio
import os
import shutil
from datetime import datetime, timedelta, timezone
import logging
from typing import List, Optional, Any
import httpx
from pathlib import Path
from codara.core.models import Session, Message, TurnResult
from codara.adapters.base import ProviderAdapter, ConfigIsolationMixin, CliRuntimeMixin
from codara.database.manager import DatabaseManager
from codara.config import get_provider_default_model
from codara.core.models import ProviderType

logger = logging.getLogger(__name__)

class CodexAdapter(ProviderAdapter, ConfigIsolationMixin, CliRuntimeMixin):
    _SESSION_STATE_PATTERNS = (
        "sessions",
        "history.jsonl",
        "state*.sqlite*",
        "logs*.sqlite*",
    )

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        ConfigIsolationMixin.__init__(self, db_manager)
        CliRuntimeMixin.__init__(self)

    async def send_turn(self, session: Session, messages: List[Message], provider_model: str) -> TurnResult:
        codex_bin = self._resolve_executable("codex")
        temp_dir, env = self.setup_isolated_env("codex", session.account_id, session=session)
        output_path = Path(temp_dir) / "last-message.txt"
        prompt = self._messages_to_prompt(messages)
        command = [
            codex_bin,
            "exec",
            "--skip-git-repo-check",
            "--full-auto",
            "--json",
            "--model",
            provider_model,
            "-o",
            str(output_path),
            "-C",
            session.cwd_path,
        ]
        if session.backend_id:
            command.extend(["resume", session.backend_id, "-"])
        else:
            command.append("-")

        proc = None

        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=session.cwd_path,
            )
            stdout, stderr = await proc.communicate(prompt.encode("utf-8"))
            stdout_output = stdout.decode()
            stderr_output = stderr.decode()
            if proc.returncode != 0:
                error_detail = self._extract_exec_error(stdout_output, stderr_output)
                lowered = error_detail.lower()
                if "429" in lowered or "rate limit" in lowered or "rate_limit" in lowered:
                    raise RuntimeError(f"Codex Rate Limit: {error_detail}")
                raise RuntimeError(error_detail)

            backend_id, context_tokens, output_text = self._parse_exec_output(
                stdout_output,
                output_path,
                session.backend_id,
            )

            return TurnResult(
                output=output_text,
                backend_id=backend_id,
                finish_reason="stop",
                context_tokens=context_tokens
            )
        except FileNotFoundError as exc:
            raise RuntimeError("Codex CLI is not installed on the local system") from exc

        finally:
            if proc is not None and proc.returncode is None:
                proc.terminate()
                await proc.wait()

    async def resume_session(self, backend_id: str) -> Session:
        """Returns a session object representing the resumed backend_id."""
        return Session(
            session_id=None,
            backend_id=backend_id,
            provider="codex",
            account_id="",
            cwd_path=os.getcwd()
        )

    async def terminate_session(self, backend_id: str) -> None:
        pass

    def sync_account_session_state(self, source_account_id: str, target_account_id: str) -> bool:
        if source_account_id == target_account_id:
            return False
        return self.sync_provider_state(
            "codex",
            source_account_id,
            target_account_id,
            self._SESSION_STATE_PATTERNS,
        )

    async def list_models(self, settings: Any) -> dict[str, Any]:
        cached = self._get_cached_model_listing()
        if cached is not None:
            return cached
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

    async def collect_usage(self, account: Any, credential: Optional[str], settings: Any) -> Optional[dict]:
        auth_type_value = getattr(getattr(account, "auth_type", None), "value", str(getattr(account, "auth_type", "")))
        if auth_type_value == "OAUTH_SESSION" and credential:
            access_token = self._resolve_access_token(credential)
            if access_token:
                return await self._collect_wham_usage(access_token, settings)

        if credential:
            api_key = self._resolve_api_key(credential)
            if api_key:
                return await self._collect_org_usage(api_key)

        configured_api_key = self._resolve_api_key(getattr(settings, "codex_billing_api_key", "") or "")
        if configured_api_key:
            return await self._collect_org_usage(configured_api_key)

        return None

    async def _collect_org_usage(self, api_key: str) -> Optional[dict]:
        now = datetime.now(timezone.utc)
        weekly_start = int((now - timedelta(days=7)).timestamp())
        hourly_start = int((now - timedelta(hours=1)).timestamp())
        end = int(now.timestamp())

        async with httpx.AsyncClient(timeout=30.0) as client:
            weekly_resp = await client.get(
                "https://api.openai.com/v1/organization/usage/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                params={"start_time": weekly_start, "end_time": end, "bucket_width": "1d"},
            )
            hourly_resp = await client.get(
                "https://api.openai.com/v1/organization/usage/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                params={"start_time": hourly_start, "end_time": end, "bucket_width": "1h"},
            )

        if weekly_resp.status_code in {401, 403} or hourly_resp.status_code in {401, 403}:
            return {"status": "error", "auth_failed": True}
        if weekly_resp.status_code == 429 or hourly_resp.status_code == 429:
            return {"status": "cooldown", "rate_limited": True}
        if weekly_resp.status_code != 200 or hourly_resp.status_code != 200:
            return {"status": "error"}

        weekly_total, weekly_requests, weekly_found = self._aggregate_usage_payload(weekly_resp.json())
        hourly_total, _, hourly_found = self._aggregate_usage_payload(hourly_resp.json())
        if not weekly_found and not hourly_found:
            return {"status": "error"}
        return {
            "status": "ready",
            "usage_source": "organization_usage",
            "usage_weekly": weekly_total,
            "usage_hourly": hourly_total,
            "usage_tpm": hourly_total,
            "usage_rpd": weekly_requests,
        }

    async def _collect_wham_usage(self, access_token: str, settings: Any) -> Optional[dict]:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": "OpenAI-Codex-CLI/0.116.0",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        configured_endpoints = getattr(settings, "codex_usage_endpoints", "")
        endpoints = tuple(url.strip() for url in str(configured_endpoints).split(",") if url.strip()) or (
            "https://chatgpt.com/backend-api/wham/usage",
            "https://api.openai.com/dashboard/codex/usage",
        )
        
        async with httpx.AsyncClient(timeout=10.0) as client:
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

    def _resolve_api_key(self, credential: str) -> Optional[str]:
        stripped = credential.strip()
        if stripped.startswith("sk-"):
            return stripped
        payload = self._try_parse_json(stripped)
        if payload is None:
            return None
        return self._deep_find_token(payload, {"OPENAI_API_KEY", "api_key", "apiKey", "key"})

    def _resolve_access_token(self, credential: str) -> Optional[str]:
        stripped = credential.strip()
        if stripped and not (stripped.startswith("{") or stripped.startswith("[")) and " " not in stripped and "\n" not in stripped:
            return stripped
        payload = self._try_parse_json(stripped)
        if payload is None:
            return None
        return self._deep_find_token(payload, {"access_token", "accessToken", "bearer_token", "bearerToken", "token", "id_token"})

    def _messages_to_prompt(self, messages: List[Message]) -> str:
        parts = []
        for message in messages:
            role = getattr(message, "role", "user")
            content = getattr(message, "content", "")
            parts.append(f"{role.upper()}:\n{content}")
        return "\n\n".join(parts)

    def _parse_exec_output(self, stdout: str, output_path: Path, current_backend_id: str) -> tuple[str, Optional[int], str]:
        backend_id = current_backend_id
        context_tokens: Optional[int] = None
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
            elif event.get("type") == "turn.completed":
                usage = event.get("usage") or {}
                input_tokens = usage.get("input_tokens")
                output_tokens = usage.get("output_tokens")
                if isinstance(input_tokens, int) and isinstance(output_tokens, int):
                    context_tokens = input_tokens + output_tokens
            elif event.get("type") == "item.completed":
                item = event.get("item") or {}
                if item.get("type") == "agent_message":
                    fallback_output = item.get("text", fallback_output)

        output_text = output_path.read_text(encoding="utf-8").strip() if output_path.exists() else fallback_output
        if not output_text:
            output_text = fallback_output
        if not output_text:
            raise RuntimeError("Codex exec did not return an assistant message")
        return backend_id, context_tokens, output_text

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

    def _aggregate_usage_payload(self, payload: dict) -> tuple[int, int, bool]:
        buckets = payload.get("data", [])
        total_tokens = 0
        total_requests = 0
        found = False

        # In OpenAI's usage API, buckets aggregate over time (e.g. daily/hourly).
        # We should avoid double-counting by preferring total_tokens if available.
        for bucket in buckets:
            if not isinstance(bucket, dict):
                continue
            
            # Prefer 'total_tokens' if available, otherwise sum components
            bucket_tokens = bucket.get("total_tokens") or (bucket.get("input_tokens", 0) + bucket.get("output_tokens", 0))
            if bucket_tokens:
                total_tokens += int(bucket_tokens)
                found = True
            
            bucket_reqs = bucket.get("request_count") or bucket.get("num_model_requests") or bucket.get("requests")
            if bucket_reqs:
                total_requests += int(bucket_reqs)
                found = True

        return total_tokens, total_requests, found
