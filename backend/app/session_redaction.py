"""Redaction of host-only and secret-like values in user-facing exports.

Its own module so both the session service and the publish mixin can use it
without importing each other.
"""

from __future__ import annotations

import re
from typing import Any


def _redact_text(value: str) -> str:
    """Remove host-only and secret-like values from user-facing exports."""
    value = re.sub(
        r"(?i)\b(?:sk|api)[-_][a-z0-9_-]{12,}\b",
        "[REDACTED_TOKEN]",
        value,
    )
    value = re.sub(
        r"(?i)(authorization\s*:\s*bearer\s+)[^\s\"']+",
        r"\1[REDACTED_TOKEN]",
        value,
    )
    value = re.sub(
        r"(?i)\b(?:COM\d+|/dev/(?:tty|cu)\S+)\b",
        "[REDACTED_SERIAL]",
        value,
    )
    value = re.sub(
        r"(?i)(?:[a-z]:\\(?:[^\\\r\n]+\\)+|/(?:home|users|var|tmp)/)"
        r"[^\s\"']*",
        "[REDACTED_PATH]",
        value,
    )
    return value


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if any(
                    marker in key.lower()
                    for marker in ("token", "api_key", "secret", "serial_port")
                )
                else _redact_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value
