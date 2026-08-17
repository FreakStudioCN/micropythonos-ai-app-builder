"""Fixed demo sessions and the demo-only error injector.

Extracted from ``session_service`` as a mixin so the demo seeds and their
replay logic sit apart from the real session state machine.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

from .generator import _build_mpk
from .models import DemoErrorInjectionRequest, DemoSessionRequest, SessionCreateRequest
from .runner_services import STAGE_SKILLS
from .session_common import _json_dump, _now


DEMO_SEEDS: dict[str, dict[str, str]] = {
    "countdown": {
        "prompt_zh": "做一个课堂倒计时器，深色主题，有开始、暂停和重置按钮。",
        "prompt_en": "Build a dark classroom countdown timer with start, pause and reset controls.",
        "package_name": "com.demo.classroom_timer",
        "display_name_zh": "课堂倒计时",
        "display_name_en": "Classroom Timer",
        "summary": "一个适合课堂投屏演示的深色倒计时器。",
        "headline": "05:00",
        "caption": "READY · CLASSROOM TIMER",
    },
    "calendar": {
        "prompt_zh": "做一个精美的深色日历，可以查看本月日期。",
        "prompt_en": "Build a polished dark calendar for viewing the current month.",
        "package_name": "com.demo.calendar",
        "display_name_zh": "星空日历",
        "display_name_en": "Starlight Calendar",
        "summary": "一个深色主题、适合触摸屏的月历。",
        "headline": "JULY 2026",
        "caption": "MON  TUE  WED  THU  FRI",
    },
    "device-dashboard": {
        "prompt_zh": "做一个酷炫的 ESP32 设备状态面板，显示温度、网络和电量。",
        "prompt_en": "Build a polished ESP32 status dashboard showing temperature, network and battery.",
        "package_name": "com.demo.device_dashboard",
        "display_name_zh": "设备脉搏",
        "display_name_en": "Device Pulse",
        "summary": "一个展示温度、网络和电量的设备状态面板。",
        "headline": "42°C  ·  87%",
        "caption": "DEVICE ONLINE · WIFI STRONG",
    },
}


class SessionDemoMixin:
    """Demo-only session helpers mixed into :class:`SessionService`."""

    def create_demo(
        self,
        request: DemoSessionRequest,
        user_id: str = "local-test-user",
    ) -> dict[str, Any]:
        seed = DEMO_SEEDS[request.seed]
        state = self.create(
            SessionCreateRequest(
                idempotency_key=f"demo:{request.seed}:{request.idempotency_key}",
                prompt=(
                    seed["prompt_zh"]
                    if request.ui_locale == "zh-CN"
                    else seed["prompt_en"]
                ),
                prompt_language=(
                    "zh-CN" if request.ui_locale == "zh-CN" else "en-US"
                ),
                ui_locale=request.ui_locale,
                package_name=seed["package_name"],
                display_name=(
                    seed["display_name_zh"]
                    if request.ui_locale == "zh-CN"
                    else seed["display_name_en"]
                ),
                display_name_zh=seed["display_name_zh"],
                display_name_en=seed["display_name_en"],
                short_description_zh=seed["summary"],
                short_description_en=seed["summary"],
                long_description_zh=seed["summary"],
                long_description_en=seed["summary"],
                release_notes_zh="固定演示版本",
                release_notes_en="Deterministic demo release",
                category="demo",
                publisher="MicroPythonOS",
                version="1.0.0",
                targets=["web-preview", "package-only"],
            ),
            user_id=user_id,
        )
        if state.get("demo_seed") == request.seed:
            return state
        for permission in state["permissions"]:
            if permission.get("required"):
                permission["decision"] = "allow_once"
                permission["decided_at"] = _now()
                permission["decision_source"] = "demo_seed"
        root = self._root(state["session_id"])
        app_root = (
            root
            / "project"
            / "internal_filesystem"
            / "apps"
            / seed["package_name"]
        )
        app_root.mkdir(parents=True, exist_ok=True)
        manifest = {
            "fullname": seed["package_name"],
            "name": seed["display_name_en"],
            "publisher": "MicroPythonOS",
            "version": "1.0.0",
            "activities": [
                {"entrypoint": "assets/main.py", "classname": "GeneratedApp"}
            ],
        }
        code = f'''import lvgl as lv
from mpos import Activity

class GeneratedApp(Activity):
    def onCreate(self):
        screen = lv.obj()
        screen.set_style_bg_color(lv.color_hex(0x0B1020), 0)
        title = lv.label(screen)
        title.set_text("{seed['display_name_en']}")
        title.set_pos(18, 18)
        headline = lv.label(screen)
        headline.set_text("{seed['headline']}")
        headline.set_pos(18, 82)
        caption = lv.label(screen)
        caption.set_text("{seed['caption']}")
        caption.set_pos(18, 142)
        hint = lv.label(screen)
        hint.set_text("MicroPythonOS · DEMO")
        hint.set_pos(18, 202)
        self.setContentView(screen)
'''
        manifest_path = app_root / "MANIFEST.JSON"
        source_path = app_root / "assets" / "main.py"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        source_path.write_text(code, encoding="utf-8")
        generation_result = {
            "schema_version": "mpos-gen-app-web-v1",
            "phase": "mpos-gen-app-web",
            "result": "success",
            "summary": seed["summary"],
            "model": "deterministic-demo-seed",
            "demo_seed": request.seed,
            "warnings": [],
            "structured_errors": [],
        }
        generation_path = root / "artifacts" / "generation_result.json"
        _json_dump(generation_path, generation_result)
        for path, kind, role in (
            (manifest_path, "source", "app_manifest"),
            (source_path, "source", "app_source"),
            (generation_path, "result", "generation_result"),
        ):
            self._register_artifact(state, path, "mpos-gen-app-web", kind, role)
        mpk_filename = f"{seed['package_name']}_r1.mpk"
        mpk_path = root / "artifacts" / mpk_filename
        mpk_path.write_bytes(
            base64.b64decode(_build_mpk(seed["package_name"], manifest, code))
        )
        self._register_artifact(
            state, mpk_path, "mpos-package-app-web", "package", "mpk"
        )
        for name, phase, payload in (
            (
                "app_test_result",
                "mpos-test-app-web",
                {
                    "result": "success",
                    "web_preview": {"status": "passed", "mode": "demo_seed"},
                    "warnings": [],
                    "structured_errors": [],
                },
            ),
            (
                "package_result",
                "mpos-package-app-web",
                {
                    "result": "success",
                    "package": {"filename": mpk_filename, "revision": 1},
                    "warnings": [],
                    "structured_errors": [],
                },
            ),
            (
                "publish_result",
                "mpos-publish-app-web",
                {
                    "result": "success",
                    "status": "ready_for_manual_upload",
                    "publish_ready": False,
                    "upystore": {
                        "home_url": "https://upystore.io/",
                        "developer_url": "https://upystore.io/developer",
                        "mode": "manual_guidance",
                    },
                    "checks": [
                        {"name": "manifest.publisher", "status": "passed"},
                        {"name": "mpk_release_filename", "status": "passed"},
                    ],
                    "warnings": [],
                    "structured_errors": [],
                },
            ),
        ):
            self._write_artifact_json(state, name, phase, payload)
        state["demo_seed"] = request.seed
        state["generation"] = {
            "package_name": seed["package_name"],
            "summary": seed["summary"],
            "manifest": manifest,
            "files": [
                {"path": "MANIFEST.JSON", "content": json.dumps(manifest)},
                {"path": "assets/main.py", "content": code},
            ],
            "model": "deterministic-demo-seed",
            "mpk_filename": mpk_filename,
            "revision": 1,
        }
        state["input"]["prompt_normalized_zh"] = seed["prompt_zh"]
        state["input"]["prompt_normalized_en"] = seed["prompt_en"]
        state["status"] = "running"
        state["checkpoint_id"] = "publish_check_done"
        state["current_phase"] = "mpos-publish-app-web"
        state["next_phase"] = None
        state["completed_phases"] = list(STAGE_SKILLS.values())
        state["warnings"] = []
        state["last_error"] = None
        state["structured_errors"] = []
        self._write_manifest(state)
        self._write_publish_bundle(state)
        state["status"] = "completed"
        state["checkpoint_id"] = "completed"
        self._apply_final_artifact_gate(state, completion_requested=True)
        self._write_session_bundle(state)
        self._write_manifest(state)
        self._write_state(state)
        self._event(
            state,
            "status_update",
            "mpos-publish-app-web",
            {
                "status": state["status"],
                "message": f"已恢复固定演示：{seed['display_name_zh']}",
                "demo_seed": request.seed,
            },
        )
        return self.get(state["session_id"])

    def inject_demo_error(
        self, session_id: str, request: DemoErrorInjectionRequest
    ) -> dict[str, Any]:
        if os.getenv("MPOS_DEMO_ERROR_INJECTION", "false").lower() not in {
            "1",
            "true",
            "yes",
            "on",
        }:
            raise PermissionError("Demo error injection is disabled")
        state = self._read(session_id)
        task = self._tasks.get(session_id)
        if task and not task.done():
            raise ValueError("Cannot inject an error while the session is running")
        if state.get("demo_error_idempotency_key") == request.idempotency_key:
            return state
        messages = {
            "LVGL_API_MISSING": "演示错误：当前 API summary 中不存在 lv.obj.get_pos",
            "SCRIPT_TIMEOUT": "演示错误：受控测试脚本执行超时",
            "DEVICE_NOT_CONNECTED": "演示错误：未检测到可部署设备",
            "WEB_PREVIEW_BUILD_FAILED": "演示错误：Web preview 构建失败",
        }
        error = {
            "code": request.code,
            "message": messages[request.code],
            "stage": "generation",
            "phase": "mpos-gen-app-web",
            "owner": "app",
            "retryable": True,
            "details": {"injected": True, "demo_only": True},
            "logs": ["activity_log.jsonl"],
        }
        state["demo_error_idempotency_key"] = request.idempotency_key
        state["status"] = "failed"
        state["checkpoint_id"] = "failed"
        state["next_phase"] = "mpos-gen-app-web"
        state["last_error"] = error
        state.setdefault("structured_errors", []).append(error)
        self._write_state(state)
        self._event(state, "structured_error", "mpos-gen-app-web", error)
        return self.get(session_id)
