import tempfile
import unittest
from pathlib import Path

from app.generator import GenerationError, _validate_code
from app.models import (
    DeviceResultRequest,
    PermissionBatchDecisionRequest,
    PreviewResultRequest,
    SessionCreateRequest,
)
from app.runner_services import hardware_capability_registry, script_dispatcher
import app.session_service as session_module


VALID_APP = """import lvgl as lv
from mpos import Activity

class GeneratedApp(Activity):
    def onCreate(self):
        screen = lv.obj()
        self.setContentView(screen)

    def exercise(self):
        self.value = getattr(self, 'value', 0) + 1

    def self_test(self):
        before = getattr(self, 'value', 0)
        self.exercise()
        after = self.value
        return {'changed': after != before, 'advanced': after > before}
"""


class HardwareCapabilityTests(unittest.TestCase):
    def test_registry_resolves_portable_contract_and_blocks_missing_api(self) -> None:
        portable = hardware_capability_registry.resolve(["camera"], {"camera": "show unavailable"})
        self.assertEqual(portable["status"], "portable")
        self.assertTrue(portable["physical_validation_required"])
        self.assertEqual(hardware_capability_registry.resolve(["storage.sdcard"])["status"], "partial")
        blocked = hardware_capability_registry.resolve(["gps"])
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["error"]["code"], "MPOS_CAPABILITY_API_MISSING")
        self.assertFalse(blocked["error"]["retryable"])

    def test_capability_terms_do_not_match_inside_longer_words(self) -> None:
        # Substring matching read "mic" out of "dynamic"/"ceramic", "imu" out of
        # "simulator" and "lora" out of "flora". The last one blocked generation
        # outright, because LoRa has no portable API.
        for prompt in (
            "a dynamic dashboard with live charts",
            "ceramic tile inventory counter",
            "a traffic light simulator",
            "flora encyclopedia browser",
            "retouch a photo in the browser",
        ):
            self.assertEqual(hardware_capability_registry.infer(prompt), [], prompt)

    def test_non_portable_capability_needs_more_than_a_topic_word(self) -> None:
        # "定位" is widget layout far more often than it is GNSS, and guessing
        # wrong is not recoverable: the contract resolves to blocked with a
        # non-retryable MPOS_CAPABILITY_API_MISSING.
        self.assertEqual(
            hardware_capability_registry.infer("CSS 定位练习：把方块定位到右下角"), []
        )
        self.assertEqual(hardware_capability_registry.infer("用 GPS 记录轨迹"), ["gps"])

    def test_guessed_capability_does_not_demand_a_real_device(self) -> None:
        # A photo album that displays a QR code image mentions the camera, but
        # the user never asked to take a picture; requiring hardware validation
        # for that is a blocker they cannot clear.
        sources = hardware_capability_registry.classify("显示二维码图片的相册")
        self.assertEqual(sources, {"camera": "inferred"})
        guessed = hardware_capability_registry.resolve(["camera"], {}, sources)
        self.assertFalse(guessed["physical_validation_required"])
        # Naming the camera outright still demands the device.
        asked = hardware_capability_registry.classify("用摄像头拍照并保存")
        self.assertEqual(asked["camera"], "strong")
        self.assertTrue(hardware_capability_registry.resolve(["camera"], {}, asked)["physical_validation_required"])
        # And an explicitly declared capability is never treated as a guess.
        self.assertTrue(hardware_capability_registry.resolve(["camera"], {}, {})["physical_validation_required"])

    def test_wiring_language_counts_as_evidence_for_a_portable_capability(self) -> None:
        sources = hardware_capability_registry.classify("接在开发板上的 RGB 灯带做呼吸灯")
        self.assertEqual(sources, {"lights.rgb": "qualified"})
        self.assertTrue(
            hardware_capability_registry.resolve(["lights.rgb"], {}, sources)["physical_validation_required"]
        )

    def test_prompt_inference_and_session_checkpoint_preserve_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous_root = session_module.SESSION_ROOT
            session_module.SESSION_ROOT = Path(directory).resolve()
            try:
                service = session_module.SessionService()
                state = service.create(SessionCreateRequest(
                    idempotency_key="capability-create-0001",
                    prompt="做一个能拍照并显示电量的应用",
                    package_name="com.example.capabilities",
                ))
                self.assertEqual(state["required_capabilities"], ["camera", "battery"])
                checkpoint = service._checkpoint_record(state, "created", "mpos-analyze-app-web")
                self.assertEqual(checkpoint["required_capabilities"], ["camera", "battery"])
                self.assertEqual(checkpoint["board_capabilities_schema"]["schema_version"], "mpos-board-capabilities-v1")
            finally:
                session_module.SESSION_ROOT = previous_root

    def test_direct_hardware_access_is_rejected_by_both_gates(self) -> None:
        with self.assertRaises(GenerationError):
            _validate_code("import machine\n" + VALID_APP)
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            app = repo / "internal_filesystem" / "apps" / "com.example.direct" / "assets"
            app.mkdir(parents=True)
            (app / "main.py").write_text("from machine import Pin\npin = Pin(1)\n", encoding="utf-8")
            result = script_dispatcher.run_hardware_policy(repo, "com.example.direct")
            if result.get("error", {}).get("code") == "TOOLCHAIN_MISSING":
                self.skipTest("hardware policy toolchain is not available in this checkout")
            self.assertFalse(result["ok"])
            self.assertEqual(result["result"]["result"], "failed")
            self.assertEqual(result["result"]["errors"][0]["code"], "DIRECT_HARDWARE_ACCESS_FORBIDDEN")

    def test_preview_partial_and_device_probe_keep_capability_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous_root = session_module.SESSION_ROOT
            session_module.SESSION_ROOT = Path(directory).resolve()
            try:
                service = session_module.SessionService()
                state = service.create(SessionCreateRequest(
                    idempotency_key="capability-preview-0001",
                    prompt="Build a camera app",
                    package_name="com.example.camera",
                    targets=["web-preview", "physical-device"],
                    required_capabilities=["camera"],
                    required_accessories=["camera module"],
                ))
                partial = service.preview_result(state["session_id"], PreviewResultRequest(
                    idempotency_key="capability-preview-result-0001",
                    result="partial",
                    message="camera is unavailable in the browser",
                ))
                self.assertEqual(partial["last_error"]["code"], "WEB_PREVIEW_UNSUPPORTED")
                self.assertFalse(partial["last_error"]["retryable"])

                recorded = service.record_device_result(state["session_id"], DeviceResultRequest(
                    idempotency_key="capability-device-result-0001",
                    result="failed",
                    error_code="HARDWARE_CAPABILITY_UNAVAILABLE",
                    detected_hardware_id="usb:1234:5678",
                    runtime_capability_results={"camera": False},
                ))
                self.assertEqual(recorded["last_error"]["code"], "HARDWARE_CAPABILITY_UNAVAILABLE")
                artifact = Path(directory) / state["session_id"] / "artifacts" / "deploy_result.json"
                payload = __import__("json").loads(artifact.read_text(encoding="utf-8"))
                self.assertEqual(payload["detected_hardware_id"], "usb:1234:5678")
                self.assertIs(payload["runtime_capability_results"]["camera"], False)
            finally:
                session_module.SESSION_ROOT = previous_root

    def test_sensitive_hardware_permissions_require_separate_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous_root = session_module.SESSION_ROOT
            session_module.SESSION_ROOT = Path(directory).resolve()
            try:
                service = session_module.SessionService()
                state = service.create(SessionCreateRequest(
                    idempotency_key="capability-permission-0001",
                    prompt="Build a voice recorder",
                    package_name="com.example.microphone",
                    required_capabilities=["audio.input"],
                    required_accessories=["microphone"],
                ))
                state = service.allow_all_permissions(state["session_id"], PermissionBatchDecisionRequest(
                    idempotency_key="capability-permission-batch-0001",
                ))
                sensitive = [item for item in state["permissions"] if item.get("separate_confirmation")]
                self.assertEqual({item["permission_type"] for item in sensitive}, {"microphone_access", "external_wiring"})
                self.assertTrue(all(item["decision"] == "pending" for item in sensitive))
                self.assertEqual(state["status"], "blocked")
            finally:
                session_module.SESSION_ROOT = previous_root


if __name__ == "__main__":
    unittest.main()
