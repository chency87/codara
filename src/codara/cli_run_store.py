from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from codara.config import Settings, get_settings


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class CliRunPaths:
    run_id: str
    run_dir: Path
    meta_path: Path
    prompt_path: Path
    stdout_path: Path
    stderr_path: Path


class CliRunStore:
    def __init__(self, root: Optional[Path] = None, *, settings: Optional[Settings] = None) -> None:
        settings = settings or get_settings()
        logs_root = Path(settings.logs_root).expanduser().resolve()
        self.root = (root or (logs_root / settings.cli_capture_root)).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def allocate_run(self, *, provider: str, session_id: str) -> CliRunPaths:
        safe_session_id = (
            str(session_id or "")
            .strip()
            .replace("/", "__")
            .replace("\\", "__")
        ) or "unknown-session"
        # Use a time-prefixed id so lexicographic sort roughly matches recency.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        run_id = f"run_{stamp}_{safe_session_id.replace(':', '_')}"
        # Ensure uniqueness if two runs start in the same second.
        suffix = 0
        while True:
            effective = run_id if suffix == 0 else f"{run_id}_{suffix}"
            run_dir = self.root / provider / safe_session_id / effective
            if not run_dir.exists():
                break
            suffix += 1

        run_dir.mkdir(parents=True, exist_ok=True)
        return CliRunPaths(
            run_id=run_dir.name,
            run_dir=run_dir,
            meta_path=run_dir / "meta.json",
            prompt_path=run_dir / "prompt.txt",
            stdout_path=run_dir / "stdout.log",
            stderr_path=run_dir / "stderr.log",
        )

    def write_meta(self, meta_path: Path, payload: dict[str, Any]) -> None:
        meta_path.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    def write_prompt(self, prompt_path: Path, prompt: str) -> None:
        prompt_path.write_text(prompt, encoding="utf-8")

    def start_run(
        self,
        *,
        provider: str,
        session_id: str,
        cwd: str,
        command: list[str],
        provider_model: Optional[str] = None,
        attempt: Optional[str] = None,
        trace_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> CliRunPaths:
        paths = self.allocate_run(provider=provider, session_id=session_id)
        meta = {
            "run_id": paths.run_id,
            "provider": provider,
            "session_id": session_id,
            "cwd": cwd,
            "command": command,
            "provider_model": provider_model,
            "attempt": attempt,
            "status": "running",
            "started_at": _now_iso(),
            "ended_at": None,
            "exit_code": None,
            "trace_id": trace_id,
            "request_id": request_id,
            "error": None,
        }
        self.write_meta(paths.meta_path, meta)
        return paths

    def end_run(
        self,
        paths: CliRunPaths,
        *,
        status: str,
        exit_code: Optional[int],
        error: Optional[str] = None,
    ) -> None:
        existing = self.read_meta(paths.meta_path) or {}
        existing.update(
            {
                "status": status,
                "ended_at": _now_iso(),
                "exit_code": exit_code,
                "error": error,
            }
        )
        self.write_meta(paths.meta_path, existing)

    def read_meta(self, meta_path: Path) -> Optional[dict[str, Any]]:
        if not meta_path.exists():
            return None
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def list_runs(
        self,
        *,
        session_id: Optional[str] = None,
        provider: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        if session_id:
            return self._list_runs_for_session(session_id, provider, status, limit)
        
        rows: list[dict[str, Any]] = []
        for provider_dir in self.root.iterdir():
            if not provider_dir.is_dir():
                continue
            if provider and provider_dir.name != provider:
                continue
            for session_dir in provider_dir.iterdir():
                if not session_dir.is_dir():
                    continue
                for run_dir in sorted([p for p in session_dir.iterdir() if p.is_dir()], reverse=True):
                    meta = self.read_meta(run_dir / "meta.json")
                    if meta is None:
                        continue
                    if status and str(meta.get("status") or "") != status:
                        continue
                    meta = dict(meta)
                    meta["provider"] = provider_dir.name
                    meta["run_id"] = meta.get("run_id") or run_dir.name
                    rows.append(meta)
                    if len(rows) >= limit:
                        return rows
        return rows

    def _list_runs_for_session(
        self,
        session_id: str,
        provider: Optional[str],
        status: Optional[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        safe_session_id = (
            str(session_id or "")
            .strip()
            .replace("/", "__")
            .replace("\\", "__")
        ) or "unknown-session"
        providers: list[Path]
        if provider:
            providers = [self.root / provider]
        else:
            providers = [p for p in self.root.iterdir() if p.is_dir()]

        rows: list[dict[str, Any]] = []
        for provider_dir in providers:
            session_dir = provider_dir / safe_session_id
            if not session_dir.exists():
                continue
            for run_dir in sorted([p for p in session_dir.iterdir() if p.is_dir()], reverse=True):
                meta = self.read_meta(run_dir / "meta.json")
                if meta is None:
                    continue
                if status and str(meta.get("status") or "") != status:
                    continue
                meta = dict(meta)
                meta["provider"] = meta.get("provider") or provider_dir.name
                meta["run_id"] = meta.get("run_id") or run_dir.name
                rows.append(meta)
                if len(rows) >= limit:
                    return rows
        return rows

    def stdout_path(self, *, provider: str, session_id: str, run_id: str) -> Path:
        safe_session_id = (
            str(session_id or "")
            .strip()
            .replace("/", "__")
            .replace("\\", "__")
        ) or "unknown-session"
        return self.root / provider / safe_session_id / run_id / "stdout.log"

    def stderr_path(self, *, provider: str, session_id: str, run_id: str) -> Path:
        safe_session_id = (
            str(session_id or "")
            .strip()
            .replace("/", "__")
            .replace("\\", "__")
        ) or "unknown-session"
        return self.root / provider / safe_session_id / run_id / "stderr.log"

    def meta_path(self, *, provider: str, session_id: str, run_id: str) -> Path:
        safe_session_id = (
            str(session_id or "")
            .strip()
            .replace("/", "__")
            .replace("\\", "__")
        ) or "unknown-session"
        return self.root / provider / safe_session_id / run_id / "meta.json"

    def prompt_path(self, *, provider: str, session_id: str, run_id: str) -> Path:
        safe_session_id = (
            str(session_id or "")
            .strip()
            .replace("/", "__")
            .replace("\\", "__")
        ) or "unknown-session"
        return self.root / provider / safe_session_id / run_id / "prompt.txt"
