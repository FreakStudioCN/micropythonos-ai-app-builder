"""Shared SQLAlchemy engine configuration for local and cloud persistence."""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def database_url() -> str:
    configured = (
        os.getenv("MPOS_AUTH_DATABASE_URL", "").strip()
        or os.getenv("DATABASE_URL", "").strip()
    )
    if configured:
        if configured.startswith("postgres://"):
            return configured.replace("postgres://", "postgresql+psycopg://", 1)
        if configured.startswith("postgresql://"):
            return configured.replace("postgresql://", "postgresql+psycopg://", 1)
        return configured

    project_root = Path(__file__).resolve().parents[2]
    database_path = Path(
        os.getenv(
            "MPOS_AUTH_DB_PATH",
            str(project_root / "backend" / "sessions" / "app.db"),
        )
    ).resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{database_path}"


def create_database_engine(url: str | None = None) -> Engine:
    resolved = url or database_url()
    connect_args = (
        {"check_same_thread": False}
        if resolved.startswith("sqlite:")
        else {}
    )
    return create_engine(
        resolved,
        connect_args=connect_args,
        pool_pre_ping=True,
    )


database_engine = create_database_engine()
