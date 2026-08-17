"""Generation error types shared by the generator and its validators.

Split into their own module so contract validators can raise and catch these
without importing ``generator``, which would be circular.
"""

from __future__ import annotations

from typing import Any


class GenerationError(RuntimeError):
    pass


class UpstreamGenerationError(GenerationError):
    """A sanitized, structured error returned by an AI upstream."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        failover_allowed: bool,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable
        self.failover_allowed = failover_allowed
        self.details = details or {}


class ApiValidationError(GenerationError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
