"""Timestamp and JSON state-file primitives shared by the session modules.

Their own module so the session mixins can use them without importing
``session_service``, which would be circular.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.encoders import jsonable_encoder


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json_dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(jsonable_encoder(value), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _json_load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
