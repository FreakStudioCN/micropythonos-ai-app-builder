import asyncio
import base64
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
from typing import Any

from fastapi.encoders import jsonable_encoder

from .generator import GenerationError, generate_app
from .models import (
    PROTOCOL_VERSION,
    GenerateRequest,
    PermissionDecisionRequest,
    PreviewResultRequest,
    RevisionRequest,
    SessionActionRequest,
    SessionCreateRequest,
)
from .runner_services import (
    STAGE_SKILLS,
    api_summary_version,
    device_service,
    mpos_skill_adapter,
    script_dispatcher,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SESSION_ROOT = Path(
    os.getenv("MPOS_SESSION_ROOT", str(PROJECT_ROOT / "backend" / "sessions"))
).resolve()
ARTIFACT_ID_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")


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


class SessionNotFound(KeyError):
    pass


class SessionService:
    def __init__(self) -> None:
        SESSION_ROOT.mkdir(parents=True, exist_ok=True)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._action_by_session: dict[str, str] = {}

    def capabilities(self) -> dict[str, Any]:
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
                "desktop_preview": False,
                "web_preview": True,
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
            "web_preview_notice": (
                "Web preview is a quick browser compatibility preview. It does not "
                "replace real hardware deployment."
            ),
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

    def list_sessions(self) -> list[dict[str, Any]]:
        sessions: list[dict[str, Any]] = []
        for path in sorted(
            SESSION_ROOT.glob("sess_*/session_state.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        ):
            try:
                item = _json_load(path)
                item.pop("generation", None)
                sessions.append(item)
            except (OSError, json.JSONDecodeError):
                continue
        return sessions[:50]

    def events(self, session_id: str) -> list[dict[str, Any]]:
        path = self._root(session_id) / "activity_log.jsonl"
        if not path.exists():
            return []
        result: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return result

    def _write_state(self, state: dict[str, Any]) -> None:
        state = dict(state)
        state.pop("events", None)
        state["updated_at"] = _now()
        _json_dump(self._state_path(state["session_id"]), state)

    def _event(
        self,
        state: dict[str, Any],
        event_type: str,
        phase: str,
        payload: dict[str, Any],
    ) -> None:
        root = self._root(state["session_id"])
        events = self.events(state["session_id"])
        event = {
            "protocol_version": PROTOCOL_VERSION,
            "seq": len(events) + 1,
            "ts": _now(),
            "type": event_type,
            "stage": phase.removeprefix("mpos-").removesuffix("-app-web"),
            "phase": phase,
            "session_id": state["session_id"],
            "checkpoint_id": state.get("checkpoint_id"),
            "payload": payload,
        }
        with (root / "activity_log.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def create(self, request: SessionCreateRequest) -> dict[str, Any]:
        for existing in self.list_sessions():
            if (
                existing.get("create_idempotency_key") == request.idempotency_key
                and existing.get("input", {}).get("prompt_original") == request.prompt
            ):
                return self.get(existing["session_id"])

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
            "revision_id": "r1",
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
                "publisher": request.publisher,
                "version": request.version,
                "targets": request.targets,
            },
            "capabilities": request.capabilities.model_dump(),
            "repo_commit": self.capabilities()["repo_commit"],
            "skills_commit": self.capabilities()["skills_commit"],
            "input_hash": input_hash,
            "api_summary_version": api_summary_version(),
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
                    "title": "允许调用 DeepSeek",
                    "description": (
                        "把当前 App 需求和运行错误发送到 DeepSeek API；"
                        "API Key 只保存在服务端。"
                    ),
                    "risk": "medium",
                    "required": True,
                    "command_preview": "POST DeepSeek /chat/completions",
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
        self._write_state(state)
        state["checkpoint_history"].append(self._checkpoint_record(state, "session_created", "mpos-analyze-app-web"))
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
        for session in self.list_sessions():
            for permission in session.get("permissions", []):
                if permission.get("permission_id") != permission_id:
                    continue
                state = self._read(session["session_id"])
                target = next(
                    item
                    for item in state["permissions"]
                    if item["permission_id"] == permission_id
                )
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

    def start_generation(
        self, session_id: str, request: SessionActionRequest
    ) -> dict[str, Any]:
        state = self._read(session_id)
        task = self._tasks.get(session_id)
        if task and not task.done():
            return state
        if state.get("last_action_idempotency_key") == request.idempotency_key and state[
            "status"
        ] in {"waiting_preview", "completed"}:
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

    def start_action(
        self, session_id: str, action: str, request: SessionActionRequest
    ) -> dict[str, Any]:
        if action not in STAGE_SKILLS:
            raise ValueError(f"Unsupported action: {action}")
        state = self._read(session_id)
        self._action_by_session[session_id] = action
        state["requested_action"] = action
        state["last_requested_skill"] = mpos_skill_adapter.describe(action)
        self._write_state(state)
        # The controlled runner remains a single in-flight pipeline. Action
        # endpoints are explicit protocol entry points and resume at checkpoints.
        return self.start_generation(session_id, request)

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
        snapshot_root = self._root(session_id) / "revisions" / f"r{current_revision}"
        snapshot_root.mkdir(parents=True, exist_ok=True)
        for name in ("project", "artifacts"):
            source = self._root(session_id) / name
            target = snapshot_root / name
            if source.exists() and not target.exists():
                shutil.copytree(source, target)

        state["revision_id"] = f"r{current_revision + 1}"
        state["revision_idempotency_key"] = request.idempotency_key
        state["input"]["prompt_original"] = request.prompt
        state["input"]["prompt_language"] = request.prompt_language
        state["input"]["prompt_normalized_zh"] = request.prompt
        state["input"]["prompt_normalized_en"] = request.prompt
        state["status"] = "created"
        state["current_phase"] = "mpos-analyze-app-web"
        state["checkpoint_id"] = "session_created"
        state["next_phase"] = "mpos-analyze-app-web"
        state["generation"] = None
        state["artifacts"] = []
        state["last_error"] = None
        state["structured_errors"] = []
        state["pending_repair"] = {"previous_code": None, "runtime_error": None}
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
                    "language": {
                        "ui_locale": user_input["ui_locale"],
                        "prompt_language": user_input["prompt_language"],
                        "prompt_original": user_input["prompt_original"],
                        "prompt_normalized_zh": user_input["prompt_normalized_zh"],
                        "prompt_normalized_en": user_input["prompt_normalized_en"],
                    },
                    "requirements": {"prompt": user_input["prompt_original"]},
                    "test_plan": {"targets": user_input["targets"]},
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
                    "mpos-gen-app-web",
                )

                state["current_phase"] = "mpos-gen-app-web"
                self._write_state(state)
                self._event(
                    state,
                    "start_phase",
                    "mpos-gen-app-web",
                    {
                        "message": "DeepSeek 正在生成并修复 MicroPythonOS App",
                        **mpos_skill_adapter.describe("generate"),
                    },
                )
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
                    )
                )
                generation_data = generated.model_dump()
                state["generation"] = generation_data
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
                self._checkpoint(
                    state,
                    "mpos-gen-app-web",
                    "code_generated",
                    "mpos-test-app-web",
                )

                state["current_phase"] = "mpos-test-app-web"
                web_requested = "web-preview" in user_input["targets"]
                syntax_result = script_dispatcher.run(
                    "python_syntax", app_root / "assets" / "main.py"
                )
                test_result = {
                    "schema_version": "mpos-test-app-web-v1",
                    "phase": "mpos-test-app-web",
                    "result": "partial",
                    "desktop": {
                        "status": "skipped",
                        "reason": "desktop_preview capability is unavailable on this host",
                    },
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
                    ],
                    "warnings": [],
                    "structured_errors": [],
                    "handoff": {"next_phase": "mpos-deploy-app-web"},
                }
                self._write_artifact_json(
                    state, "package_result", "mpos-package-app-web", package_result
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
                )
                publish_result = {
                    "schema_version": "mpos-publish-app-web-v1",
                    "phase": "mpos-publish-app-web",
                    "result": "partial",
                    "status": "needs_preview_and_screenshot",
                    "upystore": {
                        "home_url": "https://upystore.io/",
                        "developer_url": "https://upystore.io/developer",
                        "mode": "manual_guidance",
                    },
                    "checks": package_result["checks"]
                    + [{"name": "publish_screenshot", "status": "pending"}],
                    "warnings": deploy_result["warnings"] + [
                        "完成 Web/设备验证并准备 PNG、JPEG 或 WebP 截图后再手工上传。"
                    ],
                    "structured_errors": [],
                    "handoff": {"next_phase": None},
                }
                self._write_artifact_json(
                    state, "publish_result", "mpos-publish-app-web", publish_result
                )
                bundle_path = (
                    self._root(session_id)
                    / "artifacts"
                    / f"{generated.package_name}_{state['revision_id']}_publish-materials.zip"
                )
                with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as archive:
                    for artifact in state["artifacts"]:
                        source = self._root(session_id) / artifact["path"]
                        if source.is_file() and (
                            artifact["role"].endswith("_result")
                            or artifact["role"] in {"app_manifest", "mpk"}
                        ):
                            archive.write(source, source.name)
                self._register_artifact(
                    state,
                    bundle_path,
                    "mpos-publish-app-web",
                    "bundle",
                    "publish_materials_bundle",
                )
                self._checkpoint(
                    state,
                    "mpos-publish-app-web",
                    "publish_check_done",
                    "browser_web_preview" if web_requested else None,
                )
                state["status"] = (
                    "waiting_preview"
                    if web_requested
                    else "blocked"
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
            except (GenerationError, OSError, ValueError) as exc:
                state = self._read(session_id)
                message = str(exc)
                code = (
                    message.split(":", 1)[0]
                    if re.match(r"^[A-Z][A-Z0-9_]+:", message)
                    else "APP_GENERATION_FAILED"
                )
                error = {
                    "code": code,
                    "message": message.split(":", 1)[-1].strip(),
                    "stage": "generation",
                    "phase": "mpos-gen-app-web",
                    "owner": "app",
                    "retryable": True,
                    "details": {"attempt": state["attempts"].get("mpos-gen-app-web", 1)},
                    "logs": ["activity_log.jsonl"],
                }
                state["status"] = "failed"
                state["checkpoint_id"] = "failed"
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
            "api_summary_version": state.get("api_summary_version", {}),
            "output_files": [item["path"] for item in state.get("artifacts", [])],
            "warnings": state.get("warnings", []),
            "error": state.get("last_error"),
            "next_phase": next_phase,
        }

    def _checkpoint(
        self, state: dict[str, Any], phase: str, checkpoint: str, next_phase: str | None
    ) -> None:
        state["checkpoint_id"] = checkpoint
        state["next_phase"] = next_phase
        if phase not in state["completed_phases"]:
            state["completed_phases"].append(phase)
        state.setdefault("checkpoint_history", []).append(
            self._checkpoint_record(state, checkpoint, next_phase, phase)
        )
        self._write_state(state)
        self._event(
            state,
            "phase_complete",
            phase,
            {
                "result": "success",
                "checkpoint_id": checkpoint,
                "next_phase": next_phase,
                "artifacts": state["artifacts"],
                "warnings": [],
                "structured_errors": [],
            },
        )

    def _write_artifact_json(
        self, state: dict[str, Any], name: str, phase: str, value: dict[str, Any]
    ) -> None:
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
                if source.is_file() and source != bundle:
                    archive.write(source, artifact["path"])
        self._register_artifact(
            state,
            bundle,
            "mpos-publish-app-web",
            "bundle",
            "session_bundle",
        )

    def preview_result(
        self, session_id: str, request: PreviewResultRequest
    ) -> dict[str, Any]:
        state = self._read(session_id)
        if state.get("preview_idempotency_key") == request.idempotency_key:
            return state
        state["preview_idempotency_key"] = request.idempotency_key
        if request.result == "success":
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
        self._write_state(state)
        self._event(state, "phase_complete", "mpos-test-app-web", payload)
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
        for session in self.list_sessions():
            state = self._read(session["session_id"])
            for artifact in state.get("artifacts", []):
                if artifact["id"] != artifact_id:
                    continue
                root = self._root(state["session_id"])
                path = (root / artifact["path"]).resolve()
                if root not in path.parents or not path.is_file():
                    raise SessionNotFound(artifact_id)
                return path, artifact
        raise SessionNotFound(artifact_id)


session_service = SessionService()
