"""Referral codes and idempotent growth-task rewards."""

from __future__ import annotations

import secrets
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Column, DateTime, MetaData, String, Table, UniqueConstraint, func, insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from .billing import BillingService


REFERRAL_REWARD = 5
TASK_REWARD = 5
TASK_KEYS = {
    "generate",
    "device",
    "preview",
    "share_xiaohongshu",
    "share_moments",
    "share_douyin",
    "share_bilibili",
    "share_twitter",
}

metadata = MetaData()
referral_profiles = Table(
    "app_referral_profiles",
    metadata,
    Column("user_id", String(36), primary_key=True),
    Column("invite_code", String(16), nullable=False, unique=True, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
referrals = Table(
    "app_referrals",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("inviter_user_id", String(36), nullable=False, index=True),
    Column("invitee_user_id", String(36), nullable=False, unique=True, index=True),
    Column("invite_code", String(16), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
growth_tasks = Table(
    "app_growth_tasks",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), nullable=False, index=True),
    Column("task_key", String(48), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("user_id", "task_key", name="uq_growth_user_task"),
)


def _now() -> datetime:
    return datetime.now(UTC)


class InvalidInviteCode(ValueError):
    pass


class InvalidGrowthTask(ValueError):
    pass


class GrowthService:
    def __init__(self, *, engine: Engine, billing: BillingService) -> None:
        self.engine = engine
        self.billing = billing
        metadata.create_all(self.engine)
        self._lock = threading.RLock()

    def ensure_profile(self, user_id: str) -> str:
        with self._lock:
            with self.engine.connect() as connection:
                code = connection.execute(
                    select(referral_profiles.c.invite_code).where(
                        referral_profiles.c.user_id == user_id
                    )
                ).scalar_one_or_none()
            if code:
                return str(code)
            for _ in range(8):
                code = secrets.token_hex(4).upper()
                try:
                    with self.engine.begin() as connection:
                        connection.execute(insert(referral_profiles).values(
                            user_id=user_id,
                            invite_code=code,
                            created_at=_now(),
                        ))
                    return code
                except IntegrityError:
                    with self.engine.connect() as connection:
                        existing = connection.execute(
                            select(referral_profiles.c.invite_code).where(
                                referral_profiles.c.user_id == user_id
                            )
                        ).scalar_one_or_none()
                    if existing:
                        return str(existing)
            raise RuntimeError("无法创建邀请码，请稍后重试")

    def resolve_inviter(self, invite_code: str | None) -> str | None:
        code = (invite_code or "").strip().upper()
        if not code:
            return None
        with self.engine.connect() as connection:
            inviter_id = connection.execute(
                select(referral_profiles.c.user_id).where(
                    referral_profiles.c.invite_code == code
                )
            ).scalar_one_or_none()
        if inviter_id is None:
            raise InvalidInviteCode("邀请码无效，请检查后重试")
        return str(inviter_id)

    def record_referral(
        self,
        *,
        inviter_user_id: str,
        invitee_user_id: str,
        invite_code: str,
    ) -> None:
        if inviter_user_id == invitee_user_id:
            raise InvalidInviteCode("不能使用自己的邀请码")
        referral_id = str(uuid.uuid4())
        try:
            with self._lock, self.engine.begin() as connection:
                connection.execute(insert(referrals).values(
                    id=referral_id,
                    inviter_user_id=inviter_user_id,
                    invitee_user_id=invitee_user_id,
                    invite_code=invite_code.strip().upper(),
                    created_at=_now(),
                ))
        except IntegrityError:
            return
        self._reconcile_user(inviter_user_id)

    def claim_task(self, user_id: str, task_key: str, *, unlimited: bool = False) -> dict[str, Any]:
        if task_key not in TASK_KEYS:
            raise InvalidGrowthTask("未知任务")
        try:
            with self._lock, self.engine.begin() as connection:
                connection.execute(insert(growth_tasks).values(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    task_key=task_key,
                    completed_at=_now(),
                ))
        except IntegrityError:
            pass
        self._reconcile_user(user_id, unlimited=unlimited)
        return self.summary(user_id, unlimited=unlimited, reconcile=False)

    def summary(
        self,
        user_id: str,
        *,
        unlimited: bool = False,
        reconcile: bool = True,
    ) -> dict[str, Any]:
        invite_code = self.ensure_profile(user_id)
        if reconcile:
            self._reconcile_user(user_id, unlimited=unlimited)
        with self.engine.connect() as connection:
            referral_count = int(connection.execute(
                select(func.count()).select_from(referrals).where(
                    referrals.c.inviter_user_id == user_id
                )
            ).scalar_one())
            completed = list(connection.execute(
                select(growth_tasks.c.task_key).where(
                    growth_tasks.c.user_id == user_id
                ).order_by(growth_tasks.c.completed_at)
            ).scalars())
        return {
            "invite_code": invite_code,
            "referral_count": referral_count,
            "referral_reward": REFERRAL_REWARD,
            "task_reward": TASK_REWARD,
            "completed_task_keys": completed,
            "credits": self.billing.account(user_id, unlimited=unlimited)["credits"],
        }

    def _reconcile_user(self, user_id: str, *, unlimited: bool = False) -> None:
        with self.engine.connect() as connection:
            referral_rows = list(connection.execute(
                select(referrals.c.invitee_user_id).where(
                    referrals.c.inviter_user_id == user_id
                )
            ).scalars())
            task_rows = list(connection.execute(
                select(growth_tasks.c.task_key).where(growth_tasks.c.user_id == user_id)
            ).scalars())
        for invitee_id in referral_rows:
            self.billing.award_credits(
                user_id,
                f"referral:{invitee_id}",
                amount=REFERRAL_REWARD,
                entry_type="referral_reward",
                unlimited=unlimited,
            )
        for task_key in task_rows:
            self.billing.award_credits(
                user_id,
                f"growth-task:{task_key}",
                amount=TASK_REWARD,
                entry_type="task_reward",
                unlimited=unlimited,
            )


def growth_service_for(engine: Engine, billing: BillingService) -> GrowthService:
    return GrowthService(engine=engine, billing=billing)
