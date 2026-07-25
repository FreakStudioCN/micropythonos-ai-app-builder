"""Database-backed username/password authentication for the closed beta."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    MetaData,
    String,
    Table,
    delete,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

from .database import create_database_engine, database_engine


COOKIE_NAME = "mpos_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30
PASSWORD_N = 2**14
PASSWORD_R = 8
PASSWORD_P = 1
PASSWORD_DKLEN = 32

metadata = MetaData()
users = Table(
    "app_users",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("username", String(32), nullable=False),
    Column("username_normalized", String(32), nullable=False, unique=True, index=True),
    Column("password_hash", String(256), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
login_sessions = Table(
    "app_login_sessions",
    metadata,
    Column("token_hash", String(64), primary_key=True),
    Column(
        "user_id",
        String(36),
        ForeignKey("app_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("last_seen_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False, index=True),
)


class InvalidCredentials(RuntimeError):
    pass


class UsernameTaken(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


class AuthService:
    def __init__(
        self,
        database_url: str | None = None,
        engine: Engine | None = None,
    ) -> None:
        self.engine = engine or (
            create_database_engine(database_url)
            if database_url
            else database_engine
        )
        metadata.create_all(self.engine)

    def register(self, username: str, password: str) -> tuple[dict[str, Any], str]:
        display_name, normalized = self._validate_username(username)
        self._validate_password(password)
        user_id = str(uuid.uuid4())
        created_at = _now()
        password_hash = self._hash_password(password)
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    insert(users).values(
                        id=user_id,
                        username=display_name,
                        username_normalized=normalized,
                        password_hash=password_hash,
                        created_at=created_at,
                    )
                )
                token = self._create_login_session(connection, user_id, created_at)
        except IntegrityError as exc:
            raise UsernameTaken("用户名已存在") from exc
        return {
            "id": user_id,
            "username": display_name,
            "created_at": created_at.isoformat(),
        }, token

    def login(self, username: str, password: str) -> tuple[dict[str, Any], str]:
        _, normalized = self._validate_username(username)
        self._validate_password(password)
        with self.engine.begin() as connection:
            row = connection.execute(
                select(users).where(users.c.username_normalized == normalized)
            ).mappings().first()
            if row is None or not self._verify_password(password, row["password_hash"]):
                raise InvalidCredentials("用户名或密码错误")
            now = _now()
            connection.execute(
                delete(login_sessions).where(login_sessions.c.expires_at <= now)
            )
            token = self._create_login_session(connection, row["id"], now)
            return self._public_user(row), token

    def authenticate(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        token_hash = self._token_hash(token)
        now = _now()
        with self.engine.begin() as connection:
            row = connection.execute(
                select(
                    users.c.id,
                    users.c.username,
                    users.c.created_at,
                    login_sessions.c.expires_at,
                )
                .select_from(
                    login_sessions.join(users, login_sessions.c.user_id == users.c.id)
                )
                .where(login_sessions.c.token_hash == token_hash)
                .where(login_sessions.c.expires_at > now)
            ).mappings().first()
            if row is None:
                return None
            connection.execute(
                update(login_sessions)
                .where(login_sessions.c.token_hash == token_hash)
                .values(last_seen_at=now)
            )
            return self._public_user(row)

    def logout(self, token: str | None) -> None:
        if not token:
            return
        with self.engine.begin() as connection:
            connection.execute(
                delete(login_sessions).where(
                    login_sessions.c.token_hash == self._token_hash(token)
                )
            )

    @staticmethod
    def _validate_username(username: str) -> tuple[str, str]:
        display_name = username.strip()
        if not 3 <= len(display_name) <= 32:
            raise ValueError("用户名长度必须为 3 到 32 个字符")
        if not all(character.isalnum() or character in {"_", "-"} for character in display_name):
            raise ValueError("用户名只能包含文字、数字、下划线和连字符")
        return display_name, display_name.casefold()

    @staticmethod
    def _validate_password(password: str) -> None:
        if not 8 <= len(password) <= 128:
            raise ValueError("密码长度必须为 8 到 128 个字符")

    @staticmethod
    def _hash_password(password: str) -> str:
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=PASSWORD_N,
            r=PASSWORD_R,
            p=PASSWORD_P,
            dklen=PASSWORD_DKLEN,
        )
        return f"scrypt${PASSWORD_N}${PASSWORD_R}${PASSWORD_P}${salt.hex()}${digest.hex()}"

    @staticmethod
    def _verify_password(password: str, encoded: str) -> bool:
        try:
            algorithm, raw_n, raw_r, raw_p, raw_salt, raw_digest = encoded.split("$", 5)
            if algorithm != "scrypt":
                return False
            expected = bytes.fromhex(raw_digest)
            actual = hashlib.scrypt(
                password.encode("utf-8"),
                salt=bytes.fromhex(raw_salt),
                n=int(raw_n),
                r=int(raw_r),
                p=int(raw_p),
                dklen=len(expected),
            )
            return hmac.compare_digest(actual, expected)
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _create_login_session(
        self,
        connection: Connection,
        user_id: str,
        created_at: datetime,
    ) -> str:
        token = secrets.token_urlsafe(32)
        connection.execute(
            insert(login_sessions).values(
                token_hash=self._token_hash(token),
                user_id=user_id,
                created_at=created_at,
                last_seen_at=created_at,
                expires_at=created_at + timedelta(seconds=COOKIE_MAX_AGE),
            )
        )
        return token

    @staticmethod
    def _public_user(row: Any) -> dict[str, Any]:
        created_at = row["created_at"]
        return {
            "id": row["id"],
            "username": row["username"],
            "created_at": (
                created_at.isoformat()
                if hasattr(created_at, "isoformat")
                else str(created_at)
            ),
        }


auth_service = AuthService(engine=database_engine)
