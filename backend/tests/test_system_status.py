import os
import unittest
from unittest.mock import patch

import app.main as main_module
from app.models import SystemStatusResponse


class SystemStatusTests(unittest.TestCase):
    def test_status_is_public_and_ready_by_default(self) -> None:
        with patch.dict(os.environ, {"MAINTENANCE_MODE": "false"}):
            payload = main_module.system_status()
        self.assertEqual(payload["status"], "ready")
        self.assertFalse(payload["maintenance_mode"])
        route = next(
            route
            for route in main_module.app.routes
            if getattr(route, "path", None) == "/api/system/status"
        )
        self.assertEqual(route.methods, {"GET"})
        self.assertIs(route.response_model, SystemStatusResponse)
        self.assertEqual(
            SystemStatusResponse.model_validate(payload).model_dump(), payload
        )

    def test_maintenance_keeps_status_and_health_public_but_blocks_writes(
        self,
    ) -> None:
        with patch.dict(
            os.environ,
            {
                "MAINTENANCE_MODE": "true",
                "MAINTENANCE_MESSAGE": "Deploying a new release",
                "MAINTENANCE_RETRY_AFTER_SECONDS": "75",
            },
        ):
            status = main_module.system_status()
            health = main_module.health()
            generation_blocked = main_module._maintenance_blocks(
                "POST", "/api/generate"
            )
            deploy_blocked = main_module._maintenance_blocks(
                "POST",
                "/api/sessions/sess_0000000000000000/actions/deploy",
            )

        self.assertEqual(status["status"], "maintenance")
        self.assertEqual(status["message"], "Deploying a new release")
        self.assertEqual(status["retry_after_seconds"], 75)
        self.assertEqual(health["status"], "ok")
        self.assertTrue(generation_blocked)
        self.assertTrue(deploy_blocked)


if __name__ == "__main__":
    unittest.main()
