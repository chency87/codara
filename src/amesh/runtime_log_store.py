from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


class RuntimeLogStore:
    def __init__(self, root: str):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def list_logs(
        self,
        *,
        limit: int = 50,
        after: Optional[str] = None,
        level: Optional[str] = None,
        component: Optional[str] = None,
        trace_id: Optional[str] = None,
        request_id: Optional[str] = None,
        search: Optional[str] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        after_value = after or None
        for path in sorted(self.root.glob("**/*.jsonl"), reverse=True):
            for line in reversed(self._read_lines(path)):
                row = self._parse_line(line)
                if row is None:
                    continue
                timestamp = str(row.get("timestamp") or "")
                if after_value and timestamp >= after_value:
                    continue
                timestamp_ms = self._timestamp_ms(timestamp)
                if since is not None and timestamp_ms is not None and timestamp_ms < since:
                    continue
                if until is not None and timestamp_ms is not None and timestamp_ms > until:
                    continue
                if level and str(row.get("level") or "").upper() != level.upper():
                    continue
                if component and row.get("component") != component:
                    continue
                if trace_id and row.get("trace_id") != trace_id:
                    continue
                if request_id and row.get("request_id") != request_id:
                    continue
                if search:
                    haystack = json.dumps(row, ensure_ascii=True, sort_keys=True)
                    if search.lower() not in haystack.lower():
                        continue
                rows.append(row)
                if len(rows) >= limit:
                    return rows
        return rows

    def prune_older_than(self, cutoff_ms: int) -> dict[str, int]:
        files_deleted = 0
        records_deleted = 0
        files_rewritten = 0
        for path in sorted(self.root.glob("**/*.jsonl")):
            lines = self._read_lines(path)
            if not lines:
                path.unlink(missing_ok=True)
                files_deleted += 1
                continue
            kept: list[str] = []
            deleted = 0
            for line in lines:
                row = self._parse_line(line)
                if row is None:
                    kept.append(line)
                    continue
                timestamp_ms = self._timestamp_ms(str(row.get("timestamp") or ""))
                if timestamp_ms is None or timestamp_ms >= cutoff_ms:
                    kept.append(line)
                else:
                    deleted += 1
            if deleted == 0:
                continue
            records_deleted += deleted
            if kept:
                path.write_text("\n".join(kept) + "\n", encoding="utf-8")
                files_rewritten += 1
            else:
                path.unlink(missing_ok=True)
                files_deleted += 1
        self._remove_empty_dirs()
        return {
            "files_deleted": files_deleted,
            "files_rewritten": files_rewritten,
            "records_deleted": records_deleted,
        }

    def _read_lines(self, path: Path) -> list[str]:
        if not path.exists():
            return []
        return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _parse_line(self, line: str) -> Optional[dict[str, Any]]:
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None

    def _timestamp_ms(self, value: str) -> Optional[int]:
        if not value:
            return None
        try:
            if value.endswith("Z"):
                value = f"{value[:-1]}+00:00"
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return int(parsed.timestamp() * 1000)
        except ValueError:
            return None

    def _remove_empty_dirs(self) -> None:
        for path in sorted(self.root.glob("**/*"), reverse=True):
            if path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass
