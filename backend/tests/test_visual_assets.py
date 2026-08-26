import base64
import hashlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.generator import (
    GenerationError,
    _build_mpk,
    _validate_visual_asset_usage,
)
from app.models import GeneratedFile, SessionCreateRequest
import app.session_service as session_module
from app.visual_assets import (
    _license_is_reusable,
    heuristic_visual_asset_plan,
    native_visual_asset_plan,
    procedural_spec,
)


class VisualAssetTests(unittest.TestCase):
    def test_heuristic_keeps_plain_apps_native(self) -> None:
        self.assertEqual(
            heuristic_visual_asset_plan("A simple countdown timer", allow_web=True)[
                "render_strategy"
            ],
            "lvgl_native",
        )

    def test_heuristic_routes_generic_art_to_procedural(self) -> None:
        plan = heuristic_visual_asset_plan(
            "A weather dashboard with an illustrated background",
            allow_web=True,
        )
        self.assertEqual(plan["render_strategy"], "hybrid")
        self.assertEqual(plan["assets"][0]["generation_mode"], "procedural")

    def test_heuristic_routes_named_subject_to_web(self) -> None:
        plan = heuristic_visual_asset_plan(
            "A Pikachu themed dashboard image",
            allow_web=True,
        )
        self.assertEqual(plan["assets"][0]["generation_mode"], "web")
        self.assertTrue(plan["assets"][0]["search_query"])

    def test_procedural_spec_is_deterministic(self) -> None:
        asset = {
            "id": "hero_artwork",
            "width": 160,
            "height": 120,
            "transparent": True,
        }
        first = procedural_spec(asset, "ocean dashboard")
        second = procedural_spec(asset, "ocean dashboard")
        self.assertEqual(first, second)
        self.assertEqual(first["schema_version"], "mpos-visual-asset-spec-v1")

    def test_mpk_contains_validated_runtime_image(self) -> None:
        package_name = "com.example.visual"
        raw = b"\x19\x12runtime-image"
        digest = hashlib.sha256(raw).hexdigest()
        encoded = _build_mpk(
            package_name,
            {"fullname": package_name},
            "print('ready')",
            [
                {
                    "runtime_path": "assets/images/hero_artwork.bin",
                    "content_base64": base64.b64encode(raw).decode("ascii"),
                    "runtime_sha256": digest,
                }
            ],
        )
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(encoded))) as archive:
            self.assertEqual(
                archive.read(f"{package_name}/assets/images/hero_artwork.bin"),
                raw,
            )

    def test_mpk_rejects_escaping_runtime_path(self) -> None:
        raw = b"runtime-image"
        with self.assertRaises(GenerationError):
            _build_mpk(
                "com.example.visual",
                {"fullname": "com.example.visual"},
                "print('ready')",
                [
                    {
                        "runtime_path": "assets/images/../escape.bin",
                        "content_base64": base64.b64encode(raw).decode("ascii"),
                        "runtime_sha256": hashlib.sha256(raw).hexdigest(),
                    }
                ],
            )

    def test_visual_usage_requires_exact_path_and_fallback(self) -> None:
        asset = {"runtime_path": "assets/images/hero_artwork.bin"}
        valid = """
try:
    image.set_src('M:apps/com.example.visual/assets/images/hero_artwork.bin')
except Exception:
    fallback.set_style_bg_color(lv.color_hex(0x123456), 0)
"""
        warnings = _validate_visual_asset_usage(
            valid,
            "com.example.visual",
            [asset],
        )
        self.assertEqual(len(warnings), 1)
        with self.assertRaises(GenerationError):
            _validate_visual_asset_usage(
                "image.set_src('assets/images/hero_artwork.bin')",
                "com.example.visual",
                [asset],
            )

    def test_generated_binary_file_requires_base64(self) -> None:
        with self.assertRaises(ValueError):
            GeneratedFile(path="assets/images/a.bin", encoding="base64")
        file = GeneratedFile(
            path="assets/images/a.bin",
            encoding="base64",
            content_base64="YQ==",
        )
        self.assertEqual(file.content_base64, "YQ==")

    def test_native_plan_has_no_assets(self) -> None:
        self.assertEqual(native_visual_asset_plan()["assets"], [])

    def test_web_license_rejects_noncommercial_restrictions(self) -> None:
        self.assertTrue(_license_is_reusable("CC BY-SA 4.0"))
        self.assertFalse(_license_is_reusable("CC BY-NC 4.0"))


class VisualAssetSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        session_module.SESSION_ROOT = Path(self.temp.name).resolve()
        self.service = session_module.SessionService()

    def tearDown(self) -> None:
        self.temp.cleanup()

    async def test_procedural_asset_builds_and_validates_bundle(self) -> None:
        state = self.service.create(
            SessionCreateRequest(
                idempotency_key="visual-build-create-0001",
                prompt="A weather dashboard with an illustrated background",
                package_name="com.example.visual_build",
                targets=["package-only"],
            )
        )
        state["visual_asset_plan"] = heuristic_visual_asset_plan(
            state["input"]["prompt_original"],
            allow_web=False,
        )
        payloads = await self.service._build_visual_assets(
            state,
            state["input"]["prompt_original"],
        )
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["encoding"], "base64")
        self.assertEqual(payloads[0]["runtime_path"], "assets/images/scene_background.bin")
        roles = {item["role"] for item in state["artifacts"]}
        self.assertIn("visual_asset_bundle", roles)
        self.assertIn("app_runtime_image", roles)


if __name__ == "__main__":
    unittest.main()
