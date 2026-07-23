import tempfile
import unittest
from pathlib import Path

from app.models import PermissionDecisionRequest, SessionCreateRequest
from app.runner_services import STAGE_SKILLS, mpos_skill_adapter
import app.session_service as session_module


class ProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        session_module.SESSION_ROOT = Path(self.temp.name).resolve()
        self.service = session_module.SessionService()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_all_web_skill_contracts_are_present(self) -> None:
        for stage, expected_name in STAGE_SKILLS.items():
            contract = mpos_skill_adapter.contract(stage)
            self.assertEqual(contract.name, expected_name)
            self.assertEqual(len(contract.sha256), 64)

    def test_permission_decisions_are_idempotent(self) -> None:
        state = self.service.create(
            SessionCreateRequest(
                idempotency_key="create-test-0001",
                prompt="做一个极简计算器",
                package_name="com.example.calculator",
                targets=["web-preview", "package-only"],
            )
        )
        permission = next(
            item for item in state["permissions"] if item["permission_type"] == "file_write"
        )
        request = PermissionDecisionRequest(
            idempotency_key="permission-test-0001",
            decision="allow_once",
        )
        first = self.service.decide_permission(permission["permission_id"], request)
        second = self.service.decide_permission(permission["permission_id"], request)
        self.assertEqual(first["updated_at"], second["updated_at"])
        decided = next(
            item
            for item in second["permissions"]
            if item["permission_id"] == permission["permission_id"]
        )
        self.assertEqual(decided["decision"], "allow_once")

    def test_session_has_protocol_checkpoint_and_manifest_metadata(self) -> None:
        state = self.service.create(
            SessionCreateRequest(
                idempotency_key="create-test-0002",
                prompt="Build a calendar",
                package_name="com.example.calendar",
                targets=["package-only"],
            )
        )
        self.assertEqual(state["protocol_version"], "mpos-ai-app/v1")
        self.assertEqual(state["checkpoint_id"], "session_created")
        self.assertTrue(state["checkpoint_history"])
        self.assertEqual(len(state["input_hash"]), 64)
        self.assertIn("mpos_api_summary.json", state["api_summary_version"])
        self.assertNotIn(str(Path(self.temp.name)), str(state["permissions"]))


if __name__ == "__main__":
    unittest.main()
