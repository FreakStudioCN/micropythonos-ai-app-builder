"""CORS origin allowlist computation for the API server."""

from __future__ import annotations

import os


def compute_frontend_origins() -> list[str]:
    """Return the CORS allowlist for the FastAPI app.

    Dev localhost origins are on by default; same-origin production deployments
    set MPOS_ALLOW_DEV_ORIGINS=false so localhost pages cannot make credentialed
    cross-origin requests. Additional origins come from FRONTEND_ORIGINS (or the
    legacy FRONTEND_ORIGIN), comma-separated.
    """
    allow_dev = os.getenv("MPOS_ALLOW_DEV_ORIGINS", "true").strip().lower() in {"1", "true", "yes", "on"}
    local_frontend_origins = [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
    ] if allow_dev else []
    configured_frontend_origins = os.getenv(
        "FRONTEND_ORIGINS",
        os.getenv("FRONTEND_ORIGIN", ""),
    )
    return list(dict.fromkeys(
        local_frontend_origins
        + [origin.strip() for origin in configured_frontend_origins.split(",") if origin.strip()]
    ))
