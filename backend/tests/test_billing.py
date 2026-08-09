import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select

from app.billing import BillingService, InsufficientCredits, billing_ledger


class BillingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.service = BillingService(Path(self.temp.name))
        self.user_id = "browser-test-user"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_new_user_receives_twenty_credits(self) -> None:
        account = self.service.account(self.user_id)
        self.assertEqual(account["credits"], 20)
        self.assertEqual(account["generation_cost"], 10)
        self.assertEqual(account["generations_remaining"], 2)
        self.assertEqual(account["generation_limit"], 2)
        self.assertFalse(account["unlimited_credits"])

    def test_generation_charge_is_atomic_and_idempotent(self) -> None:
        first = self.service.consume_generation(self.user_id, "generation:sess:r1")
        second = self.service.consume_generation(self.user_id, "generation:sess:r1")
        self.assertEqual(first["credits"], 10)
        self.assertEqual(second["credits"], 10)

    def test_availability_check_does_not_consume_credits(self) -> None:
        first = self.service.ensure_generation_available(self.user_id)
        second = self.service.ensure_generation_available(self.user_id)

        self.assertEqual(first["credits"], 20)
        self.assertEqual(second["credits"], 20)
        with self.service.engine.connect() as connection:
            generation_entries = connection.execute(
                select(billing_ledger).where(
                    billing_ledger.c.user_id == self.user_id,
                    billing_ledger.c.entry_type == "generation",
                )
            ).all()
        self.assertEqual(generation_entries, [])

    def test_generation_is_rejected_when_balance_is_empty(self) -> None:
        for revision in range(1, 3):
            self.service.consume_generation(
                self.user_id,
                f"generation:sess:r{revision}",
            )
        with self.assertRaises(InsufficientCredits):
            self.service.consume_generation(
                self.user_id,
                "generation:sess:r3",
            )
        with self.assertRaises(InsufficientCredits):
            self.service.ensure_generation_available(self.user_id)

    def test_unlimited_generation_keeps_balance_and_writes_no_consumption(self) -> None:
        account = self.service.account(self.user_id, unlimited=True)
        for revision in range(1, 8):
            account = self.service.consume_generation(
                self.user_id,
                f"generation:admin:r{revision}",
                unlimited=True,
            )

        self.assertEqual(account["credits"], 20)
        self.assertTrue(account["unlimited_credits"])
        with self.service.engine.connect() as connection:
            generation_entries = connection.execute(
                select(billing_ledger).where(
                    billing_ledger.c.user_id == self.user_id,
                    billing_ledger.c.entry_type == "generation",
                )
            ).all()
        self.assertEqual(generation_entries, [])
