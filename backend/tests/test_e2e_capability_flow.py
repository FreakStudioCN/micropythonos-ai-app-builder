"""End-to-end capability flow over the real HTTP API.

There is no browser E2E suite in this repo, and a real non-demo generation
needs a DEEPSEEK_API_KEY nobody has in CI. This is the honest maximum: every
layer runs for real — FastAPI routing, auth, billing, the session state
machine, requirement analysis, the prompt builder, the generation validators,
the vendored hardware policy gate, artifact writing — and only the AI provider
is replaced.

The fake provider deliberately calls the *real* ``_build_user_prompt`` with the
request the session actually produced, so "the capability contract reaches the
model" is observed rather than assumed. That is the exact wiring that was
silently inert before: the modules existed and the unit tests passed, because
each end was tested in isolation and the seam between them never was.

Written artifacts are validated against the runner's JSON schemas, so a
protocol drift shows up here instead of in production.
"""

import asyncio
import contextlib
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from app.auth import AuthService
from app.billing import BillingService
from app.session_service import SessionService
import app.generator as generator_module
import app.main as main_module
import app.session_service as session_module

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "runner" / "schemas"

# A portable, capability-aware App: probes the camera, keeps working without
# it, offers focus navigation as well as touch, and releases the device on
# exit. It must pass every existing validator plus the new capability checks.
CAMERA_APP = """
import lvgl as lv
from mpos import Activity, CameraManager

class GeneratedApp(Activity):
    def onCreate(self):
        screen = lv.obj()
        screen.set_size(320, 240)
        screen.set_style_bg_color(lv.color_hex(0x0F172A), 0)
        screen.set_style_text_color(lv.color_hex(0xF8FAFC), 0)
        screen.set_style_radius(12, 0)
        screen.set_style_pad_all(10, 0)
        card = lv.obj(screen)
        card.set_size(280, 160)
        card.set_pos(20, 40)
        card.set_style_bg_color(lv.color_hex(0x1E293B), 0)
        card.set_style_border_color(lv.color_hex(0x6366F1), 0)
        card.set_style_radius(12, 0)
        card.set_style_pad_all(8, 0)
        self.group = lv.group_create()
        self.shutter = lv.button(card)
        self.shutter.set_size(100, 36)
        self.shutter.set_style_bg_color(lv.color_hex(0x6366F1), 0)
        self.shutter.set_style_radius(10, 0)
        self.group.add_obj(self.shutter)
        self.shutter.add_event_cb(self.capture, lv.EVENT.CLICKED, None)
        self.label = lv.label(card)
        self.has_camera = CameraManager.has_camera()
        self.label.set_text("Ready" if self.has_camera else "No camera")
        self.setContentView(screen)

    def capture(self, event):
        self.update_label("Captured" if self.has_camera else "No camera")

    def update_label(self, value):
        self.label.set_text(value)

    def onStop(self, screen):
        CameraManager.stop()

    def self_test(self):
        before = self.label.get_text()
        self.update_label("Test")
        changed = self.label.get_text() != before
        self.update_label(before)
        return {"changed": changed, "restored": self.label.get_text() == before}
"""



# Identical to CAMERA_APP except for one forbidden board access, so it clears
# every earlier validator and the failure can only come from the hardware gate.
BOARD_BOUND_APP = CAMERA_APP.replace(
    "from mpos import Activity, CameraManager",
    "from machine import Pin\nfrom mpos import Activity, CameraManager",
).replace(
    "        screen = lv.obj()",
    "        screen = lv.obj()\n        self.led = Pin(21, Pin.OUT)",
    1,
)


class CapabilityFlowE2ETests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schemas = {}
        resources = []
        for path in sorted(SCHEMA_DIR.glob("*.schema.json")):
            schema = json.loads(path.read_text(encoding="utf-8"))
            cls.schemas[path.name] = schema
            resources.append((schema["$id"], Resource.from_contents(schema)))
        cls.registry = Registry().with_resources(resources)

    def assert_schema_valid(self, name: str, instance: dict) -> None:
        Draft202012Validator(
            self.schemas[name],
            registry=self.registry,
            format_checker=FormatChecker(),
        ).validate(instance)

    def setUp(self) -> None:
        # ignore_cleanup_errors: on Windows SQLAlchemy can still hold auth.db
        # when the directory is torn down.
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._originals = (
            session_module.SESSION_ROOT,
            main_module.session_service,
            main_module.billing_service,
            main_module.auth_service,
        )
        session_module.SESSION_ROOT = Path(self.temp.name, "sessions").resolve()
        auth = AuthService(f"sqlite:///{Path(self.temp.name, 'auth.db')}")
        self.auth = auth
        main_module.auth_service = auth
        main_module.billing_service = BillingService(engine=auth.engine)
        main_module.session_service = SessionService()
        self.service = main_module.session_service
        # One portal for the whole test. A bare TestClient spins up a fresh
        # anyio portal per request and tears it down on the way out, which
        # cancels the background run task the 202 just started — the pipeline
        # then lands on "cancelled" instead of its real verdict.
        self._stack = contextlib.ExitStack()
        self.client = self._stack.enter_context(TestClient(main_module.app))
        self.prompts: list[str] = []
        self.requests: list[object] = []

    def tearDown(self) -> None:
        self._stack.close()
        self.auth.engine.dispose()
        (
            session_module.SESSION_ROOT,
            main_module.session_service,
            main_module.billing_service,
            main_module.auth_service,
        ) = self._originals
        self.temp.cleanup()

    # ---- helpers ---------------------------------------------------------

    def _login(self, username: str = "e2e-user") -> None:
        response = self.client.post(
            "/api/auth/register",
            json={"username": username, "password": "correct-horse-123"},
        )
        self.assertEqual(response.status_code, 201, response.text)

    def _fake_provider(self, app_code: str = CAMERA_APP):
        """Stand-in for the AI upstream that records the real prompt."""

        async def _call(request, correction="", timeout_seconds=None):
            self.requests.append(request)
            self.prompts.append(
                generator_module._build_user_prompt(request, correction)
            )
            return (
                {
                    "summary": "Camera app",
                    "prompt_normalized_zh": "拍照 App",
                    "prompt_normalized_en": "Camera app",
                    "entrypoint": "app.py",
                    "classname": "GeneratedApp",
                    "app_code": app_code,
                    "acceptance_tests": [
                        "用户可以点击快门按钮",
                        "没有摄像头时界面仍可用",
                    ],
                    "store_metadata": {},
                },
                "fake-model",
                {"provider": "deepseek_primary"},
            )

        return AsyncMock(side_effect=_call)

    def _create_session(self, prompt: str, package: str) -> dict:
        response = self.client.post(
            "/api/sessions",
            json={
                "idempotency_key": f"e2e-create-{package}",
                "prompt": prompt,
                "package_name": package,
                "targets": ["web-preview", "physical-device", "package-only"],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        state = response.json()
        allow = self.client.post(
            f"/api/sessions/{state['session_id']}/permissions/allow-all",
            json={"idempotency_key": f"e2e-allow-{package}"},
        )
        self.assertEqual(allow.status_code, 200, allow.text)
        return allow.json()

    async def _generate(
        self, session_id: str, package: str, app_code: str = CAMERA_APP
    ) -> dict:
        with patch.object(
            generator_module, "_call_deepseek", new=self._fake_provider(app_code)
        ):
            # actions/run is the full pipeline the frontend actually triggers;
            # actions/generate only runs one stage and reports "completed" for
            # that stage alone.
            started = self.client.post(
                f"/api/sessions/{session_id}/actions/run",
                json={"idempotency_key": f"e2e-run-{package}"},
            )
            self.assertEqual(started.status_code, 202, started.text)
            return await self._await_pipeline(session_id)

    async def _await_pipeline(self, session_id: str, timeout_s: float = 90.0) -> dict:
        """Wait for the background run the way the browser does — by polling.

        The task lives in the TestClient's portal loop, not this test's loop, so
        awaiting the Task object directly is a cross-loop await: it appears to
        work until it doesn't. ``/actions/run`` sets "running" synchronously
        before returning 202, so there is no window where this exits early.
        """
        deadline = time.monotonic() + timeout_s
        while True:
            state = self.client.get(f"/api/sessions/{session_id}").json()
            if state["status"] != "running":
                return state
            if time.monotonic() >= deadline:
                self.fail(
                    f"session {session_id} still running after {timeout_s}s"
                )
            await asyncio.sleep(0.05)

    # ---- the flow --------------------------------------------------------

    async def test_hardware_app_carries_its_contract_from_prompt_to_artifacts(
        self,
    ) -> None:
        self._login()
        package = "com.example.e2ecam"
        state = self._create_session("做一个拍照 App，按实体按键拍照", package)

        # 1. The request never named a board, yet the session knows what the
        #    App needs and has no board field at all.
        user_input = state["input"]
        self.assertIn("camera", user_input["required_capabilities"])
        self.assertNotIn("target_board", user_input)
        self.assertTrue(user_input["physical_validation_required"])
        self.assertIn("camera", user_input["runtime_fallbacks"])
        self.assertTrue(state["capability_versions"]["skill_commit"])

        # 2. The contract must actually reach the model. This is the seam that
        #    was inert: the session knew the capabilities, the generator never
        #    received them.
        state = await self._generate(state["session_id"], package)
        self.assertTrue(self.prompts, "the generator was never called")
        prompt = self.prompts[0]
        self.assertIn("CameraManager.has_camera()", prompt)
        self.assertIn("mpos.board", prompt)
        self.assertIn("focus", prompt.lower())
        self.assertIn("camera", self.requests[0].required_capabilities)

        # 3. Generation succeeded through the real gate and validators.
        self.assertIsNotNone(state["generation"], state.get("last_error"))
        self.assertEqual(state["status"], "waiting_preview")

    async def test_preview_limit_is_partial_and_never_repairs_the_app(self) -> None:
        self._login()
        package = "com.example.e2eprev"
        state = self._create_session("做一个拍照 App", package)
        state = await self._generate(state["session_id"], package)
        session_id = state["session_id"]

        # The browser cannot run a camera. That is the preview's limit.
        response = self.client.post(
            f"/api/sessions/{session_id}/actions/preview-result",
            json={
                "idempotency_key": "e2e-preview-partial",
                "result": "partial",
                "message": "no camera in the browser",
                "unsupported_capabilities": ["camera"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        state = self.client.get(f"/api/sessions/{session_id}").json()

        errors = [
            error
            for error in state["structured_errors"]
            if error["code"] == "WEB_PREVIEW_UNSUPPORTED"
        ]
        self.assertTrue(errors)
        self.assertEqual(errors[0]["owner"], "external")
        self.assertFalse(errors[0]["retryable"])
        # Never back to generation, and never silently completed.
        self.assertNotEqual(state["next_phase"], "mpos-gen-app-web")
        self.assertNotEqual(state["status"], "completed")

    async def test_device_without_the_capability_blocks_completion(self) -> None:
        self._login()
        package = "com.example.e2edev"
        state = self._create_session("做一个拍照 App", package)
        state = await self._generate(state["session_id"], package)
        session_id = state["session_id"]

        response = self.client.post(
            f"/api/sessions/{session_id}/devices/result",
            json={
                "idempotency_key": "e2e-device-missing",
                "result": "launch_success",
                "hardware_id": "brand-new-board-2027",
                "runtime_capability_results": [
                    {
                        "capability": "camera",
                        "available": False,
                        "probe": "CameraManager.has_camera()",
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        state = self.client.get(f"/api/sessions/{session_id}").json()

        unavailable = [
            error
            for error in state["structured_errors"]
            if error["code"] == "HARDWARE_CAPABILITY_UNAVAILABLE"
        ]
        self.assertTrue(unavailable)
        self.assertEqual(unavailable[0]["owner"], "device")
        self.assertFalse(unavailable[0]["retryable"])
        self.assertNotEqual(state["status"], "completed")

    async def test_device_evidence_is_persisted_and_schema_valid(self) -> None:
        self._login()
        package = "com.example.e2eok"
        state = self._create_session("做一个拍照 App", package)
        state = await self._generate(state["session_id"], package)
        session_id = state["session_id"]

        response = self.client.post(
            f"/api/sessions/{session_id}/devices/result",
            json={
                "idempotency_key": "e2e-device-ok",
                "result": "launch_success",
                "hardware_id": "brand-new-board-2027",
                "runtime_capability_results": [
                    {
                        "capability": "camera",
                        "available": True,
                        "probe": "CameraManager.has_camera()",
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        state = self.client.get(f"/api/sessions/{session_id}").json()

        self.assertEqual(state["detected_hardware_id"], "brand-new-board-2027")
        self.assertTrue(state["runtime_capability_results"][0]["available"])
        self.assertFalse(
            [
                error
                for error in state["structured_errors"]
                if error["code"] == "HARDWARE_CAPABILITY_UNAVAILABLE"
            ]
        )
        # An unlisted board that probes fine stays valid, with an advisory note.
        self.assertTrue(
            any("brand-new-board-2027" in item for item in state["warnings"])
        )

        deploy_artifact = next(
            item for item in state["artifacts"] if item["role"] == "deploy_result"
        )
        deploy_result = json.loads(
            Path(
                session_module.SESSION_ROOT, session_id, deploy_artifact["path"]
            ).read_text(encoding="utf-8")
        )
        self.assert_schema_valid("deploy-result.schema.json", deploy_result)
        self.assertEqual(deploy_result["hardware_id"], "brand-new-board-2027")
        self.assertTrue(deploy_result["runtime_capability_results"])
        self.assertTrue(deploy_result["board_metadata_is_advisory"])

    def test_capability_surface_reports_the_pinned_snapshot(self) -> None:
        """The runner surface must name the snapshot a generation ran against.

        Without this the endpoint can lose the field and nothing notices — the
        same way the capability wiring was inert while every unit test passed.
        """
        self._login()
        response = self.client.get("/api/capabilities")
        self.assertEqual(response.status_code, 200, response.text)
        reference = response.json()["capability_reference"]
        self.assertEqual(
            reference["board_capabilities_schema"], "mpos-board-capabilities-v1"
        )
        self.assertIn("camera", reference["capability_names"])
        self.assertIn("skill_commit", reference)

    async def test_probe_evidence_survives_a_later_report_that_carries_none(
        self,
    ) -> None:
        """A reload empties the browser's probe state; the server must not follow.

        The browser posts whatever it holds in memory with every device report.
        After a page reload that is an empty list, and replacing the stored
        evidence with it silently re-blocks a session the device already
        cleared — with no error anyone could act on.
        """
        self._login()
        package = "com.example.e2emerge"
        state = self._create_session("做一个拍照 App", package)
        state = await self._generate(state["session_id"], package)
        session_id = state["session_id"]

        probed = self.client.post(
            f"/api/sessions/{session_id}/devices/result",
            json={
                "idempotency_key": "e2e-merge-probe",
                "result": "probe_success",
                "hardware_id": "board-under-test",
                "runtime_capability_results": [
                    {
                        "capability": "camera",
                        "available": True,
                        "probe": "CameraManager.has_camera()",
                    }
                ],
            },
        )
        self.assertEqual(probed.status_code, 200, probed.text)
        state = self.client.get(f"/api/sessions/{session_id}").json()
        self.assertTrue(state["runtime_capability_results"][0]["available"])

        # A bare probe now writes a deploy_result of its own, so that artifact
        # has to satisfy the protocol schema without an install behind it.
        probe_artifact = next(
            item for item in state["artifacts"] if item["role"] == "deploy_result"
        )
        probe_result = json.loads(
            Path(
                session_module.SESSION_ROOT, session_id, probe_artifact["path"]
            ).read_text(encoding="utf-8")
        )
        self.assert_schema_valid("deploy-result.schema.json", probe_result)
        self.assertEqual(probe_result["mode"], "webserial")
        self.assertFalse(probe_result["app_installed"])

        # Same session, fresh page: the install reports no probes at all.
        installed = self.client.post(
            f"/api/sessions/{session_id}/devices/result",
            json={
                "idempotency_key": "e2e-merge-install",
                "result": "install_success",
                "hardware_id": "board-under-test",
                "runtime_capability_results": [],
            },
        )
        self.assertEqual(installed.status_code, 200, installed.text)
        state = self.client.get(f"/api/sessions/{session_id}").json()

        evidence = {
            item["capability"]: item
            for item in state["runtime_capability_results"]
        }
        self.assertTrue(evidence["camera"]["available"])
        self.assertFalse(
            [
                error
                for error in state["structured_errors"]
                if error["code"] == "HARDWARE_CAPABILITY_UNAVAILABLE"
            ]
        )

    async def test_non_portable_capability_fails_the_session_loudly(self) -> None:
        self._login()
        package = "com.example.e2egps"
        state = self._create_session("做一个 GPS 定位 App，显示经纬度", package)
        self.assertIn("gps", state["input"]["required_capabilities"])

        state = await self._generate(state["session_id"], package)

        # No fabricated GPS code, and the gap is owned by MicroPythonOS.
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["last_error"]["code"], "MPOS_CAPABILITY_API_MISSING")
        self.assertEqual(state["last_error"]["owner"], "micropythonos")
        self.assertFalse(state["last_error"]["retryable"])
        self.assertFalse(self.prompts, "a blocked capability must not reach the model")

    async def test_plain_ui_app_is_unaffected_by_the_capability_layer(self) -> None:
        self._login()
        package = "com.example.e2eplain"
        state = self._create_session("做一个简单的计数器", package)
        self.assertEqual(state["input"]["required_capabilities"], [])
        self.assertFalse(state["input"]["physical_validation_required"])

        state = await self._generate(state["session_id"], package)
        self.assertIsNotNone(state["generation"], state.get("last_error"))
        # No hardware contract text is injected for a plain UI App.
        self.assertNotIn("抽象硬件能力", self.prompts[0])

    async def test_accessory_wording_cannot_waive_the_hardware_gate(self) -> None:
        """The board-hardware gate is mandatory and has no keyword escape hatch.

        Wiring allow_direct_hardware to detected accessory phrases turned the
        whole gate off for any prompt containing a word like "外接", which is
        exactly the bypass the spec forbids.
        """
        self._login()
        package = "com.example.e2egate"
        state = self._create_session("做一个外接传感器的显示 App", package)
        self.assertTrue(state["input"]["required_accessories"])

        state = await self._generate(
            state["session_id"], package, app_code=BOARD_BOUND_APP
        )

        self.assertEqual(state["status"], "failed")
        self.assertIsNone(state["generation"])
        self.assertIn("machine.Pin", state["last_error"]["message"])


if __name__ == "__main__":
    sys.exit(unittest.main())
