import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, func, select

from app.billing import BillingService, billing_accounts, billing_ledger
from app.payments import PaymentError, PaymentService, payment_orders


class FakePaymentService(PaymentService):
    async def _create_paypal(self, order_id, plan, public_url):
        return f"paypal-{order_id}", f"https://paypal.example/approve/{order_id}"


class PaymentServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        path = Path(self.temp.name, "payments.db")
        self.engine = create_engine(f"sqlite:///{path}")
        self.billing = BillingService(engine=self.engine)
        self.service = FakePaymentService(engine=self.engine, billing=self.billing)

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    async def test_create_order_uses_server_plan_price(self):
        with patch.dict(
            "os.environ",
            {
                "PAYPAL_CHECKOUT_ENABLED": "true",
                "PAYPAL_CLIENT_ID": "client",
                "PAYPAL_CLIENT_SECRET": "secret",
            },
            clear=True,
        ):
            order = await self.service.create_order(
                "user-1", "paypal", "plus", "https://mpos.example"
            )
        self.assertEqual(order["amount_minor"], 4900)
        self.assertEqual(order["credits"], 300)
        self.assertEqual(order["status"], "pending")
        self.assertTrue(order["checkout_url"].startswith("https://paypal.example/"))

    def test_paid_order_grants_credits_exactly_once(self):
        now = __import__("datetime").datetime.now(__import__("datetime").UTC)
        with self.engine.begin() as connection:
            connection.execute(
                payment_orders.insert().values(
                    id="order1",
                    user_id="user-1",
                    provider="alipay",
                    plan_id="go",
                    amount_minor=1900,
                    currency="CNY",
                    credits=100,
                    status="pending",
                    created_at=now,
                    updated_at=now,
                )
            )
        self.service.mark_paid(
            "order1", provider_payment_id="trade-1", amount_minor=1900, currency="CNY"
        )
        self.service.mark_paid(
            "order1", provider_payment_id="trade-1", amount_minor=1900, currency="CNY"
        )
        account = self.billing.account("user-1")
        self.assertEqual(account["credits"], 150)
        with self.engine.begin() as connection:
            payment_entries = connection.scalar(
                select(func.count()).select_from(billing_ledger).where(
                    billing_ledger.c.idempotency_key == "payment:order1"
                )
            )
        self.assertEqual(payment_entries, 1)
        self.assertEqual(self.service.get_order("order1")["status"], "paid")

    def test_amount_mismatch_does_not_grant_credits(self):
        now = __import__("datetime").datetime.now(__import__("datetime").UTC)
        with self.engine.begin() as connection:
            connection.execute(
                payment_orders.insert().values(
                    id="order2",
                    user_id="user-2",
                    provider="wechat",
                    plan_id="go",
                    amount_minor=1900,
                    currency="CNY",
                    credits=100,
                    status="pending",
                    created_at=now,
                    updated_at=now,
                )
            )
        with self.assertRaisesRegex(PaymentError, "金额"):
            self.service.mark_paid(
                "order2", provider_payment_id="trade-2", amount_minor=1, currency="CNY"
            )
        self.assertEqual(self.service.get_order("order2")["status"], "pending")
        self.assertEqual(self.billing.account("user-2")["credits"], 50)


if __name__ == "__main__":
    unittest.main()
