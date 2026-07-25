"""Database-backed free beta credits and generation ledger."""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Connection, Engine

from .database import create_database_engine, database_engine


INITIAL_CREDITS = 50
GENERATION_COST = 10
GENERATION_LIMIT = INITIAL_CREDITS // GENERATION_COST
metadata = MetaData()
billing_accounts = Table(
    "app_billing_accounts",
    metadata,
    Column("user_id", String(36), primary_key=True),
    Column("credits", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
billing_ledger = Table(
    "app_billing_ledger",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), nullable=False, index=True),
    Column("idempotency_key", String(200), nullable=False),
    Column("entry_type", String(32), nullable=False),
    Column("amount", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("user_id", "idempotency_key", name="uq_billing_user_idempotency"),
)


def _now() -> datetime:
    return datetime.now(UTC)


class InsufficientCredits(RuntimeError):
    def __init__(self, balance: int, required: int) -> None:
        super().__init__("点数不足，免费内测额度已用完")
        self.balance = balance
        self.required = required


class BillingService:
    def __init__(
        self,
        root: Path | None = None,
        engine: Engine | None = None,
    ) -> None:
        if engine is not None:
            self.engine = engine
        elif root is not None:
            root.mkdir(parents=True, exist_ok=True)
            self.engine = create_database_engine(f"sqlite:///{root / 'billing.db'}")
        else:
            self.engine = database_engine
        metadata.create_all(self.engine)
        self._lock = threading.RLock()

    def account(
        self,
        user_id: str,
        *,
        unlimited: bool = False,
    ) -> dict[str, Any]:
        with self._lock, self.engine.begin() as connection:
            return self._public(
                self._load_or_create(connection, user_id),
                unlimited=unlimited,
            )

    def consume_generation(
        self,
        user_id: str,
        idempotency_key: str,
        *,
        unlimited: bool = False,
    ) -> dict[str, Any]:
        with self._lock, self.engine.begin() as connection:
            account = self._load_or_create(connection, user_id, for_update=True)
            if unlimited:
                return self._public(account, unlimited=True)
            if self._has_transaction(connection, user_id, idempotency_key):
                return self._public(account, unlimited=False)
            if account["credits"] < GENERATION_COST:
                raise InsufficientCredits(account["credits"], GENERATION_COST)
            updated_at = _now()
            next_credits = account["credits"] - GENERATION_COST
            connection.execute(
                update(billing_accounts)
                .where(billing_accounts.c.user_id == user_id)
                .values(credits=next_credits, updated_at=updated_at)
            )
            self._append_ledger(
                connection,
                user_id=user_id,
                idempotency_key=idempotency_key,
                entry_type="generation",
                amount=-GENERATION_COST,
                created_at=updated_at,
            )
            return self._public(
                {
                    **dict(account),
                    "credits": next_credits,
                    "updated_at": updated_at,
                },
                unlimited=False,
            )

    def _load_or_create(
        self,
        connection: Connection,
        user_id: str,
        *,
        for_update: bool = False,
    ) -> Any:
        statement = select(billing_accounts).where(
            billing_accounts.c.user_id == user_id
        )
        if for_update:
            statement = statement.with_for_update()
        account = connection.execute(statement).mappings().first()
        if account is not None:
            return account

        created_at = _now()
        values = {
            "user_id": user_id,
            "credits": INITIAL_CREDITS,
            "created_at": created_at,
            "updated_at": created_at,
        }
        connection.execute(insert(billing_accounts).values(**values))
        self._append_ledger(
            connection,
            user_id=user_id,
            idempotency_key="initial-grant",
            entry_type="initial_grant",
            amount=INITIAL_CREDITS,
            created_at=created_at,
        )
        return values

    @staticmethod
    def _has_transaction(
        connection: Connection,
        user_id: str,
        idempotency_key: str,
    ) -> bool:
        return connection.execute(
            select(billing_ledger.c.id).where(
                billing_ledger.c.user_id == user_id,
                billing_ledger.c.idempotency_key == idempotency_key,
            )
        ).first() is not None

    @staticmethod
    def _append_ledger(
        connection: Connection,
        *,
        user_id: str,
        idempotency_key: str,
        entry_type: str,
        amount: int,
        created_at: datetime,
    ) -> None:
        connection.execute(
            insert(billing_ledger).values(
                id=str(uuid.uuid4()),
                user_id=user_id,
                idempotency_key=idempotency_key,
                entry_type=entry_type,
                amount=amount,
                created_at=created_at,
            )
        )

    def _public(
        self,
        account: Any,
        *,
        unlimited: bool,
    ) -> dict[str, Any]:
        updated_at = account["updated_at"]
        return {
            "user_id": account["user_id"],
            "credits": account["credits"],
            "unlimited_credits": unlimited,
            "generations_remaining": account["credits"] // GENERATION_COST,
            "generation_limit": GENERATION_LIMIT,
            "generation_cost": GENERATION_COST,
            "initial_credits": INITIAL_CREDITS,
            "updated_at": (
                updated_at.isoformat()
                if hasattr(updated_at, "isoformat")
                else str(updated_at)
            ),
        }


billing_service = BillingService(engine=database_engine)
