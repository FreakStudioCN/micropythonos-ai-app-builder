"""Cross-device capability contract tests.

Covers the acceptance criteria in docs/cross-device-capability-integration.md:
no board selector, portable capabilities get probes and fallbacks, non-portable
ones stop generation instead of faking code, preview limits never start a
repair loop, and an unlisted board that probes fine is still a valid device.
"""

import unittest

from app.capabilities import (
    allows_code_repair,
    capability_error,
    capability_index,
    capability_versions,
    probe_free_names,
)
from app.capability_extraction import analyze_requirements, extract_capabilities
from app.capability_policy import (
    check_focus_navigation,
    check_lifecycle_cleanup,
    check_runtime_fallbacks,
    evaluate_generated_app,
)
from app.device_service import device_service
from app.generation_prompts import capability_contract_prompt
from app.models import (
    CapabilityProbeResult,
    DeviceResultRequest,
    PreviewResultRequest,
    SessionCreateRequest,
)

PORTABLE_APP = """
import lvgl as lv
from mpos import Activity, CameraManager

class GeneratedApp(Activity):
    def onCreate(self):
        screen = lv.obj()
        self.group = lv.group_create()
        self.button = lv.button(screen)
        self.group.add_obj(self.button)
        self.button.add_event_cb(self.shoot, lv.EVENT.CLICKED, None)
        self.ready = CameraManager.has_camera()
        self.setContentView(screen)

    def shoot(self, event):
        pass

    def onStop(self, screen):
        CameraManager.stop()
"""

BOARD_BOUND_APP = """
import lvgl as lv
import mpos.board
from machine import Pin, I2C
from mpos import Activity

class GeneratedApp(Activity):
    def onCreate(self):
        self.led = Pin(21, Pin.OUT)
        self.bus = I2C(0)
        screen = lv.obj()
        self.setContentView(screen)
"""

POINTER_ONLY_APP = """
import lvgl as lv
from mpos import Activity

class GeneratedApp(Activity):
    def onCreate(self):
        screen = lv.obj()
        button = lv.button(screen)
        button.add_event_cb(self.tap, lv.EVENT.CLICKED, None)
        self.setContentView(screen)

    def tap(self, event):
        pass
"""


class CapabilitySnapshotTests(unittest.TestCase):
    def test_board_selection_is_never_required(self) -> None:
        index = capability_index()
        self.assertFalse(index.board_selection_required)
        self.assertTrue(index.unknown_board_allowed)
        self.assertTrue(index.selection_policy["runtime_probe_authoritative"])

    def test_requests_have_no_target_board_field(self) -> None:
        request = SessionCreateRequest(idempotency_key="abcdefgh", prompt="拍照 app")
        self.assertNotIn("target_board", request.model_dump())
        self.assertIn("required_capabilities", request.model_dump())

    def test_versions_are_pinned_for_resume(self) -> None:
        versions = capability_versions()
        self.assertEqual(
            versions["board_capabilities_schema"], "mpos-board-capabilities-v1"
        )
        for key in ("skill_commit", "mpos_commit"):
            self.assertTrue(versions[key])

    def test_non_portable_capabilities_report_an_os_gap(self) -> None:
        index = capability_index()
        for name in ("gps", "infrared", "lora", "sensor.environmental"):
            contract = index.contract(name)
            self.assertFalse(contract.generatable, name)
            self.assertEqual(
                contract.blocking_error_code(), "MPOS_CAPABILITY_API_MISSING", name
            )

    def test_only_generator_faults_may_be_auto_repaired(self) -> None:
        self.assertTrue(allows_code_repair("DIRECT_HARDWARE_ACCESS_FORBIDDEN"))
        for code in (
            "WEB_PREVIEW_UNSUPPORTED",
            "HARDWARE_CAPABILITY_UNAVAILABLE",
            "MPOS_CAPABILITY_API_MISSING",
        ):
            self.assertFalse(allows_code_repair(code), code)

    def test_error_owner_and_retryable_follow_the_code_table(self) -> None:
        cases = {
            "WEB_PREVIEW_UNSUPPORTED": ("external", False),
            "HARDWARE_CAPABILITY_UNAVAILABLE": ("device", False),
            "MPOS_CAPABILITY_API_MISSING": ("micropythonos", False),
            "DIRECT_HARDWARE_ACCESS_FORBIDDEN": ("skill", True),
        }
        for code, (owner, retryable) in cases.items():
            error = capability_error(code, "x", stage="test", capability="camera")
            self.assertEqual(error["owner"], owner, code)
            self.assertEqual(error["retryable"], retryable, code)


class CapabilityExtractionTests(unittest.TestCase):
    def test_hardware_requests_need_no_board_mention(self) -> None:
        analysis = analyze_requirements("做一个拍照 App，按实体按键拍照")
        self.assertIn("camera", analysis["required_capabilities"])
        self.assertIn("input.keypad", analysis["required_capabilities"])
        self.assertTrue(analysis["physical_validation_required"])
        self.assertIn("camera", analysis["runtime_fallbacks"])

    def test_wiring_language_demands_real_device_validation(self) -> None:
        # Saying where to wire an LED is physical intent; a simulator that
        # happens to mention lights is not. Both directions matter: the first
        # miss ships an unvalidated hardware App, the second hands the session a
        # completion blocker that nothing the user does can clear.
        wired = analyze_requirements("做一个 LED 呼吸灯，接在开发板的 RGB 灯上")
        self.assertEqual(wired["capability_sources"]["lights.rgb"], "qualified")
        self.assertTrue(wired["physical_validation_required"])

        for mock in ("做一个红绿灯模拟器", "模拟开发板上的红绿灯"):
            analysis = analyze_requirements(mock)
            self.assertEqual(
                analysis["capability_sources"]["lights.rgb"], "inferred", mock
            )
            self.assertFalse(analysis["physical_validation_required"], mock)

    def test_ui_words_do_not_imply_hardware(self) -> None:
        # "location"/"coordinates"/"highlight" describe layout, not GPS or LEDs.
        # A false positive here would block generation on a plain UI App.
        found = extract_capabilities(
            "A ledger that highlights the location coordinates of each row"
        )
        self.assertEqual(found, [])

    def test_onboard_hardware_is_not_an_accessory(self) -> None:
        analysis = analyze_requirements("用板载摄像头拍照")
        self.assertIn("camera", analysis["required_capabilities"])
        self.assertEqual(analysis["required_accessories"], [])

    def test_explicit_external_part_is_recorded_as_an_accessory(self) -> None:
        analysis = analyze_requirements("我要外接一个 I2C module 读数据")
        self.assertTrue(analysis["required_accessories"])

    def test_non_portable_request_is_reported_not_invented(self) -> None:
        # A qualified hardware request must stop rather than fake the code.
        analysis = analyze_requirements("读取板载温湿度传感器")
        codes = [item["code"] for item in analysis["blocking_capabilities"]]
        self.assertIn("MPOS_CAPABILITY_API_MISSING", codes)

    def test_unambiguous_hardware_noun_blocks_without_a_qualifier(self) -> None:
        # "GPS" is itself a hardware noun; it needs no extra evidence.
        analysis = analyze_requirements("做一个 GPS 定位 App，显示经纬度")
        self.assertEqual(
            [item["capability"] for item in analysis["blocking_capabilities"]],
            ["gps"],
        )

    def test_topic_word_alone_never_blocks_a_plain_ui_app(self) -> None:
        # A unit converter and a UI mock are not hardware requests. Blocking
        # these aborted generation before the model was ever called.
        for prompt in ("温度换算器", "虚拟遥控器界面", "显示温度曲线的图表界面"):
            analysis = analyze_requirements(prompt)
            self.assertEqual(analysis["blocking_capabilities"], [], prompt)

    def test_model_proposed_unknown_capability_is_rejected(self) -> None:
        analysis = analyze_requirements(
            "做个计时器", model_capabilities=["telepathy", "camera"]
        )
        self.assertIn("telepathy", analysis["unrecognized_capabilities"])
        self.assertNotIn("telepathy", analysis["required_capabilities"])


class GenerationPolicyTests(unittest.TestCase):
    def test_portable_app_passes_the_hardware_gate(self) -> None:
        verdict = evaluate_generated_app(
            PORTABLE_APP,
            capabilities=["camera"],
            app_fullname="com.example.cam",
        )
        self.assertTrue(verdict["passed"], verdict["errors"])

    def test_board_module_and_machine_constructors_are_rejected(self) -> None:
        verdict = evaluate_generated_app(
            BOARD_BOUND_APP,
            capabilities=["lights.rgb"],
            app_fullname="com.example.bad",
        )
        self.assertFalse(verdict["passed"])
        symbols = {error["details"]["symbol"] for error in verdict["errors"]}
        self.assertIn("mpos.board", symbols)
        self.assertIn("machine.Pin", symbols)
        self.assertIn("machine.I2C", symbols)
        for error in verdict["errors"]:
            self.assertEqual(error["code"], "DIRECT_HARDWARE_ACCESS_FORBIDDEN")

    def test_confirmed_accessory_may_use_the_low_level_exception(self) -> None:
        verdict = evaluate_generated_app(
            BOARD_BOUND_APP,
            capabilities=[],
            app_fullname="com.example.accessory",
            allow_direct_hardware=True,
        )
        self.assertTrue(verdict["passed"])

    def test_missing_runtime_probe_is_flagged(self) -> None:
        warnings = check_runtime_fallbacks(POINTER_ONLY_APP, ["camera"])
        self.assertTrue(any("camera" in item for item in warnings))

    def test_pointer_only_interaction_is_flagged(self) -> None:
        self.assertTrue(check_focus_navigation(POINTER_ONLY_APP))

    def test_focus_navigation_path_passes(self) -> None:
        self.assertEqual(check_focus_navigation(PORTABLE_APP), [])

    def test_stateful_hardware_without_cleanup_is_flagged(self) -> None:
        self.assertTrue(check_lifecycle_cleanup(POINTER_ONLY_APP, ["camera"]))

    def test_stateful_hardware_with_cleanup_passes(self) -> None:
        self.assertEqual(check_lifecycle_cleanup(PORTABLE_APP, ["camera"]), [])

    def test_prompt_carries_the_contract_not_a_hand_written_list(self) -> None:
        prompt = capability_contract_prompt(["camera", "gps"])
        self.assertIn("CameraManager.has_camera()", prompt)
        self.assertIn("MPOS_CAPABILITY_API_MISSING", prompt)
        self.assertIn("mpos.board", prompt)
        self.assertEqual(capability_contract_prompt([]), "")


class PreviewLimitTests(unittest.TestCase):
    def test_partial_is_an_accepted_preview_outcome(self) -> None:
        request = PreviewResultRequest(
            idempotency_key="abcdefgh",
            result="partial",
            unsupported_capabilities=["camera"],
        )
        self.assertEqual(request.result, "partial")
        self.assertEqual(request.unsupported_capabilities, ["camera"])

    def test_camera_is_known_to_be_unrunnable_in_preview(self) -> None:
        analysis = analyze_requirements("拍照 App")
        self.assertIn("camera", analysis["web_preview_unsupported"])

    def test_preview_limit_never_authorises_a_code_repair(self) -> None:
        error = capability_error(
            "WEB_PREVIEW_UNSUPPORTED", "x", stage="test", capability="camera"
        )
        self.assertFalse(error["retryable"])
        self.assertFalse(allows_code_repair(error["code"]))


class DeviceProbeTests(unittest.TestCase):
    def test_templated_probe_is_not_auto_executable(self) -> None:
        # sensor.imu ships a probe that still takes a parameter.
        self.assertEqual(
            probe_free_names("SensorManager.get_default_sensor(sensor_type) is not None"),
            ["sensor_type"],
        )
        self.assertEqual(probe_free_names("CameraManager.has_camera()"), [])

    def test_available_capability_produces_no_error(self) -> None:
        verdict = device_service.evaluate_probe_results(
            required_capabilities=["camera"],
            results=[CapabilityProbeResult(capability="camera", available=True)],
            hardware_id="waveshare-esp32-s3-touch-lcd-2",
        )
        self.assertEqual(verdict["errors"], [])
        self.assertTrue(verdict["runtime_capability_results"][0]["available"])

    def test_absent_capability_is_owned_by_the_device(self) -> None:
        verdict = device_service.evaluate_probe_results(
            required_capabilities=["camera"],
            results=[CapabilityProbeResult(capability="camera", available=False)],
            hardware_id="some-board",
        )
        self.assertEqual(
            [item["code"] for item in verdict["errors"]],
            ["HARDWARE_CAPABILITY_UNAVAILABLE"],
        )
        self.assertEqual(verdict["errors"][0]["owner"], "device")

    def test_unmeasured_probe_is_not_reported_as_missing_hardware(self) -> None:
        verdict = device_service.evaluate_probe_results(
            required_capabilities=["sensor.imu"],
            results=[
                CapabilityProbeResult(
                    capability="sensor.imu",
                    available=None,
                    detail="NameError: sensor_type",
                )
            ],
            hardware_id="some-board",
        )
        self.assertEqual(verdict["errors"], [])
        self.assertTrue(verdict["warnings"])

    def test_unknown_board_that_probes_successfully_is_accepted(self) -> None:
        verdict = device_service.evaluate_probe_results(
            required_capabilities=["camera"],
            results=[CapabilityProbeResult(capability="camera", available=True)],
            hardware_id="brand-new-board-2027",
        )
        self.assertEqual(verdict["errors"], [])
        self.assertTrue(any("brand-new-board-2027" in w for w in verdict["warnings"]))

    def test_listed_board_probing_fine_produces_no_drift_noise(self) -> None:
        # boards[].os_registrations is a coarser, differently-named list than
        # feature_contracts: no board registers "network" at all. Comparing
        # membership across the two namespaces warned on every correct device.
        verdict = device_service.evaluate_probe_results(
            required_capabilities=["network", "input.keypad"],
            results=[
                CapabilityProbeResult(capability="network", available=True),
                CapabilityProbeResult(capability="input.keypad", available=True),
            ],
            hardware_id="m5stack_core2",
        )
        self.assertEqual(verdict["errors"], [])
        self.assertEqual(verdict["warnings"], [])

    def test_static_metadata_is_only_advisory(self) -> None:
        verdict = device_service.evaluate_probe_results(
            required_capabilities=[],
            results=[],
            hardware_id="",
        )
        self.assertTrue(verdict["board_metadata_is_advisory"])

    def test_device_result_has_no_board_default(self) -> None:
        request = DeviceResultRequest(
            idempotency_key="abcdefgh", result="probe_success"
        )
        payload = request.model_dump()
        self.assertNotIn("board", payload)
        self.assertEqual(payload["hardware_id"], "")




class GenerationPathTests(unittest.IsolatedAsyncioTestCase):
    """The gate must sit in the real generate_app path, not merely exist."""

    async def test_non_portable_capability_stops_before_any_model_call(self) -> None:
        from unittest.mock import AsyncMock, patch

        from app.generator import ApiValidationError, generate_app
        from app.models import GenerateRequest

        request = GenerateRequest(
            prompt="显示当前温度",
            package_name="com.example.temp",
            required_capabilities=["sensor.environmental"],
        )
        with patch("app.generator._call_deepseek", new=AsyncMock()) as upstream:
            with self.assertRaises(ApiValidationError) as caught:
                await generate_app(request)
        self.assertEqual(caught.exception.code, "MPOS_CAPABILITY_API_MISSING")
        upstream.assert_not_awaited()

    async def test_capability_contract_reaches_the_model_prompt(self) -> None:
        from app.generator import _build_user_prompt
        from app.models import GenerateRequest

        prompt = _build_user_prompt(
            GenerateRequest(
                prompt="拍照 App",
                package_name="com.example.cam",
                required_capabilities=["camera"],
            )
        )
        self.assertIn("CameraManager.has_camera()", prompt)
        self.assertIn("mpos.board", prompt)

    async def test_plain_ui_app_prompt_is_left_alone(self) -> None:
        from app.generator import _build_user_prompt
        from app.models import GenerateRequest

        prompt = _build_user_prompt(
            GenerateRequest(prompt="做一个计数器", package_name="com.example.counter")
        )
        self.assertNotIn("抽象硬件能力", prompt)


if __name__ == "__main__":
    unittest.main()
