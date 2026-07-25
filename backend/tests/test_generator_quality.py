import base64
import io
import json
import unittest
import zipfile
from unittest.mock import AsyncMock, patch

from app.generator import (
    ApiValidationError,
    GenerationError,
    SYSTEM_PROMPT,
    _build_mpk,
    _build_correction,
    _normalize_generation_payload,
    _build_user_prompt,
    _normalize_lvgl_code,
    _parse_model_json,
    _validate_api_summaries,
    _validate_code,
    _validate_product_contract,
    _validate_visual_contract,
    generate_app,
)
from app.models import GenerateRequest


STYLED_APP = """
import lvgl as lv
from mpos import Activity

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
        button = lv.button(card)
        button.set_size(100, 36)
        button.set_style_bg_color(lv.color_hex(0x6366F1), 0)
        button.set_style_radius(10, 0)
        self.label = lv.label(card)
        self.label.set_text("Ready")
        self.setContentView(screen)

    def update_label(self, value):
        self.label.set_text(value)

    def self_test(self):
        before = self.label.get_text()
        self.update_label("Test")
        changed = self.label.get_text() != before
        self.update_label(before)
        return {"changed": changed, "restored": self.label.get_text() == before}
"""


class GeneratorQualityTests(unittest.TestCase):
    def test_model_json_parser_accepts_markdown_fence(self) -> None:
        parsed = _parse_model_json(
            {
                "content": (
                    "```json\n"
                    '{"summary":"番茄钟","app_code":"print(1)",'
                    '"acceptance_tests":["a","b"]}\n'
                    "```"
                )
            }
        )
        self.assertEqual(parsed["summary"], "番茄钟")

    def test_model_json_parser_accepts_prose_and_multipart_content(self) -> None:
        parsed = _parse_model_json(
            {
                "content": [
                    {"type": "text", "text": "生成结果如下：\n"},
                    {
                        "type": "text",
                        "text": (
                            '{"summary":"Timer","app_code":"print(1)",'
                            '"acceptance_tests":["a","b"]}'
                        ),
                    },
                ]
            }
        )
        self.assertEqual(parsed["summary"], "Timer")

    def test_model_json_parser_falls_back_to_tool_arguments(self) -> None:
        parsed = _parse_model_json(
            {
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "arguments": (
                                '{"summary":"Timer","app_code":"print(1)",'
                                '"acceptance_tests":["a","b"]}'
                            )
                        }
                    }
                ],
            }
        )
        self.assertEqual(parsed["summary"], "Timer")

    def test_generation_payload_accepts_code_alias_and_nested_tests(self) -> None:
        normalized = _normalize_generation_payload(
            {
                "result": {
                    "code": "print('timer')",
                    "tests": ["starts", "stops"],
                    "summary": "Timer",
                }
            }
        )
        self.assertEqual(normalized["app_code"], "print('timer')")
        self.assertEqual(normalized["acceptance_tests"], ["starts", "stops"])
        self.assertEqual(normalized["summary"], "Timer")

    def test_generation_payload_accepts_files_dictionary(self) -> None:
        normalized = _normalize_generation_payload(
            {
                "files": {
                    "assets/main.py": {"content": "print('calendar')"},
                    "README.md": "calendar",
                },
                "acceptance_criteria": ["changes month", "selects date"],
            }
        )
        self.assertEqual(normalized["app_code"], "print('calendar')")
        self.assertEqual(
            normalized["acceptance_tests"],
            ["changes month", "selects date"],
        )

    def test_generation_payload_accepts_files_list(self) -> None:
        normalized = _normalize_generation_payload(
            {
                "data": {
                    "files": [
                        {"path": "README.md", "content": "ignore"},
                        {"path": "app.py", "content": "print('ready')"},
                    ],
                    "test_cases": ["opens", "responds"],
                }
            }
        )
        self.assertEqual(normalized["app_code"], "print('ready')")
        self.assertEqual(normalized["acceptance_tests"], ["opens", "responds"])

    def test_mpk_starts_with_explicit_package_directory(self) -> None:
        package_name = "com.example.calendar"
        encoded = _build_mpk(
            package_name,
            {"fullname": package_name},
            "print('calendar')",
        )
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(encoded))) as archive:
            names = archive.namelist()
            self.assertEqual(names[0], f"{package_name}/")
            self.assertTrue(archive.getinfo(names[0]).is_dir())
            self.assertEqual(names[1], f"{package_name}/assets/")
            self.assertIn(f"{package_name}/MANIFEST.JSON", names)
            self.assertIn(f"{package_name}/META-INF/MANIFEST.JSON", names)
            self.assertIn(f"{package_name}/icon_64x64.png", names)
            self.assertIn(
                f"{package_name}/res/mipmap-mdpi/icon_64x64.png",
                names,
            )
            self.assertIn(f"{package_name}/assets/main.py", names)
            icon = archive.read(f"{package_name}/icon_64x64.png")
            self.assertEqual(icon[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(int.from_bytes(icon[16:20], "big"), 64)
            self.assertEqual(int.from_bytes(icon[20:24], "big"), 64)

    def test_flex_flow_selector_is_removed_for_current_lvgl_binding(self) -> None:
        source = "grid.set_flex_flow(lv.FLEX_FLOW.ROW_WRAP, 0)"
        normalized, warnings = _normalize_lvgl_code(source)
        self.assertEqual(
            normalized,
            "grid.set_flex_flow(lv.FLEX_FLOW.ROW_WRAP)",
        )
        self.assertTrue(any("set_flex_flow" in item for item in warnings))

    def test_legacy_align_with_base_widget_is_converted_to_align_to(self) -> None:
        source = (
            "timer_label.align(card, lv.ALIGN.CENTER, 0, -20)\n"
            "status_label.align(card, lv.ALIGN.CENTER, 0, 20)"
        )
        normalized, warnings = _normalize_lvgl_code(source)
        self.assertEqual(
            normalized,
            "timer_label.align_to(card, lv.ALIGN.CENTER, 0, -20)\n"
            "status_label.align_to(card, lv.ALIGN.CENTER, 0, 20)",
        )
        self.assertTrue(any("align_to" in item for item in warnings))

    def test_unconvertible_four_argument_align_is_rejected(self) -> None:
        bad = STYLED_APP.replace(
            "        self.label.set_text(\"Ready\")",
            "        self.label.set_text(\"Ready\")\n"
            "        self.label.align(make_base(), lv.ALIGN.CENTER, 0, 0)",
        )
        with self.assertRaisesRegex(GenerationError, "align_to"):
            _validate_code(bad)

    def test_finite_calculator_parsing_while_is_allowed(self) -> None:
        finite = STYLED_APP.replace(
            "    def update_label(self, value):",
            "    def parse_tokens(self, tokens):\n"
            "        index = 0\n"
            "        total = 0\n"
            "        while index < len(tokens):\n"
            "            total += tokens[index]\n"
            "            index += 1\n"
            "        return total\n\n"
            "    def update_label(self, value):",
        )
        self.assertTrue(_validate_code(finite))

    def test_infinite_while_is_still_rejected(self) -> None:
        blocking = STYLED_APP.replace(
            "    def update_label(self, value):",
            "    def run_forever(self):\n"
            "        while True:\n"
            "            pass\n\n"
            "    def update_label(self, value):",
        )
        with self.assertRaises(GenerationError) as caught:
            _validate_code(blocking)
        self.assertRegex(str(caught.exception), r"第 \d+ 行阻塞式 while")
        self.assertIn("lv.timer_create", str(caught.exception))

    def test_finite_while_inside_on_create_is_allowed(self) -> None:
        finite = STYLED_APP.replace(
            "        screen = lv.obj()",
            "        count = 0\n"
            "        while count < 3:\n"
            "            count += 1\n"
            "        screen = lv.obj()",
        )
        self.assertTrue(_validate_code(finite))

    def test_infinite_while_in_on_create_is_rejected(self) -> None:
        blocking = STYLED_APP.replace(
            "        screen = lv.obj()",
            "        while True:\n"
            "            pass\n"
            "        screen = lv.obj()",
        )
        with self.assertRaisesRegex(GenerationError, "阻塞式 while"):
            _validate_code(blocking)

    def test_styled_app_passes_visual_contract(self) -> None:
        result = _validate_visual_contract(STYLED_APP)
        self.assertTrue(result)

    def test_reusable_single_padding_rule_is_not_rejected(self) -> None:
        reusable_style = STYLED_APP.replace(
            "        screen.set_style_pad_all(10, 0)\n",
            "",
        )
        result = _validate_visual_contract(reusable_style)
        self.assertTrue(result)

    def test_styled_app_without_any_padding_is_rejected(self) -> None:
        no_padding = "\n".join(
            line
            for line in STYLED_APP.splitlines()
            if "set_style_pad_" not in line
        )
        with self.assertRaisesRegex(GenerationError, "明确内边距"):
            _validate_visual_contract(no_padding)

    def test_default_looking_app_is_rejected(self) -> None:
        plain = "\n".join(
            line for line in STYLED_APP.splitlines() if "set_style_" not in line
        )
        with self.assertRaisesRegex(GenerationError, "界面仍像未设计的原型"):
            _validate_visual_contract(plain)

    def test_api_correction_includes_bad_code_and_coordinate_advice(self) -> None:
        bad_code = "self.player.get_pos()"
        correction = _build_correction(
            ApiValidationError(
                "LVGL_API_MISSING",
                "以下调用不在当前 API summary 中：lv.obj.get_pos",
            ),
            bad_code,
        )
        self.assertIn(bad_code, correction)
        self.assertIn("self.player_x", correction)
        self.assertIn("set_pos", correction)

    def test_correction_is_last_after_previous_and_failed_code(self) -> None:
        request = GenerateRequest(
            prompt="Build a timer dashboard",
            previous_code="PREVIOUS_CODE_SENTINEL",
            runtime_error="previous runtime failure",
        )
        correction = _build_correction(
            GenerationError(
                "第 12 行阻塞式 while 循环，界面更新请使用 lv.timer_create"
            ),
            "FAILED_CANDIDATE_SENTINEL",
            attempt=2,
        )
        prompt = _build_user_prompt(request, correction)
        self.assertLess(
            prompt.index("PREVIOUS_CODE_SENTINEL"),
            prompt.index("FAILED_CANDIDATE_SENTINEL"),
        )
        self.assertLess(
            prompt.index("FAILED_CANDIDATE_SENTINEL"),
            prompt.index("<FINAL_CORRECTION>"),
        )
        self.assertTrue(prompt.rstrip().endswith("</FINAL_CORRECTION>"))

    def test_unknown_get_pos_is_rejected_by_summary(self) -> None:
        bad = STYLED_APP.replace(
            "        self.label = lv.label(card)",
            "        card.get_pos()\n        self.label = lv.label(card)",
        )
        with self.assertRaises(ApiValidationError):
            _validate_api_summaries(bad)

    def test_position_reads_are_rejected_before_api_summary(self) -> None:
        bad = STYLED_APP.replace(
            "        self.label = lv.label(card)",
            "        card.get_pos()\n        self.label = lv.label(card)",
        )
        with self.assertRaisesRegex(GenerationError, "get_pos"):
            _validate_code(bad)

    def test_widget_tree_reads_are_rejected_with_python_list_guidance(self) -> None:
        bad = STYLED_APP.replace(
            "        before = self.label.get_text()",
            "        count = self.label.get_child_cnt()\n"
            "        before = self.label.get_text()",
        )
        with self.assertRaisesRegex(GenerationError, "get_child_cnt"):
            _validate_code(bad)
        correction = _build_correction(
            GenerationError("lv.obj.get_child_cnt is unavailable"),
            bad,
        )
        self.assertIn("self.day_buttons", correction)
        self.assertIn("len(self.day_buttons)", correction)

    def test_system_prompt_requires_visual_design_and_no_position_reads(self) -> None:
        self.assertIn("视觉质量是验收条件", SYSTEM_PROMPT)
        self.assertIn("get_pos()", SYSTEM_PROMPT)
        self.assertIn("set_style_bg_color", SYSTEM_PROMPT)
        self.assertIn("set_flex_flow(flow)", SYSTEM_PROMPT)
        self.assertIn("get_child_cnt()", SYSTEM_PROMPT)
        self.assertIn("align_to(other, lv.ALIGN.OUT_BOTTOM_MID, x, y)", SYSTEM_PROMPT)
        self.assertIn("纯计算逻辑允许使用", SYSTEM_PROMPT)

    def test_calendar_prompt_rejects_generic_styled_app(self) -> None:
        with self.assertRaisesRegex(GenerationError, "日期按钮集合"):
            _validate_product_contract(STYLED_APP, "做一个日历")

    def test_calculator_prompt_requires_real_arithmetic_controls(self) -> None:
        with self.assertRaisesRegex(GenerationError, "计算执行逻辑"):
            _validate_product_contract(STYLED_APP, "做一个四则运算计算器")

    def test_calculator_button_factory_is_not_mistaken_for_one_button(self) -> None:
        calculator = STYLED_APP.replace(
            "        self.setContentView(screen)\n",
            "        button.add_event_cb(self.on_key, lv.EVENT.CLICKED, None)\n"
            "        self.operator = '+'\n"
            "        self.result = 0\n"
            "        self.setContentView(screen)\n",
        ).replace(
            "    def update_label(self, value):\n",
            "    def on_key(self, event):\n"
            "        if self.operator == '+': self.result = 8 + 2\n"
            "        elif self.operator == '-': self.result = 8 - 2\n"
            "        elif self.operator == '*': self.result = 8 * 2\n"
            "        elif self.operator == '/': self.result = 8 / 2\n\n"
            "    def update_label(self, value):\n",
        )
        result = _validate_product_contract(
            calculator,
            "做一个极简四则运算计算器，按钮要大，适合触摸屏",
        )
        self.assertTrue(result)


class GeneratorRetryDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _payload(code: str) -> dict[str, object]:
        return {
            "summary": "Status panel",
            "app_code": code,
            "acceptance_tests": ["state changes", "state restores"],
        }

    async def test_failed_attempt_then_success_emits_private_diagnostics(self) -> None:
        blocking = STYLED_APP.replace(
            "    def update_label(self, value):",
            "    def run_forever(self):\n"
            "        while True:\n"
            "            pass\n\n"
            "    def update_label(self, value):",
        )
        records: list[dict[str, object]] = []
        responses = [
            (
                self._payload(blocking),
                "test-model",
                {
                    "model": "test-model",
                    "request_id": "req-failed",
                    "usage": {"total_tokens": 10},
                },
            ),
            (
                self._payload(STYLED_APP),
                "test-model",
                {
                    "model": "test-model",
                    "request_id": "req-success",
                    "usage": {"total_tokens": 20},
                },
            ),
        ]
        request = GenerateRequest(
            prompt="Build a styled status panel with secret sk-super-secret"
        )
        with patch(
            "app.generator._call_deepseek",
            new=AsyncMock(side_effect=responses),
        ):
            result = await generate_app(request, attempt_sink=records.append)
        self.assertEqual(result.model, "test-model")
        self.assertEqual([item["status"] for item in records], [
            "validation_failed",
            "passed",
        ])
        self.assertRegex(
            str(records[0]["validation"]),
            r"第 \d+ 行阻塞式 while",
        )
        self.assertIn("candidate", records[0])
        self.assertNotIn(
            "sk-super-secret",
            json.dumps(records, ensure_ascii=False),
        )

    async def test_failed_attempts_are_all_emitted(self) -> None:
        blocking = STYLED_APP.replace(
            "    def update_label(self, value):",
            "    def run_forever(self):\n"
            "        while True:\n"
            "            pass\n\n"
            "    def update_label(self, value):",
        )
        records: list[dict[str, object]] = []
        response = (
            self._payload(blocking),
            "test-model",
            {"model": "test-model"},
        )
        with patch(
            "app.generator._call_deepseek",
            new=AsyncMock(side_effect=[response] * 2),
        ):
            with patch.dict(
                "app.generator.os.environ",
                {"DEEPSEEK_MAX_ATTEMPTS": "2"},
            ):
                with self.assertRaisesRegex(GenerationError, "经过 2 次"):
                    await generate_app(
                        GenerateRequest(prompt="Build a styled status panel"),
                        attempt_sink=records.append,
                    )
        self.assertEqual(len(records), 2)
        self.assertTrue(
            all(item["status"] == "validation_failed" for item in records)
        )


if __name__ == "__main__":
    unittest.main()
