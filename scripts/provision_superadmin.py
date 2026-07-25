#!/usr/bin/env python3
"""Provision a superadmin without exposing credentials on the command line."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
LOCAL_DATABASE_PATH = BACKEND_ROOT / "sessions" / "app.db"
PASSWORD_ENV = "MPOS_SUPERADMIN_PASSWORD"


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create or explicitly promote a local/production superadmin.",
    )
    parser.add_argument(
        "--target",
        choices=("local", "production"),
        required=True,
        help="Use the project SQLite database or production DATABASE_URL.",
    )
    parser.add_argument("--username", required=True, help="Account username.")
    parser.add_argument(
        "--promote-existing",
        action="store_true",
        help="Explicitly allow promotion when the username already belongs to a regular user.",
    )
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help="Explicitly reset the existing account password and revoke its login sessions.",
    )
    return parser.parse_args(argv)


def _target_database_url(target: str) -> str:
    if target == "local":
        LOCAL_DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{LOCAL_DATABASE_PATH}"

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("Production provisioning requires DATABASE_URL.")
    if not database_url.startswith(("postgres://", "postgresql://", "postgresql+")):
        raise RuntimeError("Production DATABASE_URL must use PostgreSQL.")
    return database_url


def _read_password() -> str:
    password = os.environ.pop(PASSWORD_ENV, None)
    if password is not None:
        if not password:
            raise RuntimeError(f"{PASSWORD_ENV} must not be empty.")
        return password

    password = getpass.getpass("Superadmin password: ")
    confirmation = getpass.getpass("Confirm superadmin password: ")
    if password != confirmation:
        raise RuntimeError("Password confirmation does not match.")
    if not password:
        raise RuntimeError("Password must not be empty.")
    return password


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        database_url = _target_database_url(args.target)
        password = _read_password()

        # Set the target before importing app.auth, whose module-level service
        # initializes an engine during import.
        os.environ["MPOS_AUTH_DATABASE_URL"] = database_url
        sys.path.insert(0, str(BACKEND_ROOT))
        from app.auth import AuthService

        service = AuthService(database_url=database_url)
        service.provision_superadmin(
            args.username,
            password,
            promote_existing=args.promote_existing,
            reset_password=args.reset_password,
        )
    except (EOFError, KeyboardInterrupt):
        print(
            f"status=cancelled target={args.target} username={args.username}",
            file=sys.stderr,
        )
        return 130
    except Exception:
        print(
            f"status=failed target={args.target} username={args.username}; "
            "check target configuration and explicit promotion/reset flags",
            file=sys.stderr,
        )
        return 1

    print(f"status=success target={args.target} username={args.username}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
