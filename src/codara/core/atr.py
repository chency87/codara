import json
import re
from typing import List, Dict, Any, Optional


class ATRModule:
    """Action Translation & Reconstruction Module"""

    def __init__(self):
        self.block_pattern = re.compile(
            r"<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>> REPLACE",
            re.DOTALL,
        )
        self.file_path_patterns = (
            re.compile(r"^###\s+(.+?)\s*$", re.MULTILINE),
            re.compile(r"^File:\s+(.+?)\s*$", re.MULTILINE),
            re.compile(r"^Path:\s+(.+?)\s*$", re.MULTILINE),
        )
        self.json_block_pattern = re.compile(r"```json\s*(.*?)```", re.DOTALL | re.IGNORECASE)
        self.diff_block_pattern = re.compile(r"```diff\s*(.*?)```", re.DOTALL | re.IGNORECASE)
        self.diff_header_pattern = re.compile(r"^diff --git a/(.+?) b/(.+?)$", re.MULTILINE)
        self.file_diff_pattern = re.compile(r"^\+\+\+\s+b/(.+)$", re.MULTILINE)

    def extract_actions(self, output: str) -> List[Dict[str, Any]]:
        """Extract actionable payloads from assistant output."""
        actions: List[Dict[str, Any]] = []
        actions.extend(self._extract_json_actions(output))
        actions.extend(self._extract_search_replace_actions(output))
        actions.extend(self._extract_diff_actions(output))
        for index, action in enumerate(actions, start=1):
            action.setdefault("action_id", f"atr_{index}")
        return actions

    def _extract_json_actions(self, output: str) -> List[Dict[str, Any]]:
        actions: List[Dict[str, Any]] = []
        for match in self.json_block_pattern.finditer(output):
            payload = self._parse_json_block(match.group(1))
            if payload is None:
                continue
            if isinstance(payload, dict) and isinstance(payload.get("actions"), list):
                items = [item for item in payload["actions"] if isinstance(item, dict)]
            elif isinstance(payload, dict):
                items = [payload]
            elif isinstance(payload, list):
                items = [item for item in payload if isinstance(item, dict)]
            else:
                items = []
            for item in items:
                normalized = self._normalize_json_action(item)
                if normalized:
                    actions.append(normalized)
        return actions

    def _extract_search_replace_actions(self, output: str) -> List[Dict[str, Any]]:
        actions: List[Dict[str, Any]] = []
        for match in self.block_pattern.finditer(output):
            path = self._infer_file_path(output[:match.start()])
            actions.append(
                {
                    "type": "patch",
                    "format": "search_replace",
                    "path": path,
                    "file": path,
                    "search": match.group(1),
                    "replace": match.group(2),
                    "raw": match.group(0),
                    "source": "text",
                    "exact": bool(path),
                }
            )
        return actions

    def _extract_diff_actions(self, output: str) -> List[Dict[str, Any]]:
        actions: List[Dict[str, Any]] = []
        for match in self.diff_block_pattern.finditer(output):
            diff_text = match.group(1).strip()
            if not diff_text:
                continue
            paths = self._extract_diff_paths(diff_text)
            actions.append(
                {
                    "type": "patch",
                    "format": "unified_diff",
                    "patch": diff_text,
                    "paths": paths,
                    "path": paths[0] if len(paths) == 1 else None,
                    "file": paths[0] if len(paths) == 1 else None,
                    "raw": diff_text,
                    "source": "diff",
                    "exact": bool(paths),
                }
            )
        return actions

    def _parse_json_block(self, raw: str) -> Optional[Any]:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def _normalize_json_action(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        action_type = item.get("type")
        if not isinstance(action_type, str) or not action_type.strip():
            return None
        path = self._normalize_path(
            item.get("path") or item.get("file") or item.get("file_path") or item.get("target_path")
        )
        normalized: Dict[str, Any] = {
            "type": action_type.strip(),
            "path": path,
            "file": path,
            "source": "json",
            "raw": item,
        }
        if isinstance(item.get("search"), str) and isinstance(item.get("replace"), str):
            normalized["format"] = "search_replace"
            normalized["search"] = item["search"]
            normalized["replace"] = item["replace"]
        elif isinstance(item.get("patch"), str):
            normalized["format"] = "unified_diff"
            normalized["patch"] = item["patch"]
        elif isinstance(item.get("diff"), str):
            normalized["format"] = "unified_diff"
            normalized["patch"] = item["diff"]
        elif isinstance(item.get("content"), str):
            normalized["format"] = "write_file"
            normalized["content"] = item["content"]
        elif isinstance(item.get("command"), str):
            normalized["format"] = "command"
            normalized["command"] = item["command"]
        normalized["exact"] = bool(path or normalized.get("patch") or normalized.get("command"))
        return normalized

    def _infer_file_path(self, prefix: str) -> Optional[str]:
        candidates: list[str] = []
        for pattern in self.file_path_patterns:
            candidates.extend(pattern.findall(prefix))
        if not candidates:
            return None
        return self._normalize_path(candidates[-1])

    def _normalize_path(self, value: Any) -> Optional[str]:
        if not isinstance(value, str):
            return None
        stripped = value.strip().strip("`").strip()
        return stripped or None

    def _extract_diff_paths(self, diff_text: str) -> List[str]:
        paths = [match[1].strip() for match in self.diff_header_pattern.findall(diff_text) if match[1].strip()]
        paths.extend(match.strip() for match in self.file_diff_pattern.findall(diff_text) if match.strip())
        deduped: list[str] = []
        seen: set[str] = set()
        for path in paths:
            if path not in seen:
                deduped.append(path)
                seen.add(path)
        return deduped

    def verify_actions(self, actions: List[Dict[str, Any]], workspace_root: str) -> bool:
        """Dry-run verification of actions against the file system."""
        for action in actions:
            if action.get("type") == "patch" and action.get("format") == "search_replace":
                if not action.get("path") or not isinstance(action.get("search"), str) or not isinstance(action.get("replace"), str):
                    return False
        return True
