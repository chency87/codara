from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


class FileTraceStore:
    def __init__(self, root: str):
        self.root = Path(root).expanduser().resolve()
        self.events_root = self.root / "events"
        self.index_root = self.root / "index"
        self.events_root.mkdir(parents=True, exist_ok=True)
        self.index_root.mkdir(parents=True, exist_ok=True)

    def append_batch(self, batch: Iterable[dict[str, Any]]) -> None:
        grouped_events: dict[Path, list[str]] = {}
        grouped_roots: dict[Path, list[str]] = {}
        for row in batch:
            started_at = int(row["started_at"])
            shard_key = self._shard_key(started_at)
            event_path = self.events_root / shard_key["event_relpath"]
            grouped_events.setdefault(event_path, []).append(json.dumps(row, ensure_ascii=True, sort_keys=True))
            if row.get("kind") == "span" and not row.get("parent_span_id"):
                index_row = {
                    "trace_id": row.get("trace_id"),
                    "span_id": row.get("span_id"),
                    "name": row.get("name"),
                    "component": row.get("component"),
                    "level": row.get("level"),
                    "status": row.get("status"),
                    "request_id": row.get("request_id"),
                    "started_at": row.get("started_at"),
                    "ended_at": row.get("ended_at"),
                    "duration_ms": row.get("duration_ms"),
                    "attributes": row.get("attributes"),
                    "event_id": row.get("event_id"),
                    "shard_hour": shard_key["hour_key"],
                }
                index_path = self.index_root / shard_key["index_relpath"]
                grouped_roots.setdefault(index_path, []).append(json.dumps(index_row, ensure_ascii=True, sort_keys=True))

        for path, lines in grouped_events.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")

        for path, lines in grouped_roots.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")

    def list_traces(
        self,
        *,
        limit: int = 50,
        after: Optional[int] = None,
        component: Optional[str] = None,
        request_id: Optional[str] = None,
        status: Optional[str] = None,
        trace_id: Optional[str] = None,
        search: Optional[str] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        search_text = (search or "").strip().lower()
        for path in self._iter_index_files():
            for line in reversed(self._read_lines(path)):
                row = self._parse_line(line)
                if row is None:
                    continue
                started_at = int(row.get("started_at") or 0)
                if after is not None and started_at >= after:
                    continue
                if since is not None and started_at < since:
                    continue
                if until is not None and started_at > until:
                    continue
                if component and row.get("component") != component:
                    continue
                if request_id and row.get("request_id") != request_id:
                    continue
                if status and row.get("status") != status:
                    continue
                if trace_id and row.get("trace_id") != trace_id:
                    continue
                if search_text:
                    haystack = json.dumps(row, ensure_ascii=True, sort_keys=True).lower()
                    if search_text not in haystack:
                        continue
                rows.append(row)
                if len(rows) >= limit:
                    return rows
        return rows

    def prune_older_than(self, cutoff_ms: int) -> dict[str, int]:
        event_result = self._prune_jsonl_tree(self.events_root, cutoff_ms)
        index_result = self._prune_jsonl_tree(self.index_root, cutoff_ms)
        return {
            "files_deleted": event_result["files_deleted"] + index_result["files_deleted"],
            "files_rewritten": event_result["files_rewritten"] + index_result["files_rewritten"],
            "records_deleted": event_result["records_deleted"] + index_result["records_deleted"],
        }

    def get_trace_events(self, trace_id: str) -> list[dict[str, Any]]:
        trace_root = self._find_trace_root(trace_id)
        if not trace_root:
            return []
        shard_hour = trace_root.get("shard_hour")
        if not shard_hour:
            return []
        event_path = self.events_root / shard_hour[:4] / shard_hour[5:7] / shard_hour[8:10] / f"{shard_hour[11:13]}.jsonl"
        rows: list[dict[str, Any]] = []
        for line in self._read_lines(event_path):
            row = self._parse_line(line)
            if row is None or row.get("trace_id") != trace_id:
                continue
            rows.append(row)
        rows.sort(key=lambda item: (int(item.get("started_at") or 0), str(item.get("event_id") or "")))
        return rows

    def _find_trace_root(self, trace_id: str) -> Optional[dict[str, Any]]:
        for path in self._iter_index_files():
            for line in reversed(self._read_lines(path)):
                row = self._parse_line(line)
                if row and row.get("trace_id") == trace_id:
                    return row
        return None

    def _iter_index_files(self):
        if not self.index_root.exists():
            return []
        return sorted(self.index_root.glob("**/root-spans.jsonl"), reverse=True)

    def _read_lines(self, path: Path) -> list[str]:
        if not path.exists():
            return []
        return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _parse_line(self, line: str) -> Optional[dict[str, Any]]:
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None

    def _prune_jsonl_tree(self, root: Path, cutoff_ms: int) -> dict[str, int]:
        files_deleted = 0
        files_rewritten = 0
        records_deleted = 0
        for path in sorted(root.glob("**/*.jsonl")):
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
                started_at = int(row.get("started_at") or 0)
                if started_at >= cutoff_ms:
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
        self._remove_empty_dirs(root)
        return {
            "files_deleted": files_deleted,
            "files_rewritten": files_rewritten,
            "records_deleted": records_deleted,
        }

    def _remove_empty_dirs(self, root: Path) -> None:
        for path in sorted(root.glob("**/*"), reverse=True):
            if path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass

    def _shard_key(self, started_at_ms: int) -> dict[str, str]:
        dt = datetime.fromtimestamp(started_at_ms / 1000, tz=timezone.utc)
        year = f"{dt.year:04d}"
        month = f"{dt.month:02d}"
        day = f"{dt.day:02d}"
        hour = f"{dt.hour:02d}"
        return {
            "hour_key": f"{year}/{month}/{day}/{hour}",
            "event_relpath": f"{year}/{month}/{day}/{hour}.jsonl",
            "index_relpath": f"{year}/{month}/{day}/root-spans.jsonl",
        }
