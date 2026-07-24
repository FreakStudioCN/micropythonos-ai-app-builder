"""In-process indexes for persisted sessions and append-only event logs."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class _LogCache:
    offset: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)
    partial: bytes = b""


class EventLogCache:
    """Parse only bytes appended since the previous JSONL read."""

    def __init__(self) -> None:
        self._cache: dict[str, _LogCache] = {}
        self._lock = threading.Lock()

    def read(self, session_id: str, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            self.invalidate(session_id)
            return []
        with self._lock:
            size = path.stat().st_size
            entry = self._cache.get(session_id)
            if entry is None or size < entry.offset:
                entry = _LogCache()
                self._cache[session_id] = entry
            if size == entry.offset:
                return list(entry.events)
            with path.open("rb") as handle:
                handle.seek(entry.offset)
                chunk = handle.read(size - entry.offset)
            data = entry.partial + chunk
            lines = data.split(b"\n")
            entry.partial = lines.pop()
            for raw in lines:
                if not raw.strip():
                    continue
                try:
                    entry.events.append(json.loads(raw.decode("utf-8")))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
            entry.offset = size
            return list(entry.events)

    def next_seq(self, session_id: str, path: Path) -> int:
        return len(self.read(session_id, path)) + 1

    def append(
        self, session_id: str, path: Path, event: dict[str, Any]
    ) -> None:
        line = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as handle:
            handle.write(line)
        with self._lock:
            entry = self._cache.get(session_id)
            if entry is not None and not entry.partial:
                entry.events.append(event)
                entry.offset += len(line)
            else:
                self._cache.pop(session_id, None)

    def invalidate(self, session_id: str) -> None:
        with self._lock:
            self._cache.pop(session_id, None)


class SessionIndex:
    """Map artifact and permission IDs to their owning session."""

    def __init__(self, session_root: Path) -> None:
        self.session_root = session_root
        self._artifacts: dict[str, str] = {}
        self._permissions: dict[str, str] = {}
        self._lock = threading.Lock()

    def register_state(self, state: dict[str, Any]) -> None:
        session_id = state.get("session_id")
        if not session_id:
            return
        with self._lock:
            for artifact in state.get("artifacts", []) or []:
                artifact_id = artifact.get("id")
                if artifact_id:
                    self._artifacts[artifact_id] = session_id
            for permission in state.get("permissions", []) or []:
                permission_id = permission.get("permission_id")
                if permission_id:
                    self._permissions[permission_id] = session_id

    def _rebuild(self) -> None:
        for path in self.session_root.glob("sess_*/session_state.json"):
            try:
                self.register_state(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue

    def session_for_artifact(self, artifact_id: str) -> str | None:
        with self._lock:
            found = self._artifacts.get(artifact_id)
        if found:
            return found
        self._rebuild()
        with self._lock:
            return self._artifacts.get(artifact_id)

    def session_for_permission(self, permission_id: str) -> str | None:
        with self._lock:
            found = self._permissions.get(permission_id)
        if found:
            return found
        self._rebuild()
        with self._lock:
            return self._permissions.get(permission_id)

    def forget(self, session_id: str) -> None:
        with self._lock:
            self._artifacts = {
                key: value
                for key, value in self._artifacts.items()
                if value != session_id
            }
            self._permissions = {
                key: value
                for key, value in self._permissions.items()
                if value != session_id
            }
