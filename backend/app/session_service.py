import asyncio
import base64
import binascii
import difflib
import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from fastapi.encoders import jsonable_encoder

from .generator import GenerationError, _build_mpk, _default_icon_png, generate_app
from .models import (
    PROTOCOL_VERSION,
    DemoErrorInjectionRequest,
    DemoSessionRequest,
    DeviceResultRequest,
    GenerateRequest,
    PermissionBatchDecisionRequest,
    PermissionDecisionRequest,
    PreviewResultRequest,
    RevisionRequest,
    ScreenshotUploadRequest,
    SessionActionRequest,
    SessionCreateRequest,
)
from .object_storage import session_object_store
from .runner_services import (
    STAGE_SKILLS,
    api_summary_version,
    device_service,
    hardware_capability_registry,
    mpos_skill_adapter,
    script_dispatcher,
)
from .session_index import EventLogCache, SessionIndex


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SESSION_ROOT = Path(
    os.getenv("MPOS_SESSION_ROOT", str(PROJECT_ROOT / "backend" / "sessions"))
).resolve()
ARTIFACT_ID_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")
INSTALLER_URL = "https://install.micropythonos.com/"
SAFE_AI_PROVIDER_IDS = {
    "deepseek",
    "kimi",
    "kimi_k27",
    "zhipu_glm52",
    "deepseek_primary",
    "deepseek_secondary",
    "aigocode",
}

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


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json_dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(jsonable_encoder(value), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _json_load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _git_commit(path: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True,
            check=False,
            text=True,
            timeout=3,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass

    git_marker = path / ".git"
    git_dir = git_marker
    if git_marker.is_file():
        line = git_marker.read_text(encoding="utf-8", errors="ignore").strip()
        if line.startswith("gitdir:"):
            raw = line.split(":", 1)[1].strip()
            git_dir = (path / raw).resolve()
    head = git_dir / "HEAD"
    if not head.exists():
        return "unknown"
    value = head.read_text(encoding="utf-8", errors="ignore").strip()
    if value.startswith("ref:"):
        ref = git_dir / value.split(":", 1)[1].strip()
        if ref.exists():
            return ref.read_text(encoding="utf-8", errors="ignore").strip()
    return value or "unknown"


def _redact_text(value: str) -> str:
    """Remove host-only and secret-like values from user-facing exports."""
    value = re.sub(
        r"(?i)\b(?:sk|api)[-_][a-z0-9_-]{12,}\b",
        "[REDACTED_TOKEN]",
        value,
    )
    value = re.sub(
        r"(?i)(authorization\s*:\s*bearer\s+)[^\s\"']+",
        r"\1[REDACTED_TOKEN]",
        value,
    )
    value = re.sub(
        r"(?i)\b(?:COM\d+|/dev/(?:tty|cu)\S+)\b",
        "[REDACTED_SERIAL]",
        value,
    )
    value = re.sub(
        r"(?i)(?:[a-z]:\\(?:[^\\\r\n]+\\)+|/(?:home|users|var|tmp)/)"
        r"[^\s\"']*",
        "[REDACTED_PATH]",
        value,
    )
    return value


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if any(
                    marker in key.lower()
                    for marker in ("token", "api_key", "secret", "serial_port")
                )
                else _redact_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _normalize_hardware_policy_result(result: dict[str, Any]) -> dict[str, Any]:
    """Treat a missing optional policy tool as a warning, not a violation."""
    error = result.get("error")
    if (
        not result.get("ok")
        and isinstance(error, dict)
        and error.get("code") == "TOOLCHAIN_MISSING"
    ):
        normalized = dict(result)
        normalized["ok"] = True
        normalized["skipped"] = True
        normalized["warning"] = normalized.pop("error")
        return normalized
    return result


class SessionNotFound(KeyError):
    pass


class SessionService:
    def __init__(self, object_store: Any | None = None) -> None:
        SESSION_ROOT.mkdir(parents=True, exist_ok=True)
        self._object_store = object_store or session_object_store
        self._object_store.restore_all(SESSION_ROOT)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._action_by_session: dict[str, str] = {}
        self._event_cache = EventLogCache()
        self._index = SessionIndex(SESSION_ROOT)
        self._generation_success_handler: Callable[[dict[str, Any]], None] | None = None

    def set_generation_success_handler(
        self,
        handler: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        self._generation_success_handler = handler

    def configure_generation_billing(
        self,
        session_id: str,
        *,
        action_idempotency_key: str,
        unlimited: bool,
    ) -> dict[str, Any]:
        state = self._read(session_id)
        revision_id = str(state.get("revision_id", "r1"))
        previous = state.get("billing") or {}
        state["billing"] = {
            "charge_on_success": True,
            "settled": bool(previous.get("settled")),
            "settled_at": previous.get("settled_at"),
            "idempotency_key": (
                previous.get("idempotency_key")
                or f"generation:{session_id}:{revision_id}"
            ),
            "action_idempotency_key": action_idempotency_key,
            "unlimited": unlimited,
            "exempt_reason": None,
        }
        self._write_state(state)
        return self.get(session_id)

    @property
    def object_storage_enabled(self) -> bool:
        return bool(self._object_store.enabled)

    def capabilities(self) -> dict[str, Any]:
        desktop_capability = script_dispatcher.desktop_smoke_capability()
        return {
            "protocol_version": PROTOCOL_VERSION,
            "capabilities": {
                "file_operation": True,
                "script_run": True,
                "approval_request": True,
                "permission_request": True,
                "checkpoint_resume": True,
                "cancellation": True,
                "retry": True,
                "timeout": True,
                "desktop_preview": desktop_capability["available"],
                "web_preview": True,
                "browser_webserial": False,
                **device_service.capabilities(),
                "firmware_flash": False,
                "network_read": True,
                "network_upload": False,
                "upystore_publish": False,
            },
            "repo_commit": _git_commit(PROJECT_ROOT / "vendor" / "MicroPythonOS"),
            "skills_commit": _git_commit(
                PROJECT_ROOT / "vendor" / "MicroPython_Skills"
            ),
            "mpos_commit": _git_commit(PROJECT_ROOT / "vendor" / "MicroPythonOS"),
            "skill_commit": _git_commit(PROJECT_ROOT / "vendor" / "MicroPython_Skills"),
            "board_capabilities_schema": hardware_capability_registry.describe(),
            "web_preview_notice": (
                "Web preview is a quick browser compatibility preview. It does not "
                "replace real hardware deployment."
            ),
            "desktop_preview_details": desktop_capability,
        }

    def _root(self, session_id: str) -> Path:
        if not re.fullmatch(r"sess_[a-f0-9]{16}", session_id):
            raise SessionNotFound(session_id)
        root = (SESSION_ROOT / session_id).resolve()
        if SESSION_ROOT not in root.parents:
            raise SessionNotFound(session_id)
        return root

    def _state_path(self, session_id: str) -> Path:
        return self._root(session_id) / "session_state.json"

    def _read(self, session_id: str) -> dict[str, Any]:
        path = self._state_path(session_id)
        if not path.exists():
            raise SessionNotFound(session_id)
        state = _json_load(path)
        state["events"] = self.events(session_id)
        return state

    def get(self, session_id: str) -> dict[str, Any]:
        return self._read(session_id)

    def require_owner(self, session_id: str, user_id: str) -> dict[str, Any]:
        state = self._read(session_id)
        if state.get("owner_user_id") != user_id:
            raise SessionNotFound(session_id)
        return state

    def require_artifact_owner(self, artifact_id: str, user_id: str) -> None:
        if not ARTIFACT_ID_RE.fullmatch(artifact_id):
            raise SessionNotFound(artifact_id)
        session_id = self._index.session_for_artifact(artifact_id)
        if not session_id:
            raise SessionNotFound(artifact_id)
        self.require_owner(session_id, user_id)

    def require_permission_owner(self, permission_id: str, user_id: str) -> None:
        session_id = self._index.session_for_permission(permission_id)
        if not session_id:
            raise SessionNotFound(permission_id)
        self.require_owner(session_id, user_id)

    def list_sessions(self, user_id: str | None = None) -> list[dict[str, Any]]:
        sessions: list[dict[str, Any]] = []
        for path in sorted(
            SESSION_ROOT.glob("sess_*/session_state.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        ):
            try:
                item = _json_load(path)
                if user_id is not None and item.get("owner_user_id") != user_id:
                    continue
                item.pop("generation", None)
                sessions.append(item)
            except (OSError, json.JSONDecodeError):
                continue
        return sessions[:50]

    def session_summary(self, session_id: str) -> dict[str, Any]:
        state = self._read(session_id)
        generation = state.get("generation") or {}
        return _redact_value(
            {
                "schema_version": "mpos-session-summary-v1",
                "session_id": session_id,
                "revision_id": state["revision_id"],
                "status": state["status"],
                "checkpoint_id": state["checkpoint_id"],
                "prompt_original": state["input"]["prompt_original"],
                "prompt_normalized_zh": state["input"].get(
                    "prompt_normalized_zh", ""
                ),
                "prompt_normalized_en": state["input"].get(
                    "prompt_normalized_en", ""
                ),
                "app": {
                    "fullname": state["input"]["package_name"],
                    "display_name": state["input"]["display_name"],
                    "publisher": state["input"]["publisher"],
                    "version": state["input"]["version"],
                },
                "summary": generation.get("summary", ""),
                "artifacts": [
                    {
                        "id": item["id"],
                        "role": item["role"],
                        "path": item["path"],
                        "mime": item["mime"],
                        "size": item["size"],
                        "sha256": item["sha256"],
                    }
                    for item in state.get("artifacts", [])
                ],
                "warnings": state.get("warnings", []),
                "structured_errors": state.get("structured_errors", []),
                "repo_commit": state.get("repo_commit"),
                "skills_commit": state.get("skills_commit"),
                "created_at": state.get("created_at"),
                "updated_at": state.get("updated_at"),
                "handoff": {
                    "next_phase": state.get("next_phase"),
                    "installer_url": INSTALLER_URL,
                    "upystore_developer_url": "https://upystore.io/developer",
                },
            }
        )

    def activity_log(
        self, session_id: str, *, view: str = "engineer", redacted: bool = True
    ) -> dict[str, Any]:
        events = self.events(session_id)
        if view == "user":
            allowed = {
                "phase_complete",
                "status_update",
                "permission_request",
                "structured_error",
            }
            events = [item for item in events if item.get("type") in allowed]
        if redacted:
            events = _redact_value(events)
        return {
            "schema_version": "mpos-activity-log-export-v1",
            "session_id": session_id,
            "view": view,
            "redacted": redacted,
            "events": events,
        }

    def export_bundle(
        self, session_id: str, *, kind: str = "session"
    ) -> tuple[Path, dict[str, Any]]:
        state = self._read(session_id)
        root = self._root(session_id)
        if kind not in {"session", "demo-artifacts"}:
            raise ValueError("Unsupported export kind")
        filename = (
            f"{state['input']['package_name']}_{state['revision_id']}_"
            f"{'demo-artifacts' if kind == 'demo-artifacts' else 'session-redacted'}.zip"
        )
        bundle = root / "artifacts" / filename
        allowed_demo_roles = {
            "app_manifest",
            "app_source",
            "mpk",
            "publish_result",
            "publish_screenshot",
            "desktop_screenshot",
            "artifact_manifest",
        }
        summary = self.session_summary(session_id)
        log = self.activity_log(session_id, view="engineer", redacted=True)
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "session_summary.json",
                json.dumps(summary, ensure_ascii=False, indent=2),
            )
            archive.writestr(
                "activity_log.redacted.jsonl",
                "".join(
                    json.dumps(item, ensure_ascii=False) + "\n"
                    for item in log["events"]
                ),
            )
            archive.writestr(
                "session_state.redacted.json",
                json.dumps(_redact_value(state), ensure_ascii=False, indent=2),
            )
            for artifact in state.get("artifacts", []):
                if kind == "demo-artifacts" and artifact["role"] not in allowed_demo_roles:
                    continue
                if artifact["kind"] == "bundle":
                    continue
                source = (root / artifact["path"]).resolve()
                if root not in source.parents or not source.is_file():
                    continue
                arcname = artifact["path"]
                if source.suffix.lower() in {".json", ".jsonl", ".txt", ".log", ".py"}:
                    archive.writestr(
                        arcname,
                        _redact_text(source.read_text(encoding="utf-8", errors="replace")),
                    )
                else:
                    archive.write(source, arcname)
        role = (
            "demo_artifact_bundle"
            if kind == "demo-artifacts"
            else "redacted_session_bundle"
        )
        self._register_artifact(
            state, bundle, "mpos-publish-app-web", "bundle", role
        )
        self._write_manifest(state)
        self._write_state(state)
        artifact = next(item for item in state["artifacts"] if item["role"] == role)
        return bundle, artifact

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

    def events(self, session_id: str) -> list[dict[str, Any]]:
        path = self._root(session_id) / "activity_log.jsonl"
        return self._event_cache.read(session_id, path)

    def _write_state(self, state: dict[str, Any]) -> None:
        state = dict(state)
        state.pop("events", None)
        billing = state.get("billing") or {}
        should_settle = (
            state.get("status") == "completed"
            and billing.get("charge_on_success") is True
            and billing.get("settled") is not True
            and bool(billing.get("action_idempotency_key"))
            and state.get("last_action_idempotency_key")
            == billing.get("action_idempotency_key")
        )
        if should_settle and self._generation_success_handler is not None:
            self._generation_success_handler(state)
            billing["settled"] = True
            billing["settled_at"] = _now()
            state["billing"] = billing
        state["updated_at"] = _now()
        _json_dump(self._state_path(state["session_id"]), state)
        self._index.register_state(state)
        self._object_store.sync_session(SESSION_ROOT, state["session_id"])

    def _event(
        self,
        state: dict[str, Any],
        event_type: str,
        phase: str,
        payload: dict[str, Any],
    ) -> None:
        root = self._root(state["session_id"])
        log_path = root / "activity_log.jsonl"
        event = {
            "protocol_version": PROTOCOL_VERSION,
            "seq": self._event_cache.next_seq(state["session_id"], log_path),
            "ts": _now(),
            "type": event_type,
            "stage": phase.removeprefix("mpos-").removesuffix("-app-web"),
            "phase": phase,
            "session_id": state["session_id"],
            "checkpoint_id": state.get("checkpoint_id"),
            "payload": payload,
        }
        self._event_cache.append(state["session_id"], log_path, event)
        self._object_store.sync_path(
            SESSION_ROOT,
            state["session_id"],
            log_path,
        )

    def create(
        self,
        request: SessionCreateRequest,
        user_id: str = "local-test-user",
    ) -> dict[str, Any]:
        for existing in self.list_sessions(user_id):
            if (
                existing.get("create_idempotency_key") == request.idempotency_key
                and existing.get("input", {}).get("prompt_original") == request.prompt
            ):
                return self.get(existing["session_id"])

        capability_sources = {} if request.required_capabilities else hardware_capability_registry.classify(request.prompt)
        required_capabilities = request.required_capabilities or list(capability_sources)
        capability_contract = hardware_capability_registry.resolve(
            required_capabilities, request.runtime_fallbacks, capability_sources
        )
        physical_validation_required = (
            request.physical_validation_required
            or capability_contract.get("physical_validation_required", False)
        )
        session_id = f"sess_{uuid.uuid4().hex[:16]}"
        root = self._root(session_id)
        (root / "artifacts").mkdir(parents=True)
        (root / "project").mkdir()
        permission_id = f"perm_file_write_{uuid.uuid4().hex[:12]}"
        network_permission_id = f"perm_network_read_{uuid.uuid4().hex[:12]}"
        script_permission_id = f"perm_script_run_{uuid.uuid4().hex[:12]}"
        package_permission_id = f"perm_package_build_{uuid.uuid4().hex[:12]}"
        serial_permission_id = f"perm_serial_scan_{uuid.uuid4().hex[:12]}"
        device_permission_id = f"perm_device_write_{uuid.uuid4().hex[:12]}"
        input_hash = hashlib.sha256(
            json.dumps(request.model_dump(), sort_keys=True).encode("utf-8")
        ).hexdigest()
        state: dict[str, Any] = {
            "schema_version": "mpos-ai-app-session-v1",
            "protocol_version": PROTOCOL_VERSION,
            "session_id": session_id,
            "owner_user_id": user_id,
            "revision_id": "r1",
            "billing": {
                "charge_on_success": True,
                "settled": False,
                "settled_at": None,
                "idempotency_key": f"generation:{session_id}:r1",
                "action_idempotency_key": None,
                "unlimited": False,
                "exempt_reason": None,
            },
            "create_idempotency_key": request.idempotency_key,
            "status": "blocked",
            "current_phase": "mpos-plan-app-web",
            "checkpoint_id": "session_created",
            "checkpoint_history": [],
            "next_phase": "mpos-analyze-app-web",
            "attempts": {},
            "completed_phases": ["mpos-plan-app-web"],
            "created_at": _now(),
            "input": {
                "prompt_original": request.prompt,
                "prompt_language": request.prompt_language,
                "prompt_normalized_zh": request.prompt,
                "prompt_normalized_en": request.prompt,
                "ui_locale": request.ui_locale,
                "package_name": request.package_name,
                "display_name": request.display_name,
                "display_name_zh": request.display_name_zh or request.display_name,
                "display_name_en": request.display_name_en or request.display_name,
                "short_description_zh": request.short_description_zh,
                "short_description_en": request.short_description_en,
                "long_description_zh": request.long_description_zh,
                "long_description_en": request.long_description_en,
                "release_notes_zh": request.release_notes_zh,
                "release_notes_en": request.release_notes_en,
                "category": request.category,
                "publisher": request.publisher,
                "version": request.version,
                "targets": request.targets,
            },
            "required_capabilities": required_capabilities,
            "required_accessories": request.required_accessories,
            "runtime_fallbacks": request.runtime_fallbacks,
            "physical_validation_required": physical_validation_required,
            "capability_contract": capability_contract,
            "capabilities": request.capabilities.model_dump(),
            "repo_commit": self.capabilities()["repo_commit"],
            "skills_commit": self.capabilities()["skills_commit"],
            "input_hash": input_hash,
            "api_summary_version": api_summary_version(),
            "board_capabilities_schema": hardware_capability_registry.describe(),
            "permissions": [
                {
                    "permission_id": permission_id,
                    "permission_type": "file_write",
                    "title": "允许创建 App 会话文件",
                    "description": (
                        "在隔离的 session 目录写入源码、检查结果和 MPK；"
                        "不会修改 MicroPythonOS 或 MicroPython_Skills。"
                    ),
                    "risk": "low",
                    "required": True,
                    "command_preview": f"write sessions/{session_id}/project and artifacts",
                    "choices": ["allow_once", "deny"],
                    "decision": "pending",
                    "expires_at": None,
                },
                {
                    "permission_id": network_permission_id,
                    "permission_type": "network_read",
                    "title": "允许调用 AI 生成服务",
                    "description": (
                        "把当前 App 需求和运行错误发送到 AI 生成服务；"
                        "服务凭据只保存在服务端。"
                    ),
                    "risk": "medium",
                    "required": True,
                    "command_preview": "POST AI generation service",
                    "choices": ["allow_once", "deny"],
                    "decision": "pending",
                    "expires_at": None,
                },
                {
                    "permission_id": script_permission_id,
                    "permission_type": "script_run",
                    "title": "允许执行受控检查",
                    "description": "只允许服务器白名单中的语法检查和打包步骤，不能执行任意 shell。",
                    "risk": "medium",
                    "required": True,
                    "command_preview": "python -m py_compile <session app.py>",
                    "choices": ["allow_once", "deny"],
                    "decision": "pending",
                    "expires_at": None,
                },
                {
                    "permission_id": package_permission_id,
                    "permission_type": "package_build",
                    "title": "允许打包 MPK",
                    "description": "读取当前 revision 的 App 文件并写入可下载的 .mpk。",
                    "risk": "low",
                    "required": "package-only" in request.targets
                    or "web-preview" in request.targets
                    or "physical-device" in request.targets,
                    "command_preview": f"build {request.package_name}_r1.mpk",
                    "choices": ["allow_once", "deny"],
                    "decision": "pending",
                    "expires_at": None,
                },
            ]
            + (
                [
                    {
                        "permission_id": serial_permission_id,
                        "permission_type": "serial_scan",
                        "title": "允许扫描串口设备",
                        "description": "只读取可用串口列表，不写入设备。",
                        "risk": "medium",
                        "required": True,
                        "command_preview": "scan serial ports",
                        "choices": ["allow_once", "deny"],
                        "decision": "pending",
                        "expires_at": None,
                    },
                    {
                        "permission_id": device_permission_id,
                        "permission_type": "device_write",
                        "title": "允许部署到设备",
                        "description": "检测到设备后才允许复制 App；当前没有设备时不会执行写入。",
                        "risk": "high",
                        "required": True,
                        "command_preview": "mpremote fs cp <app> :/apps/",
                        "choices": ["allow_once", "deny"],
                        "decision": "pending",
                        "expires_at": None,
                        "separate_confirmation": True,
                    },
                ]
                if "physical-device" in request.targets
                else []
            ),
            "artifacts": [],
            "warnings": [],
            "structured_errors": [],
            "last_error": None,
            "generation": None,
        }
        sensitive_permissions = []
        if "audio.input" in required_capabilities:
            sensitive_permissions.append(("microphone_access", "允许使用麦克风", "App 将在真机读取麦克风输入。", "microphone probe/read"))
        if request.required_accessories:
            sensitive_permissions.append(("external_wiring", "确认外接硬件接线", "请确认配件、电压和接线符合设备说明。", "confirm external accessory wiring"))
        prompt_text = request.prompt.casefold()
        if "storage.sdcard" in required_capabilities and any(term in prompt_text for term in ("format", "格式化")):
            sensitive_permissions.append(("sd_format", "允许格式化 SD 卡", "该操作会清除存储卡数据，必须单独确认。", "format SD card"))
        if any(term in prompt_text for term in ("firmware flash", "flash firmware", "刷固件", "烧录固件")):
            sensitive_permissions.append(("firmware_flash", "允许刷写固件", "固件写入可能导致设备重启，必须单独确认。", "flash device firmware"))
        for permission_type, title, description, command in sensitive_permissions:
            state["permissions"].append({
                "permission_id": f"perm_{permission_type}_{uuid.uuid4().hex[:12]}",
                "permission_type": permission_type,
                "title": title,
                "description": description,
                "risk": "high",
                "required": True,
                "command_preview": command,
                "choices": ["allow_once", "deny"],
                "decision": "pending",
                "expires_at": None,
                "separate_confirmation": True,
            })
        self._write_state(state)
        self._write_artifact_json(
            state,
            "plan_state",
            "mpos-plan-app-web",
            {
                "schema_version": "mpos-plan-app-web-v1",
                "protocol_version": PROTOCOL_VERSION,
                "session_id": session_id,
                "checkpoint_id": "session_created",
                "result": "success",
                "next_phase": "mpos-analyze-app-web",
                "targets": request.targets,
                "capabilities": request.capabilities.model_dump(),
                "required_capabilities": required_capabilities,
                "required_accessories": request.required_accessories,
                "runtime_fallbacks": request.runtime_fallbacks,
                "physical_validation_required": physical_validation_required,
                "capability_contract": state["capability_contract"],
            },
        )
        state["checkpoint_history"].append(self._checkpoint_record(state, "session_created", "mpos-analyze-app-web"))
        self._write_manifest(state)
        self._write_state(state)
        self._event(
            state,
            "phase_complete",
            "mpos-plan-app-web",
            {
                "result": "success",
                "checkpoint_id": "session_created",
                "next_phase": "mpos-analyze-app-web",
            },
        )
        for permission in state["permissions"]:
            if permission["required"]:
                self._event(
                    state,
                    "permission_request",
                    "mpos-plan-app-web",
                    permission,
                )
        return self.get(session_id)

    def decide_permission(
        self, permission_id: str, request: PermissionDecisionRequest
    ) -> dict[str, Any]:
        session_id = self._index.session_for_permission(permission_id)
        if session_id:
            state = self._read(session_id)
            target = next(
                (
                    item
                    for item in state["permissions"]
                    if item["permission_id"] == permission_id
                ),
                None,
            )
            if target:
                if target.get("decision_idempotency_key") == request.idempotency_key:
                    return state
                if target["decision"] != "pending":
                    return state
                target["decision"] = request.decision
                target["decision_idempotency_key"] = request.idempotency_key
                target["decided_at"] = _now()
                if request.decision == "deny":
                    error = {
                        "code": "PERMISSION_DENIED",
                        "message": f"用户拒绝权限：{target['title']}",
                        "stage": "plan",
                        "phase": "mpos-plan-app-web",
                        "owner": "user",
                        "retryable": True,
                        "details": {"permission_id": permission_id},
                        "logs": [],
                    }
                    state["status"] = "blocked"
                    state["last_error"] = error
                    state["structured_errors"].append(error)
                    self._event(state, "structured_error", "mpos-plan-app-web", error)
                else:
                    pending_required = any(
                        item["required"] and item["decision"] == "pending"
                        for item in state["permissions"]
                    )
                    state["status"] = "blocked" if pending_required else "created"
                    state["last_error"] = None
                    self._event(
                        state,
                        "status_update",
                        "mpos-plan-app-web",
                        {"status": "ready", "message": f"权限已确认：{target['title']}"},
                    )
                self._write_state(state)
                return self.get(state["session_id"])
        raise SessionNotFound(permission_id)

    def allow_all_permissions(
        self, session_id: str, request: PermissionBatchDecisionRequest
    ) -> dict[str, Any]:
        state = self._read(session_id)
        if state.get("permission_batch_idempotency_key") == request.idempotency_key:
            return state

        denied = [
            item
            for item in state["permissions"]
            if item.get("required") and item.get("decision") == "deny"
        ]
        if denied:
            raise ValueError("存在已经拒绝的权限，请新建会话后重新确认")

        decided_at = _now()
        changed = False
        for permission in state["permissions"]:
            if not permission.get("required") or permission.get("decision") != "pending":
                continue
            if permission.get("separate_confirmation"):
                continue
            permission["decision"] = "allow_once"
            permission["decision_idempotency_key"] = (
                f"{request.idempotency_key}:{permission['permission_id']}"
            )
            permission["decided_at"] = decided_at
            permission["decision_source"] = "batch_allow_once"
            changed = True
            self._event(
                state,
                "status_update",
                "mpos-plan-app-web",
                {
                    "status": "permission_allowed",
                    "permission_id": permission["permission_id"],
                    "message": f"权限已一键确认：{permission['title']}",
                },
            )

        state["permission_batch_idempotency_key"] = request.idempotency_key
        if changed:
            pending_separate = any(
                item.get("required")
                and item.get("decision") == "pending"
                and item.get("separate_confirmation")
                for item in state["permissions"]
            )
            state["status"] = "blocked" if pending_separate else "created"
            state["last_error"] = None
            self._event(
                state,
                "status_update",
                "mpos-plan-app-web",
                {
                    "status": "blocked" if pending_separate else "ready",
                    "message": "仍有高风险权限需要单独确认" if pending_separate else "全部必需权限已一次性确认",
                },
            )
            self._write_state(state)
        return self.get(session_id)

    def start_generation(
        self, session_id: str, request: SessionActionRequest
    ) -> dict[str, Any]:
        state = self._read(session_id)
        task = self._tasks.get(session_id)
        if task and not task.done():
            return state
        if state.get("last_action_idempotency_key") == request.idempotency_key and state[
            "status"
        ] in {"waiting_preview", "waiting_device", "completed"}:
            return state
        allowed_types = {
            item["permission_type"]
            for item in state["permissions"]
            if item["decision"] == "allow_once"
        }
        required_types = {
            item["permission_type"]
            for item in state["permissions"]
            if item.get("required")
        }
        allowed = required_types.issubset(allowed_types)
        if not allowed:
            return state
        if state["status"] in {"failed", "timeout", "cancelled"}:
            self._archive_failed_attempt(state, request.idempotency_key)
            state = self._read(session_id)
        state["last_action_idempotency_key"] = request.idempotency_key
        previous_code = request.previous_code
        if not previous_code and state.get("generation"):
            previous_code = next(
                (
                    item["content"]
                    for item in state["generation"].get("files", [])
                    if item["path"] in {"assets/main.py", "app.py"}
                ),
                None,
            )
        runtime_error = request.runtime_error
        if not runtime_error and state.get("last_error"):
            runtime_error = json.dumps(
                {
                    "last_error": state["last_error"],
                    "last_result": state.get("generation"),
                    "activity_log_tail": self.events(session_id)[-30:],
                },
                ensure_ascii=False,
            )[-8000:]
        state["pending_repair"] = {
            "previous_code": previous_code,
            "runtime_error": runtime_error,
        }
        state["status"] = "running"
        state["current_phase"] = "mpos-analyze-app-web"
        state["attempts"]["mpos-gen-app-web"] = (
            state["attempts"].get("mpos-gen-app-web", 0) + 1
        )
        state["last_error"] = None
        self._write_state(state)
        self._tasks[session_id] = asyncio.create_task(self._run_generation(session_id))
        return self.get(session_id)

    def _archive_failed_attempt(
        self, state: dict[str, Any], retry_idempotency_key: str
    ) -> None:
        history = state.setdefault("retry_history", [])
        if any(
            item.get("idempotency_key") == retry_idempotency_key
            for item in history
        ):
            return
        attempt_number = len(history) + 1
        root = self._root(state["session_id"])
        archive_root = root / "failed-attempts" / f"attempt-{attempt_number:03d}"
        archive_root.mkdir(parents=True, exist_ok=True)
        for relative in ("session_state.json", "activity_log.jsonl"):
            source = root / relative
            if source.is_file():
                shutil.copy2(source, archive_root / source.name)
        result_files: list[str] = []
        artifacts_root = root / "artifacts"
        for source in artifacts_root.glob("*_result.json"):
            target = archive_root / "artifacts" / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            result_files.append(target.relative_to(root).as_posix())
        archived_generation_run = None
        generation_run = state.get("generation_attempt_run")
        if isinstance(generation_run, str) and generation_run:
            source_run = (root / generation_run).resolve()
            if root in source_run.parents and source_run.is_dir():
                target_run = (
                    archive_root / "generation-attempts" / source_run.name
                )
                shutil.copytree(source_run, target_run)
                archived_generation_run = target_run.relative_to(root).as_posix()
        record = {
            "attempt": attempt_number,
            "idempotency_key": retry_idempotency_key,
            "archived_at": _now(),
            "previous_status": state["status"],
            "previous_checkpoint_id": state.get("checkpoint_id"),
            "previous_error": state.get("last_error"),
            "activity_log": (
                archive_root / "activity_log.jsonl"
            ).relative_to(root).as_posix(),
            "result_files": result_files,
            "generation_attempt_run": archived_generation_run,
        }
        history.append(record)
        self._write_state(state)
        self._event(
            state,
            "status_update",
            "mpos-plan-app-web",
            {
                "status": "retry_scheduled",
                "attempt": attempt_number + 1,
                "previous_checkpoint_id": record["previous_checkpoint_id"],
            },
        )

    def _write_generation_attempt(
        self,
        state: dict[str, Any],
        run_relative: str,
        record: dict[str, Any],
    ) -> None:
        attempt_number = max(1, int(record.get("attempt", 1)))
        attempt_root = (
            self._root(state["session_id"])
            / run_relative
            / f"attempt-{attempt_number:03d}"
        )
        attempt_root.mkdir(parents=True, exist_ok=True)
        candidate = record.get("candidate")
        if isinstance(candidate, str) and candidate:
            (attempt_root / "candidate.py").write_text(candidate, encoding="utf-8")
        validation = record.get("validation")
        _json_dump(
            attempt_root / "validation.json",
            {
                "attempt": attempt_number,
                "status": str(record.get("status") or "unknown"),
                "validation": validation if isinstance(validation, dict) else {},
            },
        )
        raw_model_meta = record.get("model_meta")
        safe_model_meta: dict[str, Any] = {}
        if isinstance(raw_model_meta, dict):
            for key in (
                "provider",
                "model",
                "request_id",
                "routing_tier",
                "routing_reason",
            ):
                value = raw_model_meta.get(key)
                if isinstance(value, str) and value:
                    safe_model_meta[key] = value[:200]
            failover_used = raw_model_meta.get("failover_used")
            if isinstance(failover_used, bool):
                safe_model_meta["failover_used"] = failover_used
            attempted_providers = raw_model_meta.get("attempted_providers")
            if isinstance(attempted_providers, list):
                safe_model_meta["attempted_providers"] = [
                    value
                    for value in attempted_providers
                    if isinstance(value, str) and value in SAFE_AI_PROVIDER_IDS
                ]
            provider_attempts = raw_model_meta.get("provider_attempts")
            if isinstance(provider_attempts, list):
                safe_attempts: list[dict[str, Any]] = []
                for item in provider_attempts:
                    if not isinstance(item, dict):
                        continue
                    provider = item.get("provider")
                    outcome = item.get("outcome")
                    attempt = item.get("attempt")
                    if (
                        provider not in SAFE_AI_PROVIDER_IDS
                        or not isinstance(outcome, str)
                        or not isinstance(attempt, int)
                    ):
                        continue
                    safe_item: dict[str, Any] = {
                        "provider": provider,
                        "attempt": attempt,
                        "outcome": outcome[:40],
                    }
                    status_code = item.get("status_code")
                    if isinstance(status_code, int):
                        safe_item["status_code"] = status_code
                    safe_attempts.append(safe_item)
                safe_model_meta["provider_attempts"] = safe_attempts
            usage = raw_model_meta.get("usage")
            if isinstance(usage, dict):
                safe_usage = {
                    key: int(value)
                    for key in (
                        "prompt_tokens",
                        "completion_tokens",
                        "total_tokens",
                    )
                    if isinstance((value := usage.get(key)), (int, float))
                    and not isinstance(value, bool)
                }
                if safe_usage:
                    safe_model_meta["usage"] = safe_usage
        _json_dump(attempt_root / "model_meta.json", safe_model_meta)
        state["generation_attempt_run"] = run_relative
        self._write_state(state)
        if safe_model_meta:
            self._event(
                state,
                "status_update",
                "mpos-gen-app-web",
                {
                    "status": "generation_attempt",
                    "attempt": attempt_number,
                },
            )

    def start_action(
        self, session_id: str, action: str, request: SessionActionRequest
    ) -> dict[str, Any]:
        if action not in STAGE_SKILLS:
            raise ValueError(f"Unsupported action: {action}")
        state = self._read(session_id)
        task = self._tasks.get(session_id)
        if task and not task.done():
            return state
        action_key = f"stage:{action}:{request.idempotency_key}"
        if state.get("last_stage_action_key") == action_key:
            return state
        required_types = {
            item["permission_type"]
            for item in state["permissions"]
            if item.get("required")
        }
        allowed_types = {
            item["permission_type"]
            for item in state["permissions"]
            if item["decision"] == "allow_once"
        }
        if not required_types.issubset(allowed_types):
            return state
        self._action_by_session[session_id] = action
        state["requested_action"] = action
        state["last_requested_skill"] = mpos_skill_adapter.describe(action)
        state["last_stage_action_key"] = action_key
        state["status"] = "running"
        state["current_phase"] = STAGE_SKILLS[action]
        state["last_error"] = None
        self._write_state(state)
        self._tasks[session_id] = asyncio.create_task(
            self._run_single_stage(session_id, action, request)
        )
        return self.get(session_id)

    def _require_generation(self, state: dict[str, Any], action: str) -> dict[str, Any]:
        generation = state.get("generation")
        if not generation:
            raise ValueError(
                f"STAGE_PREREQUISITE_MISSING: {action} requires generation output"
            )
        return generation

    async def _run_single_stage(
        self,
        session_id: str,
        action: str,
        request: SessionActionRequest,
    ) -> None:
        async with self._locks.setdefault(session_id, asyncio.Lock()):
            state = self._read(session_id)
            phase = STAGE_SKILLS[action]
            try:
                user_input = state["input"]
                self._event(
                    state,
                    "start_phase",
                    phase,
                    {
                        "message": f"独立执行阶段：{action}",
                        **mpos_skill_adapter.describe(action),
                    },
                )
                if action == "analyze":
                    payload = {
                        "schema_version": "mpos-analyze-app-web-v1",
                        "phase": phase,
                        "result": "success",
                        "app": {
                            "fullname": user_input["package_name"],
                            "name": user_input["display_name"],
                            "publisher": user_input["publisher"],
                            "version": user_input["version"],
                        },
                        "language": {
                            "ui_locale": user_input["ui_locale"],
                            "prompt_language": user_input["prompt_language"],
                            "prompt_original": user_input["prompt_original"],
                            "prompt_normalized_zh": user_input["prompt_normalized_zh"],
                            "prompt_normalized_en": user_input["prompt_normalized_en"],
                        },
                        "requirements": {
                            "prompt": user_input["prompt_original"],
                            "required_capabilities": state.get("required_capabilities", []),
                            "required_accessories": state.get("required_accessories", []),
                            "runtime_fallbacks": state.get("runtime_fallbacks", {}),
                            "physical_validation_required": state.get("physical_validation_required", False),
                        },
                        "api_plan": {
                            "mpos_summary": state["api_summary_version"].get(
                                "mpos_api_summary.json"
                            ),
                            "lvgl_summary": state["api_summary_version"].get(
                                "lvgl_api_summary.json"
                            ),
                        },
                        "dependency_plan": {
                            "required": False,
                            "classification": "builtin-mpos-and-app-local-only",
                        },
                        "test_plan": {
                            "targets": user_input["targets"],
                            "capability_contract": state.get("capability_contract", {}),
                        },
                        "warnings": [],
                        "structured_errors": [],
                        "handoff": {"next_phase": "mpos-prepare-deps-web"},
                    }
                    self._write_artifact_json(state, "analysis_result", phase, payload)
                    self._checkpoint(
                        state, phase, "requirements_analyzed", "mpos-prepare-deps-web"
                    )
                elif action == "prepare-deps":
                    payload = {
                        "schema_version": "mpos-prepare-deps-web-v1",
                        "phase": phase,
                        "result": "success",
                        "imports": ["lvgl", "mpos.Activity"],
                        "runtime_files": [],
                        "adapter_requirements": [],
                        "sync_needs_adapter": False,
                        "async_compatible": True,
                        "warnings": [],
                        "structured_errors": [],
                        "handoff": {"next_phase": "mpos-gen-app-web"},
                    }
                    self._write_artifact_json(
                        state, "dependency_handoff", phase, payload
                    )
                    self._checkpoint(
                        state, phase, "dependencies_prepared", "mpos-gen-app-web"
                    )
                elif action == "generate":
                    previous_code = request.previous_code or state.get(
                        "pending_repair", {}
                    ).get("previous_code")
                    generated = await generate_app(
                        GenerateRequest(
                            prompt=user_input["prompt_original"],
                            package_name=user_input["package_name"],
                            display_name=user_input["display_name"],
                            publisher=user_input["publisher"],
                            version=user_input["version"],
                            revision=int(state["revision_id"].removeprefix("r")),
                            previous_code=previous_code,
                            runtime_error=request.runtime_error,
                            required_capabilities=state.get("required_capabilities", []),
                            required_accessories=state.get("required_accessories", []),
                            runtime_fallbacks=state.get("runtime_fallbacks", {}),
                            physical_validation_required=state.get("physical_validation_required", False),
                            ai_provider="auto",
                        )
                    )
                    state["generation"] = generated.model_dump()
                    state["input"]["prompt_normalized_zh"] = (
                        generated.prompt_normalized_zh
                        or user_input["prompt_original"]
                    )
                    state["input"]["prompt_normalized_en"] = (
                        generated.prompt_normalized_en
                        or user_input["prompt_original"]
                    )
                    state["input"].update(generated.store_metadata)
                    app_root = (
                        self._root(session_id)
                        / "project"
                        / "internal_filesystem"
                        / "apps"
                        / generated.package_name
                    )
                    for generated_file in generated.files:
                        if generated_file.path == "generation_result.json":
                            result_payload = json.loads(generated_file.content)
                            result_payload.update({
                                "skill_commit": state.get("skills_commit"),
                                "mpos_commit": state.get("repo_commit"),
                                "board_capabilities_schema": state.get("board_capabilities_schema", {}),
                            })
                            generated_file.content = json.dumps(result_payload, ensure_ascii=False, indent=2)
                        target = (
                            self._root(session_id)
                            / "artifacts"
                            / generated_file.path
                            if generated_file.path == "generation_result.json"
                            else app_root / generated_file.path
                        )
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(generated_file.content, encoding="utf-8")
                        role = {
                            "MANIFEST.JSON": "app_manifest",
                            "assets/main.py": "app_source",
                            "generation_result.json": "generation_result",
                        }[generated_file.path]
                        self._register_artifact(
                            state,
                            target,
                            phase,
                            "result" if role == "generation_result" else "source",
                            role,
                        )
                    self._write_generated_icon(
                        state,
                        app_root,
                        generated.package_name,
                        phase,
                    )
                    self._checkpoint(
                        state, phase, "code_generated", "mpos-test-app-web"
                    )
                elif action == "test":
                    generation = self._require_generation(state, action)
                    app_root = (
                        self._root(session_id)
                        / "project"
                        / "internal_filesystem"
                        / "apps"
                        / generation["package_name"]
                    )
                    syntax_result = script_dispatcher.run(
                        "python_syntax", app_root / "assets" / "main.py"
                    )
                    hardware_policy = _normalize_hardware_policy_result(
                        script_dispatcher.run_hardware_policy(
                            self._root(session_id) / "project",
                            generation["package_name"],
                        )
                    )
                    if not hardware_policy.get("ok"):
                        raise GenerationError(
                            "Generated App violates the MicroPythonOS hardware access policy",
                            code="DIRECT_HARDWARE_ACCESS_FORBIDDEN",
                            owner="app",
                            retryable=True,
                            details=hardware_policy.get("result", hardware_policy),
                        )
                    desktop_requested = "desktop-preview" in user_input["targets"]
                    desktop_result: dict[str, Any] = {
                        "status": "skipped",
                        "reason": "desktop target not selected",
                    }
                    if desktop_requested:
                        smoke_dir = self._root(session_id) / "artifacts" / "desktop-smoke"
                        smoke = script_dispatcher.run_desktop_smoke(
                            PROJECT_ROOT / "vendor" / "MicroPythonOS",
                            generation["package_name"],
                            app_root,
                            self._root(session_id)
                            / "artifacts"
                            / "generation_result.json",
                            smoke_dir,
                        )
                        desktop_result = {
                            "status": (
                                "passed"
                                if smoke.get("ok")
                                else "skipped"
                                if smoke.get("skipped")
                                else "blocked"
                            ),
                            "runner": smoke,
                        }
                        for screenshot in smoke_dir.glob("*.png"):
                            self._register_artifact(
                                state,
                                screenshot,
                                phase,
                                "screenshot",
                                "desktop_screenshot",
                            )
                    payload = {
                        "schema_version": "mpos-test-app-web-v1",
                        "phase": phase,
                        "result": (
                            "success"
                            if syntax_result.get("ok") and hardware_policy.get("ok")
                            and desktop_result["status"] in {"passed", "skipped"}
                            else "blocked"
                        ),
                        "desktop": desktop_result,
                        "web_preview": {
                            "status": (
                                "awaiting_browser"
                                if "web-preview" in user_input["targets"]
                                else "skipped"
                            )
                        },
                        "acceptance_tests": generation.get("acceptance_tests", []),
                        "controlled_checks": {
                            "python_syntax": syntax_result,
                            "hardware_policy": hardware_policy,
                        },
                        "warnings": [],
                        "structured_errors": [],
                        "handoff": {"next_phase": "mpos-package-app-web"},
                    }
                    self._write_artifact_json(state, "app_test_result", phase, payload)
                    self._checkpoint(
                        state,
                        phase,
                        "desktop_test_done",
                        "mpos-package-app-web",
                        result=payload["result"],
                        structured_errors=payload["structured_errors"],
                    )
                elif action == "package":
                    generation = self._require_generation(state, action)
                    mpk_path = (
                        self._root(session_id)
                        / "artifacts"
                        / generation["mpk_filename"]
                    )
                    mpk_path.write_bytes(base64.b64decode(generation["mpk_base64"]))
                    self._register_artifact(state, mpk_path, phase, "package", "mpk")
                    payload = {
                        "schema_version": "mpos-package-app-web-v1",
                        "phase": phase,
                        "result": "success",
                        "package": {
                            "revision": generation["revision"],
                            "mpk_path": f"artifacts/{generation['mpk_filename']}",
                            "filename_policy": "<fullname>_rN.mpk",
                        },
                        "checks": [
                            {"name": "manifest.publisher", "status": "passed"},
                            {"name": "mpk_release_filename", "status": "passed"},
                        ],
                        "warnings": [],
                        "structured_errors": [],
                        "handoff": {"next_phase": "mpos-deploy-app-web"},
                    }
                    self._write_artifact_json(state, "package_result", phase, payload)
                    self._checkpoint(
                        state, phase, "package_done", "mpos-deploy-app-web"
                    )
                elif action == "deploy":
                    payload = {
                        "schema_version": "mpos-deploy-app-web-v1",
                        "phase": phase,
                        "result": "blocked",
                        "mode": "browser-device-handoff",
                        "hardware_available": False,
                        "install_url": "https://install.micropythonos.com/",
                        "warnings": ["等待浏览器 WebSerial 或 mpremote 回传真实设备结果。"],
                        "structured_errors": [],
                        "handoff": {"next_phase": "mpos-publish-app-web"},
                    }
                    self._write_artifact_json(state, "deploy_result", phase, payload)
                    self._checkpoint(
                        state,
                        phase,
                        "device_deploy_pending",
                        "mpos-publish-app-web",
                        result=payload["result"],
                        structured_errors=payload["structured_errors"],
                    )
                else:
                    generation = self._require_generation(state, action)
                    screenshots = [
                        item
                        for item in state["artifacts"]
                        if item["role"] in {"desktop_screenshot", "publish_screenshot"}
                    ]
                    payload = {
                        "schema_version": "mpos-publish-app-web-v1",
                        "phase": phase,
                        "result": "success" if screenshots else "partial",
                        "status": (
                            "ready_for_manual_upload"
                            if screenshots
                            else "needs_preview_and_screenshot"
                        ),
                        "publish_ready": False,
                        "upystore": {
                            "home_url": "https://upystore.io/",
                            "developer_url": "https://upystore.io/developer",
                            "mode": "manual_guidance",
                            "version_status": "unknown_unverified",
                        },
                        "app_metadata": generation.get("store_metadata", {}),
                        "mpk": {
                            "filename": generation["mpk_filename"],
                            "revision": generation["revision"],
                        },
                        "screenshots": screenshots,
                        "checks": [
                            {
                                "name": "publish_screenshot",
                                "status": "passed" if screenshots else "pending",
                            }
                        ],
                        "blockers": [] if screenshots else ["publish_screenshot"],
                        "warnings": (
                            []
                            if screenshots
                            else ["请上传 PNG、JPEG 或 WebP 截图后再发布。"]
                        ),
                        "structured_errors": [],
                        "handoff": {"next_phase": None},
                    }
                    self._write_artifact_json(state, "publish_result", phase, payload)
                    self._write_publish_bundle(state)
                    self._checkpoint(
                        state,
                        phase,
                        "publish_check_done",
                        None,
                        result=payload["result"],
                        structured_errors=payload["structured_errors"],
                    )
                state = self._read(session_id)
                state["status"] = "completed"
                state["current_phase"] = phase
                state["next_phase"] = None
                if action == "publish-check":
                    self._apply_final_artifact_gate(
                        state, completion_requested=True
                    )
                self._write_state(state)
            except (GenerationError, OSError, ValueError) as exc:
                state = self._read(session_id)
                message = str(exc)
                code = getattr(
                    exc,
                    "code",
                    (
                        message.split(":", 1)[0]
                        if re.match(r"^[A-Z][A-Z0-9_]+:", message)
                        else "STAGE_EXECUTION_FAILED"
                    ),
                )
                resume_checkpoint_id = state.get("checkpoint_id")
                error = {
                    "code": code,
                    "message": getattr(
                        exc, "message", message.split(":", 1)[-1].strip()
                    ),
                    "stage": action,
                    "phase": phase,
                    "owner": getattr(exc, "owner", "external" if code.startswith("AI_UPSTREAM_") else "app"),
                    "retryable": getattr(exc, "retryable", True),
                    "details": {
                        "resume_checkpoint_id": resume_checkpoint_id,
                        **getattr(exc, "details", {}),
                    },
                    "logs": ["activity_log.jsonl"],
                }
                state["status"] = "failed"
                state["checkpoint_id"] = "failed"
                state["resume_checkpoint_id"] = resume_checkpoint_id
                state["next_phase"] = phase
                state["last_error"] = error
                state["structured_errors"].append(error)
                self._write_state(state)
                self._event(state, "structured_error", phase, error)

    def resume(
        self, session_id: str, idempotency_key: str
    ) -> dict[str, Any]:
        state = self._read(session_id)
        if state.get("resume_idempotency_key") == idempotency_key:
            return state
        state["resume_idempotency_key"] = idempotency_key
        next_phase = state.get("next_phase")
        phase_to_action = {
            value: key for key, value in STAGE_SKILLS.items()
        }
        action = phase_to_action.get(next_phase, "generate")
        self._write_state(state)
        return self.start_action(
            session_id,
            action,
            SessionActionRequest(
                idempotency_key=f"resume-action-{idempotency_key}",
                runtime_error=(
                    json.dumps(state.get("last_error"), ensure_ascii=False)
                    if state.get("last_error")
                    else None
                ),
            ),
        )

    def scan_devices(self, session_id: str, idempotency_key: str) -> dict[str, Any]:
        state = self._read(session_id)
        allowed = any(
            item["permission_type"] == "serial_scan"
            and item["decision"] == "allow_once"
            for item in state["permissions"]
        )
        if not allowed:
            return {
                "session_id": session_id,
                "status": "blocked",
                "error": {
                    "code": "PERMISSION_DENIED",
                    "message": "扫描串口前需要用户授权",
                    "stage": "deploy",
                    "owner": "user",
                    "retryable": True,
                    "details": {},
                    "logs": [],
                },
            }
        if state.get("device_scan_idempotency_key") == idempotency_key:
            return state.get("last_device_scan", device_service.scan())
        result = device_service.scan()
        state["device_scan_idempotency_key"] = idempotency_key
        state["last_device_scan"] = result
        self._write_state(state)
        self._event(state, "status_update", "mpos-deploy-app-web", result)
        return result

    def create_revision(
        self, session_id: str, request: RevisionRequest
    ) -> dict[str, Any]:
        state = self._read(session_id)
        task = self._tasks.get(session_id)
        if task and not task.done():
            return state
        if state.get("revision_idempotency_key") == request.idempotency_key:
            return state

        current_revision = int(state["revision_id"].removeprefix("r"))
        previous_generation = state.get("generation") or {}
        previous_code = next(
            (
                item.get("content")
                for item in previous_generation.get("files", [])
                if item.get("path") in {"assets/main.py", "app.py"}
            ),
            None,
        )
        snapshot_root = self._root(session_id) / "revisions" / f"r{current_revision}"
        snapshot_root.mkdir(parents=True, exist_ok=True)
        for name in ("project", "artifacts"):
            source = self._root(session_id) / name
            target = snapshot_root / name
            if source.exists() and not target.exists():
                shutil.copytree(source, target)
        for name in ("session_state.json", "activity_log.jsonl", "artifact_manifest.json"):
            source = self._root(session_id) / name
            target = snapshot_root / name
            if source.is_file() and not target.exists():
                shutil.copy2(source, target)

        state["revision_id"] = f"r{current_revision + 1}"
        state["revision_idempotency_key"] = request.idempotency_key
        state["billing"] = {
            "charge_on_success": True,
            "settled": False,
            "settled_at": None,
            "idempotency_key": (
                f"generation:{session_id}:{state['revision_id']}"
            ),
            "action_idempotency_key": None,
            "unlimited": False,
            "exempt_reason": None,
        }
        state["input"]["prompt_original"] = request.prompt
        state["input"]["prompt_language"] = request.prompt_language
        state["input"]["prompt_normalized_zh"] = request.prompt
        state["input"]["prompt_normalized_en"] = request.prompt
        if request.required_capabilities is not None:
            state["required_capabilities"] = request.required_capabilities
        if request.required_accessories is not None:
            state["required_accessories"] = request.required_accessories
        if request.runtime_fallbacks is not None:
            state["runtime_fallbacks"] = request.runtime_fallbacks
        if request.physical_validation_required is not None:
            state["physical_validation_required"] = request.physical_validation_required
        state["capability_contract"] = hardware_capability_registry.resolve(
            state.get("required_capabilities", []), state.get("runtime_fallbacks", {})
        )
        state["status"] = "created"
        state["current_phase"] = "mpos-analyze-app-web"
        state["checkpoint_id"] = "session_created"
        state["next_phase"] = "mpos-analyze-app-web"
        state["generation"] = None
        state["artifacts"] = []
        state["last_error"] = None
        state["structured_errors"] = []
        state["pending_repair"] = {
            "previous_code": previous_code,
            "runtime_error": (
                f"用户正在基于 r{current_revision} 连续修改到 "
                f"r{current_revision + 1}，必须保留未要求删除的功能。"
            ),
        }
        state["completed_phases"] = ["mpos-plan-app-web"]
        for name in ("project", "artifacts"):
            path = self._root(session_id) / name
            if path.exists():
                shutil.rmtree(path)
            path.mkdir()
        self._write_state(state)
        self._event(
            state,
            "status_update",
            "mpos-plan-app-web",
            {
                "status": "revision_created",
                "message": f"已基于上一成功版本创建 {state['revision_id']}",
            },
        )
        return self.get(session_id)

    async def _run_generation(self, session_id: str) -> None:
        async with self._locks.setdefault(session_id, asyncio.Lock()):
            state = self._read(session_id)
            try:
                user_input = state["input"]
                self._event(
                    state,
                    "start_phase",
                    "mpos-analyze-app-web",
                    {
                        "message": "正在分析原始需求与 App 元信息",
                        **mpos_skill_adapter.describe("analyze"),
                    },
                )
                analysis = {
                    "schema_version": "mpos-analyze-app-web-v1",
                    "phase": "mpos-analyze-app-web",
                    "result": "success",
                    "app": {
                        "fullname": user_input["package_name"],
                        "name": user_input["display_name"],
                        "publisher": user_input["publisher"],
                        "version": user_input["version"],
                    },
                    "manifest_draft": {
                        "fullname": user_input["package_name"],
                        "name": user_input["display_name"],
                        "publisher": user_input["publisher"],
                        "version": user_input["version"],
                        "category": user_input.get("category", "generated"),
                    },
                    "language": {
                        "ui_locale": user_input["ui_locale"],
                        "prompt_language": user_input["prompt_language"],
                        "prompt_original": user_input["prompt_original"],
                        "prompt_normalized_zh": user_input["prompt_normalized_zh"],
                        "prompt_normalized_en": user_input["prompt_normalized_en"],
                    },
                    "requirements": {
                        "prompt": user_input["prompt_original"],
                        "continuous_modification": bool(
                            state.get("pending_repair", {}).get("previous_code")
                        ),
                    },
                    "api_plan": {
                        "mpos_summary": state["api_summary_version"].get(
                            "mpos_api_summary.json"
                        ),
                        "lvgl_summary": state["api_summary_version"].get(
                            "lvgl_api_summary.json"
                        ),
                        "full_read_required": True,
                    },
                    "dependency_plan": {
                        "required": False,
                        "classification": "builtin-mpos-and-app-local-only",
                    },
                    "test_plan": {
                        "targets": user_input["targets"],
                        "capability_contract": state.get("capability_contract", {}),
                    },
                    "deploy_plan": {
                        "physical_requested": "physical-device"
                        in user_input["targets"],
                        "install_url": "https://install.micropythonos.com/",
                    },
                    "warnings": [],
                    "structured_errors": [],
                    "handoff": {"next_phase": "mpos-gen-app-web"},
                }
                self._write_artifact_json(
                    state, "analysis_result", "mpos-analyze-app-web", analysis
                )
                self._checkpoint(
                    state,
                    "mpos-analyze-app-web",
                    "requirements_analyzed",
                    "mpos-prepare-deps-web",
                )
                dependency_handoff = {
                    "schema_version": "mpos-prepare-deps-web-v1",
                    "phase": "mpos-prepare-deps-web",
                    "result": "success",
                    "imports": ["lvgl", "mpos.Activity"],
                    "runtime_files": [],
                    "adapter_requirements": [],
                    "sync_needs_adapter": False,
                    "async_compatible": True,
                    "warnings": [],
                    "structured_errors": [],
                    "handoff": {"next_phase": "mpos-gen-app-web"},
                }
                self._write_artifact_json(
                    state,
                    "dependency_handoff",
                    "mpos-prepare-deps-web",
                    dependency_handoff,
                )
                self._checkpoint(
                    state,
                    "mpos-prepare-deps-web",
                    "dependencies_prepared",
                    "mpos-gen-app-web",
                )

                state["current_phase"] = "mpos-gen-app-web"
                self._write_state(state)
                self._event(
                    state,
                    "start_phase",
                    "mpos-gen-app-web",
                    {
                        "message": "AI 正在生成并修复 MicroPythonOS App",
                        **mpos_skill_adapter.describe("generate"),
                    },
                )
                generation_run = (
                    "generation-attempts/"
                    f"run-{int(state['attempts'].get('mpos-gen-app-web', 1)):03d}"
                )
                state["generation_attempt_run"] = generation_run
                self._write_state(state)
                generated = await generate_app(
                    GenerateRequest(
                        prompt=user_input["prompt_original"],
                        package_name=user_input["package_name"],
                        display_name=user_input["display_name"],
                        publisher=user_input["publisher"],
                        version=user_input["version"],
                        revision=int(state["revision_id"].removeprefix("r")),
                        previous_code=state.get("pending_repair", {}).get("previous_code"),
                        runtime_error=state.get("pending_repair", {}).get("runtime_error"),
                        required_capabilities=state.get("required_capabilities", []),
                        required_accessories=state.get("required_accessories", []),
                        runtime_fallbacks=state.get("runtime_fallbacks", {}),
                        physical_validation_required=state.get("physical_validation_required", False),
                        ai_provider="auto",
                    ),
                    attempt_sink=lambda record: self._write_generation_attempt(
                        state, generation_run, record
                    ),
                )
                generation_data = generated.model_dump()
                state["generation"] = generation_data
                state["input"]["prompt_normalized_zh"] = (
                    generated.prompt_normalized_zh or user_input["prompt_original"]
                )
                state["input"]["prompt_normalized_en"] = (
                    generated.prompt_normalized_en or user_input["prompt_original"]
                )
                state["input"].update(generated.store_metadata)
                self._checkpoint(
                    state,
                    "mpos-gen-app-web",
                    "api_checked",
                    "mpos-gen-app-web",
                )
                app_root = (
                    self._root(session_id)
                    / "project"
                    / "internal_filesystem"
                    / "apps"
                    / generated.package_name
                )
                for generated_file in generated.files:
                    if generated_file.path == "generation_result.json":
                        result_payload = json.loads(generated_file.content)
                        result_payload.update({
                            "skill_commit": state.get("skills_commit"),
                            "mpos_commit": state.get("repo_commit"),
                            "board_capabilities_schema": state.get("board_capabilities_schema", {}),
                        })
                        generated_file.content = json.dumps(result_payload, ensure_ascii=False, indent=2)
                    if generated_file.path == "generation_result.json":
                        target = self._root(session_id) / "artifacts" / generated_file.path
                    else:
                        target = app_root / generated_file.path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(generated_file.content, encoding="utf-8")
                    role = {
                        "MANIFEST.JSON": "app_manifest",
                        "assets/main.py": "app_source",
                        "generation_result.json": "generation_result",
                    }[generated_file.path]
                    self._register_artifact(
                        state,
                        target,
                        "mpos-gen-app-web",
                        "source" if role != "generation_result" else "result",
                        role,
                    )
                self._write_generated_icon(
                    state,
                    app_root,
                    generated.package_name,
                    "mpos-gen-app-web",
                )
                previous_code = state.get("pending_repair", {}).get("previous_code")
                generated_code = next(
                    (
                        item.content
                        for item in generated.files
                        if item.path == "assets/main.py"
                    ),
                    "",
                )
                if previous_code and generated_code:
                    diff_text = "".join(
                        difflib.unified_diff(
                            previous_code.splitlines(keepends=True),
                            generated_code.splitlines(keepends=True),
                            fromfile=f"{state['revision_id']}-previous/assets/main.py",
                            tofile=f"{state['revision_id']}/assets/main.py",
                        )
                    )
                    diff_path = (
                        self._root(session_id)
                        / "artifacts"
                        / f"{state['revision_id']}_changes.patch"
                    )
                    diff_path.write_text(
                        diff_text or "# No textual changes\n", encoding="utf-8"
                    )
                    self._register_artifact(
                        state,
                        diff_path,
                        "mpos-gen-app-web",
                        "diff",
                        "revision_diff",
                    )
                self._checkpoint(
                    state,
                    "mpos-gen-app-web",
                    "code_generated",
                    "mpos-test-app-web",
                )

                state["current_phase"] = "mpos-test-app-web"
                web_requested = "web-preview" in user_input["targets"]
                desktop_requested = "desktop-preview" in user_input["targets"]
                syntax_result = script_dispatcher.run(
                    "python_syntax", app_root / "assets" / "main.py"
                )
                hardware_policy = _normalize_hardware_policy_result(
                    script_dispatcher.run_hardware_policy(
                        self._root(session_id) / "project", generated.package_name
                    )
                )
                if not hardware_policy.get("ok"):
                    raise GenerationError(
                        "Generated App violates the MicroPythonOS hardware access policy",
                        code="DIRECT_HARDWARE_ACCESS_FORBIDDEN",
                        owner="app",
                        retryable=True,
                        details=hardware_policy.get("result", hardware_policy),
                    )
                desktop_result: dict[str, Any] = {
                    "status": "skipped",
                    "reason": (
                        "desktop target not selected"
                        if not desktop_requested
                        else "desktop_preview capability is unavailable on this host"
                    ),
                }
                if desktop_requested and state["capabilities"].get(
                    "desktop_preview"
                ):
                    smoke_dir = (
                        self._root(session_id) / "artifacts" / "desktop-smoke"
                    )
                    smoke = script_dispatcher.run_desktop_smoke(
                        PROJECT_ROOT / "vendor" / "MicroPythonOS",
                        generated.package_name,
                        app_root,
                        self._root(session_id)
                        / "artifacts"
                        / "generation_result.json",
                        smoke_dir,
                    )
                    desktop_result = {
                        "status": (
                            "passed"
                            if smoke.get("ok")
                            else "skipped"
                            if smoke.get("skipped")
                            else "blocked"
                        ),
                        "runner": smoke,
                    }
                    for screenshot in smoke_dir.glob("*.png"):
                        self._register_artifact(
                            state,
                            screenshot,
                            "mpos-test-app-web",
                            "screenshot",
                            "desktop_screenshot",
                        )
                test_result = {
                    "schema_version": "mpos-test-app-web-v1",
                    "phase": "mpos-test-app-web",
                    "result": "partial",
                    "desktop": desktop_result,
                    "web_preview": {
                        "status": "awaiting_browser" if web_requested else "skipped",
                        "reason": None if web_requested else "target not selected",
                        "notice": (
                            "Web preview is a quick browser compatibility preview. "
                            "It does not replace real hardware deployment."
                        ),
                    },
                    "acceptance_tests": generated.acceptance_tests,
                    "controlled_checks": {
                        "python_syntax": syntax_result,
                        "hardware_policy": hardware_policy,
                        "arbitrary_shell_allowed": False,
                    },
                    "warnings": [
                        "真实硬件尚未验证；涉及设备能力时必须部署到 MicroPythonOS 设备。"
                    ],
                    "structured_errors": [],
                    "handoff": {"next_phase": "mpos-package-app-web"},
                }
                self._write_artifact_json(
                    state, "app_test_result", "mpos-test-app-web", test_result
                )
                self._checkpoint(
                    state,
                    "mpos-test-app-web",
                    "desktop_test_done",
                    "browser_web_preview" if web_requested else "mpos-package-app-web",
                    result=test_result["result"],
                    structured_errors=test_result["structured_errors"],
                )

                state["current_phase"] = "mpos-package-app-web"
                mpk_path = (
                    self._root(session_id)
                    / "artifacts"
                    / generated.mpk_filename
                )
                mpk_path.write_bytes(base64.b64decode(generated.mpk_base64))
                self._register_artifact(
                    state, mpk_path, "mpos-package-app-web", "package", "mpk"
                )
                package_result = {
                    "schema_version": "mpos-package-app-web-v1",
                    "phase": "mpos-package-app-web",
                    "result": "success",
                    "app": analysis["app"],
                    "package": {
                        "revision": generated.revision,
                        "mpk_path": f"artifacts/{generated.mpk_filename}",
                        "filename_policy": "<fullname>_rN.mpk",
                    },
                    "checks": [
                        {"name": "manifest.publisher", "status": "passed"},
                        {"name": "mpk_release_filename", "status": "passed"},
                        {
                            "name": "icon_64x64.png",
                            "status": (
                                "passed"
                                if any(
                                    item["path"].endswith("icon_64x64.png")
                                    for item in state["artifacts"]
                                )
                                else "warning"
                            ),
                        },
                    ],
                    "warnings": (
                        []
                        if any(
                            item["path"].endswith("icon_64x64.png")
                            for item in state["artifacts"]
                        )
                        else ["当前 App 尚未生成 icon_64x64.png。"]
                    ),
                    "structured_errors": [],
                    "handoff": {"next_phase": "mpos-deploy-app-web"},
                }
                self._write_artifact_json(
                    state, "package_result", "mpos-package-app-web", package_result
                )
                self._write_artifact_json(
                    state,
                    "app_index_entry",
                    "mpos-package-app-web",
                    {
                        "fullname": generated.package_name,
                        "name": generated.store_metadata.get(
                            "display_name_en", user_input["display_name"]
                        ),
                        "publisher": user_input["publisher"],
                        "version": user_input["version"],
                        "release": generated.revision,
                        "mpk": generated.mpk_filename,
                        "category": generated.store_metadata.get(
                            "category", "generated"
                        ),
                    },
                )
                self._checkpoint(
                    state,
                    "mpos-package-app-web",
                    "package_done",
                    "mpos-deploy-app-web",
                )

                physical_requested = "physical-device" in user_input["targets"]
                deploy_result = {
                    "schema_version": "mpos-deploy-app-web-v1",
                    "phase": "mpos-deploy-app-web",
                    "result": "blocked" if physical_requested else "partial",
                    "mode": "install-site" if physical_requested else "web-preview",
                    "hardware_available": False,
                    "board": None,
                    "serial_port": None,
                    "micropythonos_installed": "unknown",
                    "install_url": "https://install.micropythonos.com/",
                    "permissions": [],
                    "warnings": [
                        (
                            "尚未接入串口设备。请先安装 MicroPythonOS，再回来部署 App。"
                            if physical_requested
                            else "当前记录仅为 Web preview，尚未完成真实硬件验证。"
                        )
                    ],
                    "structured_errors": (
                        [
                            {
                                "code": "DEVICE_NOT_CONNECTED",
                                "message": "当前后端未检测到可部署的串口设备",
                                "stage": "deploy",
                                "phase": "mpos-deploy-app-web",
                                "owner": "device",
                                "retryable": True,
                                "details": {
                                    "install_url": "https://install.micropythonos.com/"
                                },
                                "logs": [],
                            }
                        ]
                        if physical_requested
                        else []
                    ),
                    "handoff": {"next_phase": "mpos-publish-app-web"},
                }
                self._write_artifact_json(
                    state, "deploy_result", "mpos-deploy-app-web", deploy_result
                )
                self._checkpoint(
                    state,
                    "mpos-deploy-app-web",
                    "device_deploy_done",
                    "mpos-publish-app-web",
                    result=deploy_result["result"],
                    structured_errors=deploy_result["structured_errors"],
                )
                publish_result = {
                    "schema_version": "mpos-publish-app-web-v1",
                    "phase": "mpos-publish-app-web",
                    "result": "partial",
                    "status": "needs_preview_and_screenshot",
                    "publish_ready": False,
                    "upystore": {
                        "home_url": "https://upystore.io/",
                        "developer_url": "https://upystore.io/developer",
                        "mode": "manual_guidance",
                        "version_status": "unknown_unverified",
                    },
                    "app_metadata": generated.store_metadata,
                    "mpk": {
                        "filename": generated.mpk_filename,
                        "revision": generated.revision,
                    },
                    "checks": package_result["checks"]
                    + [
                        {"name": "publish_screenshot", "status": "pending"},
                        {
                            "name": "bilingual_descriptions",
                            "status": (
                                "passed"
                                if generated.store_metadata.get(
                                    "short_description_zh"
                                )
                                and generated.store_metadata.get(
                                    "short_description_en"
                                )
                                else "pending"
                            ),
                        },
                    ],
                    "blockers": ["publish_screenshot"],
                    "warnings": deploy_result["warnings"] + [
                        "完成 Web/设备验证并准备 PNG、JPEG 或 WebP 截图后再手工上传。"
                    ],
                    "structured_errors": [],
                    "handoff": {"next_phase": None},
                }
                self._write_artifact_json(
                    state, "publish_result", "mpos-publish-app-web", publish_result
                )
                self._write_publish_bundle(state)
                self._checkpoint(
                    state,
                    "mpos-publish-app-web",
                    "publish_check_done",
                    "browser_web_preview" if web_requested else None,
                    result=publish_result["result"],
                    structured_errors=publish_result["structured_errors"],
                )
                state["status"] = (
                    "waiting_preview"
                    if web_requested
                    else "waiting_device"
                    if physical_requested
                    else "completed"
                )
                state["current_phase"] = (
                    "mpos-test-app-web" if web_requested else "mpos-publish-app-web"
                )
                state["checkpoint_id"] = (
                    "publish_check_done"
                    if web_requested or physical_requested
                    else "completed"
                )
                state["next_phase"] = (
                    "browser_web_preview"
                    if web_requested
                    else "mpos-deploy-app-web"
                    if physical_requested
                    else None
                )
                state["warnings"] = test_result["warnings"] + publish_result["warnings"]
                self._apply_final_artifact_gate(
                    state, completion_requested=state["status"] == "completed"
                )
                self._write_manifest(state)
                self._write_session_bundle(state)
                self._write_manifest(state)
                self._write_state(state)
                self._event(
                    state,
                    "status_update",
                    state["current_phase"],
                    {
                        "status": state["status"],
                        "message": (
                            "源码和 MPK 已生成，等待浏览器 WASM 验证"
                            if web_requested
                            else "最终发布产物不完整，请按错误详情补齐"
                            if state["status"] == "blocked"
                            else "所选生成、检查和打包阶段已完成"
                        ),
                    },
                )
            except asyncio.CancelledError:
                state = self._read(session_id)
                state["status"] = "cancelled"
                state["checkpoint_id"] = "cancelled"
                state["next_phase"] = None
                self._write_state(state)
                self._event(
                    state,
                    "phase_complete",
                    state["current_phase"],
                    {"result": "cancelled", "checkpoint_id": "cancelled"},
                )
            # A generation job runs in a detached asyncio task.  Restricting this
            # handler to the expected provider/filesystem errors lets an
            # unexpected contract or serialization error escape the task.  The
            # session then has no structured failure and the UI can only show
            # the misleading generic "Generation failed" message.  Persist every
            # ordinary exception here; CancelledError is handled separately just
            # above and still retains its cancellation semantics.
            except Exception as exc:
                state = self._read(session_id)
                message = str(exc)
                code = getattr(
                    exc,
                    "code",
                    (
                        message.split(":", 1)[0]
                        if re.match(r"^[A-Z][A-Z0-9_]+:", message)
                        else "APP_GENERATION_FAILED"
                    ),
                )
                resume_checkpoint_id = state.get("checkpoint_id")
                error = {
                    "code": code,
                    "message": getattr(
                        exc, "message", message.split(":", 1)[-1].strip()
                    ),
                    "stage": "generation",
                    "phase": "mpos-gen-app-web",
                    "owner": getattr(exc, "owner", "external" if code.startswith("AI_UPSTREAM_") else "app"),
                    "retryable": getattr(exc, "retryable", True),
                    "details": {
                        "attempt": state["attempts"].get("mpos-gen-app-web", 1),
                        "resume_checkpoint_id": resume_checkpoint_id,
                        "exception_type": type(exc).__name__,
                        **getattr(exc, "details", {}),
                    },
                    "logs": ["activity_log.jsonl"],
                }
                state["status"] = "failed"
                state["checkpoint_id"] = "failed"
                state["resume_checkpoint_id"] = resume_checkpoint_id
                state["next_phase"] = "mpos-gen-app-web"
                state["last_error"] = error
                state["structured_errors"].append(error)
                self._write_state(state)
                self._event(state, "structured_error", "mpos-gen-app-web", error)
                self._event(
                    state,
                    "phase_complete",
                    "mpos-gen-app-web",
                    {
                        "result": "failed",
                        "checkpoint_id": "failed",
                        "next_phase": "mpos-gen-app-web",
                        "structured_errors": [error],
                    },
                )

    def _checkpoint_record(
        self,
        state: dict[str, Any],
        checkpoint: str,
        next_phase: str | None,
        phase: str = "mpos-plan-app-web",
    ) -> dict[str, Any]:
        action = next(
            (name for name, skill in STAGE_SKILLS.items() if skill == phase),
            None,
        )
        skill = (
            mpos_skill_adapter.describe(action)
            if action
            else {
                "skill": "mpos-plan-app-web",
                "skill_version": "repository",
                "skill_sha256": "n/a",
                "skill_path": "vendor/MicroPython_Skills/mpos-plan-app-web/SKILL.md",
            }
        )
        return {
            "checkpoint_id": checkpoint,
            "ts": _now(),
            "input": state["input"],
            "input_hash": state.get("input_hash"),
            **skill,
            "micropythonos_commit": state.get("repo_commit"),
            "micropython_skills_commit": state.get("skills_commit"),
            "skill_commit": state.get("skills_commit"),
            "mpos_commit": state.get("repo_commit"),
            "api_summary_version": state.get("api_summary_version", {}),
            "board_capabilities_schema": state.get("board_capabilities_schema", {}),
            "required_capabilities": state.get("required_capabilities", []),
            "required_accessories": state.get("required_accessories", []),
            "runtime_fallbacks": state.get("runtime_fallbacks", {}),
            "physical_validation_required": state.get("physical_validation_required", False),
            "output_files": [item["path"] for item in state.get("artifacts", [])],
            "warnings": state.get("warnings", []),
            "error": state.get("last_error"),
            "next_phase": next_phase,
        }

    def _checkpoint(
        self,
        state: dict[str, Any],
        phase: str,
        checkpoint: str,
        next_phase: str | None,
        *,
        result: str = "success",
        structured_errors: list[dict[str, Any]] | None = None,
    ) -> None:
        state["checkpoint_id"] = checkpoint
        state["next_phase"] = next_phase
        if phase not in state["completed_phases"]:
            state["completed_phases"].append(phase)
        state.setdefault("checkpoint_history", []).append(
            self._checkpoint_record(state, checkpoint, next_phase, phase)
        )
        phase_payload = {
            "protocol_version": PROTOCOL_VERSION,
            "session_id": state["session_id"],
            "stage": phase.removeprefix("mpos-").removesuffix("-app-web"),
            "phase": phase,
            "result": result,
            "checkpoint_id": checkpoint,
            "next_phase": next_phase,
            "result_path": next(
                (
                    item["path"]
                    for item in reversed(state.get("artifacts", []))
                    if item["phase"] == phase and item["kind"] == "result"
                ),
                None,
            ),
            "artifact_manifest_path": "artifact_manifest.json",
            "warnings": state.get("warnings", []),
            "structured_errors": structured_errors or [],
        }
        phase_name = phase.replace("-", "_")
        self._write_artifact_json(
            state,
            f"phase_complete.{phase_name}",
            phase,
            phase_payload,
        )
        self._write_manifest(state)
        self._write_state(state)
        self._event(
            state,
            "phase_complete",
            phase,
            {**phase_payload, "artifacts": state["artifacts"]},
        )

    def _write_generated_icon(
        self,
        state: dict[str, Any],
        app_root: Path,
        package_name: str,
        phase: str,
    ) -> None:
        icon_path = app_root / "icon_64x64.png"
        icon_path.parent.mkdir(parents=True, exist_ok=True)
        icon_path.write_bytes(_default_icon_png(package_name))
        self._register_artifact(
            state,
            icon_path,
            phase,
            "source",
            "app_icon",
        )

    def _normalize_deploy_result(
        self, state: dict[str, Any], value: dict[str, Any]
    ) -> dict[str, Any]:
        result = dict(value)
        decision_map = {
            "allow_once": "granted",
            "deny": "denied",
            "denied": "denied",
            "expired": "expired",
            "cancelled": "cancelled",
        }
        permission_decisions = []
        for permission in state.get("permissions", []):
            permission_id = str(permission.get("permission_id", ""))
            decision = decision_map.get(str(permission.get("decision", "")))
            if permission_id.startswith("perm_") and decision:
                permission_decisions.append(
                    {"permission_id": permission_id, "decision": decision}
                )
        result.setdefault("schema_version", "mpos-deploy-app-web-v1")
        result.setdefault("phase", "mpos-deploy-app-web")
        result.setdefault("result", "partial")
        result.setdefault("mode", "web-preview")
        result.setdefault("hardware_available", False)
        result.setdefault("board", None)
        result.setdefault("serial_port", None)
        result.setdefault("micropythonos_installed", "unknown")
        result.setdefault("permission_decisions", permission_decisions)
        result.setdefault("commands", [])
        result.setdefault("logs", [])
        result.setdefault("client_attested", False)
        result.setdefault("server_verified", False)
        result.setdefault("warnings", [])
        result.setdefault("structured_errors", [])
        result.setdefault("handoff", {"next_phase": "mpos-publish-app-web"})
        return result

    def _normalize_publish_result(
        self, state: dict[str, Any], value: dict[str, Any]
    ) -> dict[str, Any]:
        result = dict(value)
        user_input = state.get("input", {})
        metadata = result.get("app_metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        app = result.get("app")
        if not isinstance(app, dict):
            app = {}
        app.setdefault("fullname", user_input.get("package_name", "com.example.app"))
        app.setdefault(
            "name",
            metadata.get("display_name_en")
            or metadata.get("display_name_zh")
            or user_input.get("display_name", "Generated App"),
        )
        app.setdefault("publisher", user_input.get("publisher", "unknown"))
        app.setdefault("version", user_input.get("version", "0.1.0"))
        app.setdefault("metadata", metadata)

        mpk = result.get("mpk")
        if not isinstance(mpk, dict):
            mpk = {}
        mpk_artifact = next(
            (
                item
                for item in reversed(state.get("artifacts", []))
                if item.get("role") == "mpk"
            ),
            None,
        )
        revision_id = str(state.get("revision_id", "r1"))
        fallback_filename = (
            f"{user_input.get('package_name', 'com.example.app')}_{revision_id}.mpk"
        )
        filename = str(
            mpk.get("filename")
            or (Path(str(mpk_artifact.get("path", ""))).name if mpk_artifact else "")
            or fallback_filename
        )
        mpk.setdefault("filename", filename)
        mpk.setdefault(
            "path",
            str(mpk_artifact.get("path"))
            if mpk_artifact
            else f"artifacts/{filename}",
        )

        screenshot_artifacts = []
        for artifact in state.get("artifacts", []):
            if artifact.get("role") not in {
                "desktop_screenshot",
                "publish_screenshot",
            }:
                continue
            path = self._final_artifact_path(state, artifact)
            if path and self._valid_publish_screenshot(
                path, str(artifact.get("mime", ""))
            ):
                screenshot_artifacts.append(artifact)
        screenshot_ids = [
            str(item["id"])
            for item in screenshot_artifacts
            if item.get("id")
        ]
        screenshot_ready = bool(screenshot_ids)
        publish_ready = bool(result.get("publish_ready", False))
        release_readiness = (
            "ready_for_manual_upload"
            if publish_ready
            else "blocked"
            if result.get("result") == "blocked"
            else "partial"
        )
        upystore = result.get("upystore")
        if not isinstance(upystore, dict):
            upystore = {}
        comparison_status = str(
            upystore.get("version_status", "unknown_unverified")
        )
        if comparison_status not in {
            "not_checked",
            "not_published",
            "current",
            "update_available",
            "conflict",
            "unknown_unverified",
        }:
            comparison_status = "unknown_unverified"
        bundle_artifact = next(
            (
                item
                for item in reversed(state.get("artifacts", []))
                if item.get("role") == "publish_materials_bundle"
            ),
            None,
        )

        result.setdefault("schema_version", "mpos-publish-app-web-v1")
        result.setdefault("phase", "mpos-publish-app-web")
        result.setdefault("result", "partial")
        result.setdefault("publish_ready", False)
        result["release_readiness"] = release_readiness
        result.setdefault("blockers", [])
        result["app"] = app
        result["mpk"] = mpk
        result["screenshot_readiness"] = {
            "ready": screenshot_ready,
            "artifact_ids": screenshot_ids,
            "missing": [] if screenshot_ready else ["publish_screenshot"],
        }
        result["upystore_comparison"] = {"status": comparison_status}
        result.setdefault(
            "manual_upload_guidance",
            {
                "developer_url": str(
                    upystore.get("developer_url", "https://upystore.io/developer")
                ),
                "steps": ["Upload the prepared MPK and publish screenshots."],
                "bundle_artifact_id": (
                    bundle_artifact.get("id") if bundle_artifact else None
                ),
            },
        )
        result.setdefault("warnings", [])
        result.setdefault("structured_errors", [])
        result.setdefault("handoff", {"next_phase": None})
        return result

    @staticmethod
    def _publish_bundle_value(value: Any) -> Any:
        if isinstance(value, dict):
            blocked_keys = {
                "authorization",
                "cookie",
                "credential",
                "credentials",
                "installed_path",
                "log_excerpt",
                "password",
                "secret",
                "serial_port",
                "token",
            }
            return {
                key: SessionService._publish_bundle_value(item)
                for key, item in value.items()
                if not any(
                    marker in key.lower()
                    for marker in blocked_keys | {"api_key"}
                )
            }
        if isinstance(value, list):
            return [SessionService._publish_bundle_value(item) for item in value]
        if isinstance(value, str):
            return _redact_text(value)
        return value

    @classmethod
    def _publish_deploy_result(cls, value: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "schema_version",
            "phase",
            "stage",
            "result",
            "mode",
            "hardware_available",
            "board",
            "micropythonos_installed",
            "app_installed",
            "app_launched",
            "client_attested",
            "server_verified",
            "permission_decisions",
            "warnings",
            "structured_errors",
            "handoff",
        }
        publish_value = {
            key: item for key, item in value.items() if key in allowed
        }
        publish_value.update(
            {"serial_port": None, "commands": [], "logs": []}
        )
        return cls._publish_bundle_value(publish_value)

    def _write_artifact_json(
        self, state: dict[str, Any], name: str, phase: str, value: dict[str, Any]
    ) -> None:
        if name == "deploy_result":
            value = self._normalize_deploy_result(state, value)
        elif name == "publish_result":
            value = self._normalize_publish_result(state, value)
        path = self._root(state["session_id"]) / "artifacts" / f"{name}.json"
        _json_dump(path, value)
        self._register_artifact(state, path, phase, "result", name)

    def _register_artifact(
        self,
        state: dict[str, Any],
        path: Path,
        phase: str,
        kind: str,
        role: str,
    ) -> None:
        root = self._root(state["session_id"])
        relative = path.relative_to(root).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        artifact_id = f"art_{role}_{digest[:12]}"
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        artifact = {
            "id": artifact_id,
            "phase": phase,
            "kind": kind,
            "role": role,
            "path": relative,
            "mime": mime,
            "size": path.stat().st_size,
            "sha256": digest,
            "display_name": path.name,
        }
        state["artifacts"] = [
            current
            for current in state["artifacts"]
            if current["path"] != relative
        ]
        state["artifacts"].append(artifact)
        self._write_state(state)

    def _write_manifest(self, state: dict[str, Any]) -> None:
        manifest = {
            "schema_version": "mpos-artifact-manifest-v1",
            "session_id": state["session_id"],
            "app_fullname": state["input"]["package_name"],
            "artifacts": [
                item
                for item in state["artifacts"]
                if item["role"] != "artifact_manifest"
            ],
        }
        path = self._root(state["session_id"]) / "artifact_manifest.json"
        _json_dump(path, manifest)
        self._register_artifact(
            state, path, "mpos-plan-app-web", "manifest", "artifact_manifest"
        )

    def _write_session_bundle(self, state: dict[str, Any]) -> None:
        root = self._root(state["session_id"])
        bundle = (
            root
            / "artifacts"
            / f"{state['input']['package_name']}_{state['revision_id']}_session.zip"
        )
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
            for relative in (
                "session_state.json",
                "activity_log.jsonl",
                "artifact_manifest.json",
            ):
                source = root / relative
                if source.is_file():
                    archive.write(source, relative)
            for artifact in state["artifacts"]:
                source = root / artifact["path"]
                if (
                    source.is_file()
                    and source != bundle
                    and artifact["role"] != "artifact_manifest"
                ):
                    archive.write(source, artifact["path"])
        self._register_artifact(
            state,
            bundle,
            "mpos-publish-app-web",
            "bundle",
            "session_bundle",
        )

    def _final_artifact_path(
        self, state: dict[str, Any], artifact: dict[str, Any] | None
    ) -> Path | None:
        if not artifact:
            return None
        root = self._root(state["session_id"])
        path = (root / str(artifact.get("path", ""))).resolve()
        if root not in path.parents or not path.is_file():
            return None
        return path

    @staticmethod
    def _valid_publish_screenshot(path: Path, mime: str) -> bool:
        suffix = path.suffix.lower()
        try:
            with path.open("rb") as handle:
                signature = handle.read(12)
        except OSError:
            return False
        if mime == "image/png":
            return suffix == ".png" and signature.startswith(b"\x89PNG\r\n\x1a\n")
        if mime == "image/jpeg":
            return suffix in {".jpg", ".jpeg"} and signature.startswith(b"\xff\xd8\xff")
        if mime == "image/webp":
            return (
                suffix == ".webp"
                and signature.startswith(b"RIFF")
                and len(signature) >= 12
                and signature[8:12] == b"WEBP"
            )
        return False

    @staticmethod
    def _is_final_artifact_error(error: dict[str, Any] | None) -> bool:
        return bool(
            error
            and isinstance(error.get("details"), dict)
            and error["details"].get("gate") == "final_artifacts_only"
        )

    def _apply_final_artifact_gate(
        self,
        state: dict[str, Any],
        *,
        completion_requested: bool,
    ) -> dict[str, Any]:
        """Enforce the final_artifacts_only evidence contract before completion."""
        artifacts = state.get("artifacts", [])
        package_name = state["input"]["package_name"]
        revision_id = state["revision_id"]
        expected_mpk = f"{package_name}_{revision_id}.mpk"

        mpk_artifact = next(
            (
                item
                for item in reversed(artifacts)
                if item.get("role") == "mpk"
                and Path(str(item.get("path", ""))).name == expected_mpk
            ),
            None,
        )
        mpk_path = self._final_artifact_path(state, mpk_artifact)
        source_paths = [
            path
            for item in artifacts
            if item.get("role") in {"app_manifest", "app_source"}
            for path in [self._final_artifact_path(state, item)]
            if path is not None
        ]
        mpk_fresh = bool(mpk_path)
        if mpk_path and source_paths:
            try:
                mpk_fresh = mpk_path.stat().st_mtime_ns >= max(
                    path.stat().st_mtime_ns for path in source_paths
                )
            except OSError:
                mpk_fresh = False

        screenshot_artifact = next(
            (
                item
                for item in reversed(artifacts)
                if item.get("role") in {"desktop_screenshot", "publish_screenshot"}
                and (
                    (path := self._final_artifact_path(state, item)) is not None
                )
                and self._valid_publish_screenshot(path, str(item.get("mime", "")))
            ),
            None,
        )
        screenshot_ready = screenshot_artifact is not None

        upload_manifest_artifact = next(
            (
                item
                for item in reversed(artifacts)
                if item.get("role") == "upystore_upload_manifest"
                and self._final_artifact_path(state, item) is not None
            ),
            None,
        )
        publish_artifact = next(
            (
                item
                for item in reversed(artifacts)
                if item.get("role") == "publish_result"
            ),
            None,
        )
        publish_path = self._final_artifact_path(state, publish_artifact)
        publish_result: dict[str, Any] = {}
        if publish_path:
            try:
                publish_result = _json_load(publish_path)
            except (OSError, json.JSONDecodeError):
                publish_result = {}
        publish_bundle_artifact = next(
            (
                item
                for item in reversed(artifacts)
                if item.get("role") == "publish_materials_bundle"
                and self._final_artifact_path(state, item) is not None
            ),
            None,
        )
        manual_guidance_ready = (
            publish_result.get("upystore", {}).get("mode") == "manual_guidance"
        )
        upload_metadata_ready = bool(upload_manifest_artifact) or bool(
            publish_result and manual_guidance_ready and publish_bundle_artifact
        )

        errors: list[dict[str, Any]] = []

        def missing_error(
            artifact_role: str,
            message: str,
            *,
            code: str = "FINAL_ARTIFACT_MISSING",
            owner: str = "backend",
            expected: str | None = None,
        ) -> None:
            errors.append(
                {
                    "code": code,
                    "message": message,
                    "stage": "publish",
                    "phase": "mpos-publish-app-web",
                    "owner": owner,
                    "retryable": True,
                    "details": {
                        "gate": "final_artifacts_only",
                        "artifact_role": artifact_role,
                        "expected": expected,
                    },
                    "logs": ["artifact_manifest.json"],
                }
            )

        if not mpk_path:
            missing_error(
                "mpk",
                f"缺少当前 revision 的 MPK：{expected_mpk}",
                expected=expected_mpk,
            )
        elif not mpk_fresh:
            missing_error(
                "mpk",
                f"MPK 早于当前 App 源码，必须重新打包：{expected_mpk}",
                code="FINAL_ARTIFACT_STALE",
                expected=expected_mpk,
            )
        if not screenshot_ready:
            missing_error(
                "publish_screenshot",
                "缺少有效的 PNG、JPEG 或 WebP 发布截图",
                owner="user",
                expected="PNG/JPEG/WebP",
            )
        if not upload_metadata_ready:
            missing_error(
                "upload_metadata",
                "缺少 upystore_upload_manifest，或 publish_result/manual guidance/publish bundle",
                expected=(
                    "upystore_upload_manifest or "
                    "publish_result+manual_guidance+publish_materials_bundle"
                ),
            )

        previous_errors = state.get("structured_errors", [])
        state["structured_errors"] = [
            error
            for error in previous_errors
            if not self._is_final_artifact_error(error)
        ] + errors
        ready = not errors
        if not ready:
            state["last_error"] = errors[0]
            if completion_requested:
                state["status"] = "blocked"
                state["checkpoint_id"] = "publish_check_done"
                state["current_phase"] = "mpos-publish-app-web"
                state["next_phase"] = (
                    "mpos-package-app-web"
                    if any(
                        error["details"]["artifact_role"] == "mpk"
                        for error in errors
                    )
                    else "mpos-test-app-web"
                    if any(
                        error["details"]["artifact_role"] == "publish_screenshot"
                        for error in errors
                    )
                    else "mpos-publish-app-web"
                )
                state["final_artifact_gate_blocked"] = True
        else:
            if self._is_final_artifact_error(state.get("last_error")):
                state["last_error"] = None
            if completion_requested or state.get("final_artifact_gate_blocked"):
                state["status"] = "completed"
                state["checkpoint_id"] = "completed"
                state["current_phase"] = "mpos-publish-app-web"
                state["next_phase"] = None
            state["final_artifact_gate_blocked"] = False

        if publish_result and publish_path:
            gate_blockers = {"mpk", "publish_screenshot", "upload_metadata"}
            blockers = [
                blocker
                for blocker in publish_result.get("blockers", [])
                if blocker not in gate_blockers
            ]
            blockers.extend(
                error["details"]["artifact_role"] for error in errors
            )
            publish_result["blockers"] = list(dict.fromkeys(blockers))
            publish_result["structured_errors"] = [
                error
                for error in publish_result.get("structured_errors", [])
                if not self._is_final_artifact_error(error)
            ] + errors
            publish_result["publish_ready"] = ready
            publish_result["result"] = "success" if ready else "partial"
            publish_result["status"] = (
                "ready_for_manual_upload" if ready else "partial"
            )
            gate_checks = {
                "final_mpk": "passed" if mpk_fresh else "failed",
                "final_screenshot": "passed" if screenshot_ready else "failed",
                "upload_metadata": (
                    "passed" if upload_metadata_ready else "failed"
                ),
            }
            publish_result["checks"] = [
                check
                for check in publish_result.get("checks", [])
                if check.get("name") not in gate_checks
            ] + [
                {"name": name, "status": status}
                for name, status in gate_checks.items()
            ]
            publish_result = self._normalize_publish_result(
                state, publish_result
            )
            _json_dump(publish_path, publish_result)
            self._register_artifact(
                state,
                publish_path,
                "mpos-publish-app-web",
                "result",
                "publish_result",
            )
            if publish_bundle_artifact:
                self._write_publish_bundle(state)

        return {"ready": ready, "errors": errors}

    def _write_publish_bundle(self, state: dict[str, Any]) -> None:
        root = self._root(state["session_id"])
        bundle = (
            root
            / "artifacts"
            / (
                f"{state['input']['package_name']}_"
                f"{state['revision_id']}_publish-materials.zip"
            )
        )
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
            for artifact in state["artifacts"]:
                source = root / artifact["path"]
                if (
                    source.is_file()
                    and source != bundle
                    and (
                        artifact["role"].endswith("_result")
                        or artifact["role"]
                        in {
                            "app_manifest",
                            "app_icon",
                            "mpk",
                            "app_index_entry",
                            "desktop_screenshot",
                            "publish_screenshot",
                        }
                    )
                ):
                    if artifact["role"].endswith("_result") and source.suffix == ".json":
                        try:
                            result_value = _json_load(source)
                        except (OSError, json.JSONDecodeError):
                            continue
                        if artifact["role"] == "deploy_result":
                            result_value = self._publish_deploy_result(result_value)
                        else:
                            result_value = self._publish_bundle_value(result_value)
                        archive.writestr(
                            source.name,
                            json.dumps(result_value, ensure_ascii=False, indent=2),
                        )
                    else:
                        archive.write(source, source.name)
        self._register_artifact(
            state,
            bundle,
            "mpos-publish-app-web",
            "bundle",
            "publish_materials_bundle",
        )

    def preview_result(
        self, session_id: str, request: PreviewResultRequest
    ) -> dict[str, Any]:
        state = self._read(session_id)
        if state.get("preview_idempotency_key") == request.idempotency_key:
            return state
        state["preview_idempotency_key"] = request.idempotency_key
        contracts = state.get("capability_contract", {}).get("contracts", {})
        web_unsupported = sorted(
            name for name, contract in contracts.items()
            if contract.get("web_preview") == "unsupported_without_emulation"
        )
        if request.result == "partial" or (request.result == "failed" and web_unsupported):
            error = {
                "code": "WEB_PREVIEW_UNSUPPORTED",
                "message": request.message or "Required hardware cannot be exercised in Web preview",
                "stage": "test",
                "phase": "mpos-test-app-web",
                "owner": "external",
                "retryable": False,
                "details": {"capabilities": web_unsupported},
                "logs": [],
            }
            state["status"] = "completed"
            state["current_phase"] = "mpos-publish-app-web"
            state["checkpoint_id"] = "web_preview_partial"
            state["next_phase"] = None
            state["last_error"] = error
            state["structured_errors"].append(error)
            payload = {"result": "partial", "checkpoint_id": "web_preview_partial", "structured_errors": [error]}
        elif request.result == "success":
            self._checkpoint(
                state,
                "mpos-test-app-web",
                "web_preview_done",
                "mpos-publish-app-web",
            )
            state["status"] = "completed"
            state["current_phase"] = "mpos-publish-app-web"
            state["checkpoint_id"] = "completed"
            state["next_phase"] = None
            payload = {
                "result": "success",
                "checkpoint_id": "completed",
                "message": request.message or "浏览器 WASM 验证通过",
            }
        else:
            code = (
                "WEB_PREVIEW_TIMEOUT"
                if request.result == "timeout"
                else "WEB_PREVIEW_BUILD_FAILED"
            )
            error = {
                "code": code,
                "message": request.message or "浏览器 WASM 预览失败",
                "stage": "test",
                "phase": "mpos-test-app-web",
                "owner": "app" if request.result == "failed" else "toolchain",
                "retryable": True,
                "details": {},
                "logs": ["activity_log.jsonl"],
            }
            state["status"] = "timeout" if request.result == "timeout" else "failed"
            state["checkpoint_id"] = "failed"
            state["next_phase"] = "mpos-gen-app-web"
            state["last_error"] = error
            state["structured_errors"].append(error)
            self._event(state, "structured_error", "mpos-test-app-web", error)
            payload = {
                "result": "failed",
                "checkpoint_id": "failed",
                "structured_errors": [error],
            }
        if request.result == "success":
            gate = self._apply_final_artifact_gate(
                state, completion_requested=True
            )
            if not gate["ready"]:
                payload = {
                    "result": "partial",
                    "checkpoint_id": state["checkpoint_id"],
                    "structured_errors": gate["errors"],
                }
        self._write_state(state)
        self._event(state, "phase_complete", "mpos-test-app-web", payload)
        return self.get(session_id)

    def record_device_result(
        self, session_id: str, request: DeviceResultRequest
    ) -> dict[str, Any]:
        state = self._read(session_id)
        if state.get("device_result_idempotency_key") == request.idempotency_key:
            return state
        state["device_result_idempotency_key"] = request.idempotency_key
        success = request.result != "failed"
        installed = request.result in {"install_success", "launch_success"}
        launched = request.result == "launch_success"
        allowed_error_codes = {
            "DEVICE_NOT_CONNECTED",
            "DEVICE_BOOTLOADER_NOT_FOUND",
            "MPOS_NOT_INSTALLED_ON_DEVICE",
            "DEVICE_PROBE_FAILED",
            "SCRIPT_TIMEOUT",
            "DEVICE_DEPLOY_FAILED",
            "HARDWARE_CAPABILITY_UNAVAILABLE",
            "MPOS_CAPABILITY_API_MISSING",
        }
        error_code = (
            request.error_code
            if request.error_code in allowed_error_codes
            else "DEVICE_DEPLOY_FAILED"
        )
        inferred_facts: dict[str, tuple[bool | None, bool | None]] = {
            "DEVICE_NOT_CONNECTED": (False, None),
            "DEVICE_BOOTLOADER_NOT_FOUND": (True, None),
            "MPOS_NOT_INSTALLED_ON_DEVICE": (True, False),
            "DEVICE_PROBE_FAILED": (True, None),
            "SCRIPT_TIMEOUT": (None, None),
            "DEVICE_DEPLOY_FAILED": (None, None),
            "HARDWARE_CAPABILITY_UNAVAILABLE": (True, True),
            "MPOS_CAPABILITY_API_MISSING": (True, True),
        }
        inferred_hardware, inferred_mpos = inferred_facts[error_code]
        hardware_available = (
            request.hardware_available
            if request.hardware_available is not None
            else True
            if success
            else inferred_hardware
        )
        micropythonos_installed = (
            request.micropythonos_installed
            if request.micropythonos_installed is not None
            else True
            if success
            else inferred_mpos
        )
        structured_errors = []
        if not success:
            structured_errors.append(
                {
                    "code": error_code,
                    "message": request.message or "浏览器设备操作失败",
                    "stage": "deploy",
                    "phase": "mpos-deploy-app-web",
                    "owner": "os_api" if error_code == "MPOS_CAPABILITY_API_MISSING" else "device",
                    "retryable": error_code not in {"HARDWARE_CAPABILITY_UNAVAILABLE", "MPOS_CAPABILITY_API_MISSING"},
                    "details": {
                        "transport": request.transport,
                        "board": request.board,
                        "usb_vendor_id": request.usb_vendor_id,
                        "usb_product_id": request.usb_product_id,
                        "hardware_available": hardware_available,
                        "micropythonos_installed": micropythonos_installed,
                        "detected_hardware_id": request.detected_hardware_id,
                        "runtime_capability_results": request.runtime_capability_results,
                    },
                    "logs": ["activity_log.jsonl"],
                }
            )
        deploy_result = {
            "schema_version": "mpos-deploy-app-web-v1",
            "phase": "mpos-deploy-app-web",
            "result": "success" if installed else "partial" if success else "failed",
            "mode": "mpk-install" if installed else request.transport,
            "hardware_available": hardware_available,
            "board": request.board,
            "detected_hardware_id": request.detected_hardware_id,
            "runtime_capability_results": request.runtime_capability_results,
            "required_capabilities": state.get("required_capabilities", []),
            "required_accessories": state.get("required_accessories", []),
            "usb_vendor_id": request.usb_vendor_id,
            "usb_product_id": request.usb_product_id,
            "serial_port": (
                "browser-selected"
                if request.transport == "webserial" and hardware_available is True
                else None
            ),
            "micropythonos_installed": micropythonos_installed,
            "app_installed": installed,
            "app_launched": launched,
            "client_attested": True,
            "server_verified": False,
            "installed_path": request.installed_path,
            "permissions": [
                {
                    "type": "device_write",
                    "decision": "allow_once",
                }
            ],
            "commands": [
                {
                    "transport": request.transport,
                    "summary": request.result,
                }
            ],
            "logs": [request.log_excerpt[-4000:]] if request.log_excerpt else [],
            "warnings": (
                []
                if launched
                else ["设备已连接，但尚未记录 App 在真机成功启动。"]
                if success
                else ["设备操作失败；硬件与 MicroPythonOS 状态按实际探测结果记录。"]
            ),
            "structured_errors": structured_errors,
            "handoff": {"next_phase": "mpos-publish-app-web"},
        }
        self._write_artifact_json(
            state, "deploy_result", "mpos-deploy-app-web", deploy_result
        )
        if success:
            state["hardware_verified"] = False
            state["hardware_client_attested"] = launched
            state["last_device_result"] = request.result
            if installed:
                self._checkpoint(
                    state,
                    "mpos-deploy-app-web",
                    "device_deploy_done",
                    "mpos-publish-app-web",
                )
            if launched:
                publish_path = self._root(session_id) / "artifacts" / "publish_result.json"
                if publish_path.is_file():
                    publish_result = _json_load(publish_path)
                    checks = [
                        item
                        for item in publish_result.get("checks", [])
                        if item.get("name") != "physical_device_launch"
                    ]
                    checks.append(
                        {
                            "name": "physical_device_launch",
                            "status": "warning",
                            "details": {
                                "client_attested": True,
                                "server_verified": False,
                            },
                        }
                    )
                    publish_result["checks"] = checks
                    publish_result["hardware_validation"] = {
                        "status": "client_attested",
                        "client_attested": True,
                        "server_verified": False,
                        "board": request.board,
                        "transport": request.transport,
                    }
                    publish_result["warnings"] = list(
                        dict.fromkeys(
                            publish_result.get("warnings", [])
                            + ["真机结果由浏览器客户端声明，尚未经过服务端独立验证。"]
                        )
                    )
                    self._write_artifact_json(
                        state,
                        "publish_result",
                        "mpos-publish-app-web",
                        publish_result,
                    )
                state["status"] = "completed"
                state["checkpoint_id"] = "completed"
                state["current_phase"] = "mpos-publish-app-web"
                state["next_phase"] = None
                self._apply_final_artifact_gate(
                    state, completion_requested=True
                )
        else:
            state["hardware_verified"] = False
            state["last_device_result"] = request.result
            state["structured_errors"].extend(structured_errors)
            state["last_error"] = structured_errors[0]
            self._event(
                state,
                "structured_error",
                "mpos-deploy-app-web",
                structured_errors[0],
            )
        self._write_state(state)
        self._event(
            state,
            "status_update",
            "mpos-deploy-app-web",
            {
                "status": request.result,
                "message": request.message,
                "board": request.board,
                "transport": request.transport,
                "client_attested": True,
                "server_verified": False,
            },
        )
        return self.get(session_id)

    def upload_screenshot(
        self, session_id: str, request: ScreenshotUploadRequest
    ) -> dict[str, Any]:
        state = self._read(session_id)
        upload_keys = state.setdefault("screenshot_upload_keys", {})
        if request.idempotency_key in upload_keys:
            return self.get(session_id)
        try:
            image = base64.b64decode(request.data_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("SCREENSHOT_INVALID_BASE64: 截图不是有效的 Base64") from exc
        if len(image) > 10 * 1024 * 1024:
            raise ValueError("SCREENSHOT_TOO_LARGE: 截图不能超过 10 MB")
        signatures = {
            "image/png": image.startswith(b"\x89PNG\r\n\x1a\n"),
            "image/jpeg": image.startswith(b"\xff\xd8\xff"),
            "image/webp": image.startswith(b"RIFF")
            and len(image) >= 12
            and image[8:12] == b"WEBP",
        }
        if not signatures[request.media_type]:
            raise ValueError(
                "SCREENSHOT_FORMAT_MISMATCH: 文件内容与声明的图片格式不一致"
            )
        expected_suffix = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
        }[request.media_type]
        safe_stem = re.sub(
            r"[^a-zA-Z0-9_.-]+", "-", Path(request.filename).stem
        ).strip(".-") or "screenshot"
        filename = f"{safe_stem}-{uuid.uuid4().hex[:8]}{expected_suffix}"
        path = self._root(session_id) / "artifacts" / "screenshots" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(image)
        role = (
            "desktop_screenshot"
            if request.source == "desktop"
            else "publish_screenshot"
        )
        self._register_artifact(
            state, path, "mpos-publish-app-web", "screenshot", role
        )
        upload_keys[request.idempotency_key] = filename
        state["screenshot_upload_keys"] = upload_keys
        publish_path = self._root(session_id) / "artifacts" / "publish_result.json"
        if publish_path.is_file():
            publish_result = _json_load(publish_path)
            screenshot_items = [
                {
                    "path": item["path"],
                    "mime": item["mime"],
                    "size": item["size"],
                    "publish_format_ok": True,
                }
                for item in state["artifacts"]
                if item["role"] in {"desktop_screenshot", "publish_screenshot"}
            ]
            publish_result["screenshots"] = screenshot_items
            publish_result["blockers"] = [
                blocker
                for blocker in publish_result.get("blockers", [])
                if blocker != "publish_screenshot"
            ]
            checks = [
                check
                for check in publish_result.get("checks", [])
                if check.get("name") != "publish_screenshot"
            ]
            checks.append({"name": "publish_screenshot", "status": "passed"})
            publish_result["checks"] = checks
            publish_result["status"] = (
                "ready_for_manual_upload"
                if not publish_result["blockers"]
                else publish_result.get("status", "partial")
            )
            self._write_artifact_json(
                state,
                "publish_result",
                "mpos-publish-app-web",
                publish_result,
            )
        self._write_publish_bundle(state)
        self._apply_final_artifact_gate(
            state,
            completion_requested=bool(state.get("final_artifact_gate_blocked")),
        )
        self._write_session_bundle(state)
        self._write_manifest(state)
        self._write_state(state)
        self._event(
            state,
            "status_update",
            "mpos-publish-app-web",
            {
                "status": "screenshot_uploaded",
                "filename": filename,
                "source": request.source,
                "media_type": request.media_type,
            },
        )
        return self.get(session_id)

    async def cancel(self, session_id: str, idempotency_key: str) -> dict[str, Any]:
        current = self._read(session_id)
        if current.get("cancel_idempotency_key") == idempotency_key:
            return current
        current["cancel_idempotency_key"] = idempotency_key
        self._write_state(current)
        task = self._tasks.get(session_id)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        else:
            state = self._read(session_id)
            state["status"] = "cancelled"
            state["checkpoint_id"] = "cancelled"
            state["next_phase"] = None
            self._write_state(state)
        return self.get(session_id)

    def artifact(self, artifact_id: str) -> tuple[Path, dict[str, Any]]:
        if not ARTIFACT_ID_RE.fullmatch(artifact_id):
            raise SessionNotFound(artifact_id)
        session_id = self._index.session_for_artifact(artifact_id)
        if session_id:
            state = _json_load(self._state_path(session_id))
            artifact = next(
                (
                    item
                    for item in state.get("artifacts", [])
                    if item["id"] == artifact_id
                ),
                None,
            )
            if artifact:
                root = self._root(session_id)
                path = (root / artifact["path"]).resolve()
                if root not in path.parents or not path.is_file():
                    raise SessionNotFound(artifact_id)
                return path, artifact
        raise SessionNotFound(artifact_id)


session_service = SessionService()
