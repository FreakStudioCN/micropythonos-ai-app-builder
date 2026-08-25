"""Server-side checkout integrations for PayPal, WeChat Pay, and Alipay."""

from __future__ import annotations

import base64
import json
import os
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import quote, urlencode

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, insert, select, update
from sqlalchemy.engine import Engine

from .billing import BillingService, billing_service
from .database import database_engine


Provider = Literal["paypal", "wechat", "alipay"]


@dataclass(frozen=True)
class PaymentPlan:
    id: str
    name: str
    price_cny: int
    credits: int


PLANS = {
    "go": PaymentPlan("go", "Go", 19, 100),
    "plus": PaymentPlan("plus", "Plus", 49, 300),
    "pro": PaymentPlan("pro", "Pro", 129, 1000),
}

metadata = MetaData()
payment_orders = Table(
    "app_payment_orders",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("user_id", String(36), nullable=False, index=True),
    Column("provider", String(16), nullable=False),
    Column("plan_id", String(16), nullable=False),
    Column("amount_minor", Integer, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("credits", Integer, nullable=False),
    Column("status", String(24), nullable=False, index=True),
    Column("provider_order_id", String(128), nullable=True, index=True),
    Column("checkout_url", String(2000), nullable=True),
    Column("provider_payment_id", String(128), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("paid_at", DateTime(timezone=True), nullable=True),
)


class PaymentError(RuntimeError):
    pass


class PaymentNotConfigured(PaymentError):
    pass


class PaymentNotFound(PaymentError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _load_private_key(value: str):
    return serialization.load_pem_private_key(value.replace("\\n", "\n").encode(), password=None)


def _load_public_key(value: str):
    return serialization.load_pem_public_key(value.replace("\\n", "\n").encode())


def _rsa_sign(private_key: str, message: str) -> str:
    signature = _load_private_key(private_key).sign(
        message.encode(), padding.PKCS1v15(), hashes.SHA256()
    )
    return base64.b64encode(signature).decode()


def _rsa_verify(public_key: str, message: str, signature: str) -> bool:
    try:
        _load_public_key(public_key).verify(
            base64.b64decode(signature),
            message.encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True
    except Exception:
        return False


class PaymentService:
    def __init__(
        self,
        *,
        engine: Engine | None = None,
        billing: BillingService | None = None,
    ) -> None:
        self.engine = engine or database_engine
        self.billing = billing or billing_service
        metadata.create_all(self.engine)

    def providers(self) -> dict[str, bool]:
        return {
            "paypal": bool(
                _env("PAYPAL_CHECKOUT_ENABLED").lower() in {"1", "true", "yes", "on"}
                and _env("PAYPAL_CLIENT_ID")
                and _env("PAYPAL_CLIENT_SECRET")
            ),
            "wechat": bool(
                _env("WECHAT_PAY_MCH_ID")
                and _env("WECHAT_PAY_CERT_SERIAL_NO")
                and _env("WECHAT_PAY_PRIVATE_KEY")
                and _env("WECHAT_PAY_APP_ID")
                and _env("WECHAT_PAY_NOTIFY_URL")
                and _env("WECHAT_PAY_API_V3_KEY")
                and _env("WECHAT_PAY_PLATFORM_PUBLIC_KEY")
            ),
            "alipay": bool(
                _env("ALIPAY_APP_ID")
                and _env("ALIPAY_PRIVATE_KEY")
                and _env("ALIPAY_PUBLIC_KEY")
                and _env("ALIPAY_NOTIFY_URL")
            ),
        }

    def catalog(self) -> dict[str, Any]:
        return {
            "currency": "CNY",
            "providers": self.providers(),
            "plans": [
                {
                    "id": plan.id,
                    "name": plan.name,
                    "price": plan.price_cny,
                    "credits": plan.credits,
                }
                for plan in PLANS.values()
            ],
        }

    async def create_order(
        self,
        user_id: str,
        provider: Provider,
        plan_id: str,
        public_url: str,
    ) -> dict[str, Any]:
        plan = PLANS.get(plan_id)
        if plan is None:
            raise PaymentError("未知套餐")
        if provider not in self.providers() or not self.providers()[provider]:
            raise PaymentNotConfigured(f"{provider} 尚未配置")

        order_id = uuid.uuid4().hex
        now = _now()
        with self.engine.begin() as connection:
            connection.execute(
                insert(payment_orders).values(
                    id=order_id,
                    user_id=user_id,
                    provider=provider,
                    plan_id=plan.id,
                    amount_minor=plan.price_cny * 100,
                    currency="CNY",
                    credits=plan.credits,
                    status="creating",
                    created_at=now,
                    updated_at=now,
                )
            )
        try:
            if provider == "paypal":
                external_id, checkout_url = await self._create_paypal(order_id, plan, public_url)
            elif provider == "wechat":
                external_id, checkout_url = await self._create_wechat(order_id, plan)
            else:
                external_id, checkout_url = self._create_alipay(order_id, plan, public_url)
        except Exception:
            with self.engine.begin() as connection:
                connection.execute(
                    update(payment_orders)
                    .where(payment_orders.c.id == order_id)
                    .values(status="failed", updated_at=_now())
                )
            raise

        with self.engine.begin() as connection:
            connection.execute(
                update(payment_orders)
                .where(payment_orders.c.id == order_id)
                .values(
                    status="pending",
                    provider_order_id=external_id,
                    checkout_url=checkout_url,
                    updated_at=_now(),
                )
            )
        return self.get_order(order_id, user_id=user_id)

    def get_order(self, order_id: str, *, user_id: str | None = None) -> dict[str, Any]:
        with self.engine.begin() as connection:
            statement = select(payment_orders).where(payment_orders.c.id == order_id)
            if user_id is not None:
                statement = statement.where(payment_orders.c.user_id == user_id)
            order = connection.execute(statement).mappings().first()
        if order is None:
            raise PaymentNotFound("支付订单不存在")
        return self._public_order(dict(order))

    def list_orders(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.engine.begin() as connection:
            rows = connection.execute(
                select(payment_orders)
                .where(payment_orders.c.user_id == user_id)
                .order_by(payment_orders.c.created_at.desc())
                .limit(limit)
            ).mappings()
            return [self._public_order(dict(row)) for row in rows]

    def mark_paid(
        self,
        order_id: str,
        *,
        provider_payment_id: str,
        amount_minor: int,
        currency: str,
    ) -> dict[str, Any]:
        with self.engine.begin() as connection:
            order = connection.execute(
                select(payment_orders)
                .where(payment_orders.c.id == order_id)
                .with_for_update()
            ).mappings().first()
            if order is None:
                raise PaymentNotFound("支付订单不存在")
            if amount_minor != order["amount_minor"] or currency.upper() != order["currency"]:
                raise PaymentError("支付金额或币种与订单不一致")
            order_data = dict(order)

        self.billing.add_credits(
            order_data["user_id"],
            order_data["credits"],
            f"payment:{order_id}",
            entry_type=f"payment_{order_data['provider']}",
        )
        paid_at = order_data.get("paid_at") or _now()
        with self.engine.begin() as connection:
            connection.execute(
                update(payment_orders)
                .where(payment_orders.c.id == order_id)
                .values(
                    status="paid",
                    provider_payment_id=provider_payment_id,
                    paid_at=paid_at,
                    updated_at=_now(),
                )
            )
        return self.get_order(order_id)

    async def capture_paypal(self, order_id: str, user_id: str) -> dict[str, Any]:
        order = self._private_order(order_id, user_id=user_id)
        if order["provider"] != "paypal":
            raise PaymentError("该订单不是 PayPal 订单")
        if order["status"] == "paid":
            return self._public_order(order)
        token = await self._paypal_access_token()
        base = self._paypal_base_url()
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{base}/v2/checkout/orders/{quote(order['provider_order_id'], safe='')}/capture",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "PayPal-Request-Id": f"capture-{order_id}",
                },
                json={},
            )
        payload = self._response_json(response, "PayPal capture")
        if payload.get("status") != "COMPLETED":
            raise PaymentError("PayPal 尚未完成付款")
        capture = payload["purchase_units"][0]["payments"]["captures"][0]
        amount = capture["amount"]
        return self.mark_paid(
            order_id,
            provider_payment_id=capture["id"],
            amount_minor=self._money_to_minor(amount["value"]),
            currency=amount["currency_code"],
        )

    async def handle_paypal_webhook(self, headers: Any, payload: dict[str, Any]) -> None:
        webhook_id = _env("PAYPAL_WEBHOOK_ID")
        if not webhook_id:
            raise PaymentNotConfigured("PAYPAL_WEBHOOK_ID 尚未配置")
        token = await self._paypal_access_token()
        verification = {
            "auth_algo": headers.get("paypal-auth-algo"),
            "cert_url": headers.get("paypal-cert-url"),
            "transmission_id": headers.get("paypal-transmission-id"),
            "transmission_sig": headers.get("paypal-transmission-sig"),
            "transmission_time": headers.get("paypal-transmission-time"),
            "webhook_id": webhook_id,
            "webhook_event": payload,
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{self._paypal_base_url()}/v1/notifications/verify-webhook-signature",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=verification,
            )
        result = self._response_json(response, "PayPal webhook verification")
        if result.get("verification_status") != "SUCCESS":
            raise PaymentError("PayPal 回调验签失败")
        if payload.get("event_type") != "PAYMENT.CAPTURE.COMPLETED":
            return
        resource = payload.get("resource") or {}
        external_order_id = (((resource.get("supplementary_data") or {}).get("related_ids") or {}).get("order_id"))
        order = self._order_by_provider_id("paypal", external_order_id)
        amount = resource.get("amount") or {}
        self.mark_paid(
            order["id"],
            provider_payment_id=str(resource.get("id", "")),
            amount_minor=self._money_to_minor(str(amount.get("value", "0"))),
            currency=str(amount.get("currency_code", "")),
        )

    def handle_wechat_webhook(self, headers: Any, body: bytes) -> None:
        timestamp = headers.get("wechatpay-timestamp", "")
        nonce = headers.get("wechatpay-nonce", "")
        signature = headers.get("wechatpay-signature", "")
        serial = headers.get("wechatpay-serial", "")
        configured_serial = _env("WECHAT_PAY_PLATFORM_SERIAL_NO")
        if configured_serial and serial != configured_serial:
            raise PaymentError("微信支付平台证书序列号不匹配")
        public_key = _env("WECHAT_PAY_PLATFORM_PUBLIC_KEY")
        if not public_key:
            raise PaymentNotConfigured("WECHAT_PAY_PLATFORM_PUBLIC_KEY 尚未配置")
        message = f"{timestamp}\n{nonce}\n{body.decode()}\n"
        if not _rsa_verify(public_key, message, signature):
            raise PaymentError("微信支付回调验签失败")
        envelope = json.loads(body)
        resource = envelope["resource"]
        api_key = _env("WECHAT_PAY_API_V3_KEY").encode()
        if len(api_key) != 32:
            raise PaymentNotConfigured("WECHAT_PAY_API_V3_KEY 必须为 32 字节")
        plaintext = AESGCM(api_key).decrypt(
            resource["nonce"].encode(),
            base64.b64decode(resource["ciphertext"]),
            resource.get("associated_data", "").encode(),
        )
        transaction = json.loads(plaintext)
        if transaction.get("trade_state") != "SUCCESS":
            return
        if (
            transaction.get("mchid") != _env("WECHAT_PAY_MCH_ID")
            or transaction.get("appid") != _env("WECHAT_PAY_APP_ID")
        ):
            raise PaymentError("微信支付回调商户信息不匹配")
        amount = transaction.get("amount") or {}
        self.mark_paid(
            transaction["out_trade_no"],
            provider_payment_id=transaction["transaction_id"],
            amount_minor=int(amount["total"]),
            currency=amount.get("currency", "CNY"),
        )

    def handle_alipay_webhook(self, form: dict[str, str]) -> None:
        public_key = _env("ALIPAY_PUBLIC_KEY")
        if not public_key:
            raise PaymentNotConfigured("ALIPAY_PUBLIC_KEY 尚未配置")
        signature = form.get("sign", "")
        canonical = "&".join(
            f"{key}={value}"
            for key, value in sorted(form.items())
            if key not in {"sign", "sign_type"} and value != ""
        )
        if not _rsa_verify(public_key, canonical, signature):
            raise PaymentError("支付宝回调验签失败")
        if form.get("app_id") != _env("ALIPAY_APP_ID"):
            raise PaymentError("支付宝回调应用 ID 不匹配")
        seller_id = _env("ALIPAY_SELLER_ID")
        if seller_id and form.get("seller_id") != seller_id:
            raise PaymentError("支付宝回调商户 ID 不匹配")
        if form.get("trade_status") not in {"TRADE_SUCCESS", "TRADE_FINISHED"}:
            return
        self.mark_paid(
            form["out_trade_no"],
            provider_payment_id=form["trade_no"],
            amount_minor=self._money_to_minor(form["total_amount"]),
            currency="CNY",
        )

    async def _create_paypal(self, order_id: str, plan: PaymentPlan, public_url: str) -> tuple[str, str]:
        token = await self._paypal_access_token()
        return_url = f"{public_url}/?payment=paypal&order_id={order_id}"
        payload = {
            "intent": "CAPTURE",
            "purchase_units": [{
                "reference_id": order_id,
                "custom_id": order_id,
                "invoice_id": order_id,
                "description": f"Blockless-Make-APP {plan.name} ({plan.credits} credits)",
                "amount": {"currency_code": "CNY", "value": f"{plan.price_cny:.2f}"},
            }],
            "application_context": {
                "brand_name": "Blockless-Make-APP",
                "shipping_preference": "NO_SHIPPING",
                "user_action": "PAY_NOW",
                "return_url": return_url,
                "cancel_url": f"{public_url}/?payment=cancelled&order_id={order_id}",
            },
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{self._paypal_base_url()}/v2/checkout/orders",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "PayPal-Request-Id": f"create-{order_id}",
                },
                json=payload,
            )
        data = self._response_json(response, "PayPal create order")
        approve = next((link["href"] for link in data.get("links", []) if link.get("rel") == "approve"), "")
        if not approve:
            raise PaymentError("PayPal 未返回付款链接")
        return data["id"], approve

    async def _paypal_access_token(self) -> str:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{self._paypal_base_url()}/v1/oauth2/token",
                auth=(_env("PAYPAL_CLIENT_ID"), _env("PAYPAL_CLIENT_SECRET")),
                data={"grant_type": "client_credentials"},
            )
        data = self._response_json(response, "PayPal authentication")
        return data["access_token"]

    @staticmethod
    def _paypal_base_url() -> str:
        return "https://api-m.paypal.com" if _env("PAYPAL_ENVIRONMENT").lower() == "live" else "https://api-m.sandbox.paypal.com"

    async def _create_wechat(self, order_id: str, plan: PaymentPlan) -> tuple[str, str]:
        path = "/v3/pay/transactions/native"
        payload = {
            "appid": _env("WECHAT_PAY_APP_ID"),
            "mchid": _env("WECHAT_PAY_MCH_ID"),
            "description": f"Blockless-Make-APP {plan.name}",
            "out_trade_no": order_id,
            "notify_url": _env("WECHAT_PAY_NOTIFY_URL"),
            "amount": {"total": plan.price_cny * 100, "currency": "CNY"},
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        message = f"POST\n{path}\n{timestamp}\n{nonce}\n{body}\n"
        signature = _rsa_sign(_env("WECHAT_PAY_PRIVATE_KEY"), message)
        authorization = (
            'WECHATPAY2-SHA256-RSA2048 '
            f'mchid="{_env("WECHAT_PAY_MCH_ID")}",nonce_str="{nonce}",'
            f'signature="{signature}",timestamp="{timestamp}",'
            f'serial_no="{_env("WECHAT_PAY_CERT_SERIAL_NO")}"'
        )
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"https://api.mch.weixin.qq.com{path}",
                content=body.encode(),
                headers={"Authorization": authorization, "Content-Type": "application/json", "Accept": "application/json"},
            )
        data = self._response_json(response, "WeChat Pay create order")
        return order_id, data["code_url"]

    def _create_alipay(self, order_id: str, plan: PaymentPlan, public_url: str) -> tuple[str, str]:
        params = {
            "app_id": _env("ALIPAY_APP_ID"),
            "method": "alipay.trade.page.pay",
            "format": "JSON",
            "charset": "utf-8",
            "sign_type": "RSA2",
            "timestamp": _now().strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.0",
            "notify_url": _env("ALIPAY_NOTIFY_URL"),
            "return_url": f"{public_url}/?payment=alipay&order_id={order_id}",
            "biz_content": json.dumps({
                "out_trade_no": order_id,
                "product_code": "FAST_INSTANT_TRADE_PAY",
                "total_amount": f"{plan.price_cny:.2f}",
                "subject": f"Blockless-Make-APP {plan.name}",
                "body": f"{plan.credits} credits",
            }, ensure_ascii=False, separators=(",", ":")),
        }
        canonical = "&".join(f"{key}={value}" for key, value in sorted(params.items()))
        params["sign"] = _rsa_sign(_env("ALIPAY_PRIVATE_KEY"), canonical)
        gateway = _env("ALIPAY_GATEWAY_URL") or "https://openapi.alipay.com/gateway.do"
        return order_id, f"{gateway}?{urlencode(params)}"

    def _private_order(self, order_id: str, *, user_id: str | None = None) -> dict[str, Any]:
        with self.engine.begin() as connection:
            statement = select(payment_orders).where(payment_orders.c.id == order_id)
            if user_id is not None:
                statement = statement.where(payment_orders.c.user_id == user_id)
            order = connection.execute(statement).mappings().first()
        if order is None:
            raise PaymentNotFound("支付订单不存在")
        return dict(order)

    def _order_by_provider_id(self, provider: str, external_id: str | None) -> dict[str, Any]:
        if not external_id:
            raise PaymentNotFound("回调中缺少支付平台订单号")
        with self.engine.begin() as connection:
            order = connection.execute(
                select(payment_orders).where(
                    payment_orders.c.provider == provider,
                    payment_orders.c.provider_order_id == external_id,
                )
            ).mappings().first()
        if order is None:
            raise PaymentNotFound("支付订单不存在")
        return dict(order)

    @staticmethod
    def _public_order(order: dict[str, Any]) -> dict[str, Any]:
        return {
            key: (value.isoformat() if hasattr(value, "isoformat") else value)
            for key, value in order.items()
            if key not in {"user_id"}
        }

    @staticmethod
    def _money_to_minor(value: str) -> int:
        return int(Decimal(value) * 100)

    @staticmethod
    def _response_json(response: httpx.Response, label: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise PaymentError(f"{label} 返回了无效响应") from exc
        if not response.is_success:
            message = payload.get("message") or payload.get("error_description") or payload.get("error") or response.reason_phrase
            raise PaymentError(f"{label} 失败：{message}")
        return payload


payment_service = PaymentService()
