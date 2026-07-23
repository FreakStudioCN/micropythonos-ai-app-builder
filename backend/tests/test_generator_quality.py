import base64
import io
import unittest
import zipfile

from app.generator import (
    ApiValidationError,
    GenerationError,
    SYSTEM_PROMPT,
    _build_mpk,
    _build_correction,
    _normalize_lvgl_code,
    _validate_api_summaries,
    _validate_code,
    _validate_visual_contract,
)


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
            self.assertIn(f"{package_name}/assets/main.py", names)

    def test_flex_flow_selector_is_removed_for_current_lvgl_binding(self) -> None:
        source = "grid.set_flex_flow(lv.FLEX_FLOW.ROW_WRAP, 0)"
        normalized, warnings = _normalize_lvgl_code(source)
        self.assertEqual(
            normalized,
            "grid.set_flex_flow(lv.FLEX_FLOW.ROW_WRAP)",
        )
        self.assertTrue(any("set_flex_flow" in item for item in warnings))

    def test_styled_app_passes_visual_contract(self) -> None:
        result = _validate_visual_contract(STYLED_APP)
        self.assertTrue(result)

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


if __name__ == "__main__":
    unittest.main()
