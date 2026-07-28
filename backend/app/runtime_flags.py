"""Environment flag parsing and maintenance-mode gating."""

from __future__ import annotations

import os
from typing import Any


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _system_status_payload() -> dict[str, Any]:
    maintenance = _enabled("MAINTENANCE_MODE")
    try:
        retry_after = int(
            os.getenv("MAINTENANCE_RETRY_AFTER_SECONDS", "300").strip()
        )
    except ValueError:
        retry_after = 300
    retry_after = max(1, min(86400, retry_after))
    message = os.getenv(
        "MAINTENANCE_MESSAGE",
        "系统正在升级，请稍后重试。",
    ).strip()
    return {
        "status": "maintenance" if maintenance else "ready",
        "maintenance_mode": maintenance,
        "message": message if maintenance else "",
        "retry_after_seconds": retry_after,
    }


def _maintenance_blocks(method: str, path: str) -> bool:
    if not _enabled("MAINTENANCE_MODE") or method not in {
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
    }:
        return False
    return (
        path in {"/api/generate", "/api/requirements/chat"}
        or "/actions/" in path
        or path.endswith(("/retry", "/resume"))
        or "/devices/" in path
    )
