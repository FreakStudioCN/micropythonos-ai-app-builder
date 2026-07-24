import tempfile
import unittest
from pathlib import Path

from app.billing import BillingService, InsufficientCredits


class BillingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.service = BillingService(Path(self.temp.name))
        self.user_id = "browser-test-user"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_new_user_receives_fifty_credits(self) -> None:
        account = self.service.account(self.user_id)
        self.assertEqual(account["credits"], 50)
        self.assertEqual(account["generation_cost"], 10)
        self.assertEqual(account["plan"], "free")

    def test_generation_charge_is_atomic_and_idempotent(self) -> None:
        first = self.service.consume_generation(self.user_id, "generation:sess:r1")
        second = self.service.consume_generation(self.user_id, "generation:sess:r1")
        self.assertEqual(first["credits"], 40)
        self.assertEqual(second["credits"], 40)

    def test_generation_is_rejected_when_balance_is_empty(self) -> None:
        for revision in range(1, 6):
            self.service.consume_generation(
                self.user_id,
                f"generation:sess:r{revision}",
            )
        with self.assertRaises(InsufficientCredits):
            self.service.consume_generation(
                self.user_id,
                "generation:sess:r6",
            )

    def test_demo_subscription_adds_plan_credits_once(self) -> None:
        first = self.service.subscribe(
            self.user_id,
            "plus",
            "subscribe-plus-0001",
        )
        second = self.service.subscribe(
            self.user_id,
            "plus",
            "subscribe-plus-0001",
        )
        self.assertEqual(first["credits"], 350)
        self.assertEqual(second["credits"], 350)
        self.assertEqual(first["plan"], "plus")
