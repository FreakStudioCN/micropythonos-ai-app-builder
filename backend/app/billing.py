"""Persistent credits and subscription plans for the browser product."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


INITIAL_CREDITS = 50
GENERATION_COST = 10
PLANS: tuple[dict[str, Any], ...] = (
    {
        "id": "go",
        "name": "Go",
        "price_cny": 19,
        "credits": 100,
        "generations": 10,
        "featured": False,
        "benefits_zh": ["每月 100 点", "最多生成 10 次", "Web 预览与 MPK 打包"],
        "benefits_en": ["100 credits/month", "Up to 10 generations", "Web preview and MPK packaging"],
    },
    {
        "id": "plus",
        "name": "Plus",
        "price_cny": 49,
        "credits": 300,
        "generations": 30,
        "featured": True,
        "benefits_zh": ["每月 300 点", "最多生成 30 次", "优先生成与连续修改", "ESP32 真机部署"],
        "benefits_en": ["300 credits/month", "Up to 30 generations", "Priority generation and revisions", "ESP32 deployment"],
    },
    {
        "id": "pro",
        "name": "Pro",
        "price_cny": 129,
        "credits": 1000,
        "generations": 100,
        "featured": False,
        "benefits_zh": ["每月 1000 点", "最多生成 100 次", "最高优先级", "真机部署与发布检查"],
        "benefits_en": ["1,000 credits/month", "Up to 100 generations", "Highest priority", "Device deployment and publish checks"],
    },
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class InsufficientCredits(RuntimeError):
    def __init__(self, balance: int, required: int) -> None:
        super().__init__("点数不足，请先订阅或充值")
        self.balance = balance
        self.required = required


class BillingService:
    def __init__(self, root: Path | None = None) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.root = (
            root
            or Path(
                os.getenv(
                    "MPOS_BILLING_ROOT",
                    str(project_root / "backend" / "sessions" / "_billing"),
                )
            )
        ).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    @property
    def demo_mode(self) -> bool:
        return os.getenv("MPOS_BILLING_DEMO_MODE", "true").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    def plans(self) -> dict[str, Any]:
        return {
            "currency": "CNY",
            "generation_cost": GENERATION_COST,
            "initial_credits": INITIAL_CREDITS,
            "checkout_mode": "demo" if self.demo_mode else "provider_required",
            "plans": list(PLANS),
        }

    def account(self, user_id: str) -> dict[str, Any]:
        with self._lock:
            return self._public(self._load(user_id))

    def consume_generation(
        self, user_id: str, idempotency_key: str
    ) -> dict[str, Any]:
        with self._lock:
            account = self._load(user_id)
            if self._has_transaction(account, idempotency_key):
                return self._public(account)
            if account["credits"] < GENERATION_COST:
                raise InsufficientCredits(account["credits"], GENERATION_COST)
            account["credits"] -= GENERATION_COST
            account["updated_at"] = _now()
            account["ledger"].append(
                {
                    "idempotency_key": idempotency_key,
                    "type": "generation",
                    "amount": -GENERATION_COST,
                    "created_at": account["updated_at"],
                }
            )
            self._save(user_id, account)
            return self._public(account)

    def subscribe(
        self, user_id: str, plan_id: str, idempotency_key: str
    ) -> dict[str, Any]:
        plan = next((item for item in PLANS if item["id"] == plan_id), None)
        if plan is None:
            raise ValueError("未知订阅套餐")
        if not self.demo_mode:
            raise RuntimeError("尚未配置支付服务商，不能直接激活付费套餐")
        with self._lock:
            account = self._load(user_id)
            if self._has_transaction(account, idempotency_key):
                return self._public(account)
            account["credits"] += int(plan["credits"])
            account["plan"] = plan_id
            account["subscription_status"] = "active_demo"
            account["updated_at"] = _now()
            account["ledger"].append(
                {
                    "idempotency_key": idempotency_key,
                    "type": "demo_subscription",
                    "plan": plan_id,
                    "amount": int(plan["credits"]),
                    "created_at": account["updated_at"],
                }
            )
            self._save(user_id, account)
            return self._public(account)

    @staticmethod
    def _has_transaction(account: dict[str, Any], idempotency_key: str) -> bool:
        return any(
            entry["idempotency_key"] == idempotency_key
            for entry in account["ledger"]
        )

    def _path(self, user_id: str) -> Path:
        digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
        return self.root / f"{digest}.json"

    def _load(self, user_id: str) -> dict[str, Any]:
        path = self._path(user_id)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        created_at = _now()
        account = {
            "user_id": user_id,
            "credits": INITIAL_CREDITS,
            "plan": "free",
            "subscription_status": "inactive",
            "created_at": created_at,
            "updated_at": created_at,
            "ledger": [
                {
                    "idempotency_key": "initial-grant",
                    "type": "initial_grant",
                    "amount": INITIAL_CREDITS,
                    "created_at": created_at,
                }
            ],
        }
        self._save(user_id, account)
        return account

    def _save(self, user_id: str, account: dict[str, Any]) -> None:
        path = self._path(user_id)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(account, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _public(self, account: dict[str, Any]) -> dict[str, Any]:
        return {
            "user_id": account["user_id"],
            "credits": account["credits"],
            "plan": account["plan"],
            "subscription_status": account["subscription_status"],
            "generation_cost": GENERATION_COST,
            "initial_credits": INITIAL_CREDITS,
            "checkout_mode": "demo" if self.demo_mode else "provider_required",
            "updated_at": account["updated_at"],
        }


billing_service = BillingService()
