from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


class AuditLogStore:
    def __init__(self, root: str):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._current_path: Optional[Path] = None
        self._stream = None

    def _path_for_timestamp(self, timestamp: int) -> Path:
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        return self.root / f"{dt.year:04d}" / f"{dt.month:02d}" / f"{dt.day:02d}" / f"{dt.hour:02d}.jsonl"

    def append(self, event: dict[str, Any]) -> None:
        path = self._path_for_timestamp(event.get("timestamp", 0) or int(datetime.now(tz=timezone.utc).timestamp()))
        
        if self._current_path != path:
            if self._stream:
                self._stream.close()
            path.parent.mkdir(parents=True, exist_ok=True)
            self._stream = path.open("a", encoding="utf-8")
            self._current_path = path
        
        self._stream.write(json.dumps(event, ensure_ascii=True) + "\n")
        self._stream.flush()

    def list_events(
        self,
        *,
        limit: int = 50,
        after: Optional[int] = None,
        actor: Optional[str] = None,
        action: Optional[str] = None,
        target_type: Optional[str] = None,
        search: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        after_value = after

        for path in sorted(self.root.glob("**/*.jsonl"), reverse=True):
            for line in reversed(self._read_lines(path)):
                event = self._parse_line(line)
                if event is None:
                    continue
                ts = event.get("timestamp") or 0
                if after_value and ts >= after_value:
                    continue
                if actor and actor not in event.get("actor", ""):
                    continue
                if action and action not in event.get("action", ""):
                    continue
                if target_type and target_type != event.get("target_type"):
                    continue
                if search:
                    haystack = json.dumps(event, ensure_ascii=True, sort_keys=True)
                    if search.lower() not in haystack.lower():
                        continue
                rows.append(event)
                if len(rows) >= limit:
                    return rows
        return rows

    def close(self) -> None:
        if self._stream:
            self._stream.close()
            self._stream = None
            self._current_path = None

    def _read_lines(self, path: Path) -> list[str]:
        if not path.exists():
            return []
        return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _parse_line(self, line: str) -> Optional[dict[str, Any]]:
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None