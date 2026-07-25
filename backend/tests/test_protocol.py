import base64
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.generator import _build_mpk
from app.models import (
    DemoErrorInjectionRequest,
    DemoSessionRequest,
    DeviceResultRequest,
    GeneratedFile,
    GenerateResponse,
    PermissionBatchDecisionRequest,
    PermissionDecisionRequest,
    RevisionRequest,
    ScreenshotUploadRequest,
    SessionCreateRequest,
    SessionActionRequest,
)
from app.runner_services import STAGE_SKILLS, mpos_skill_adapter
from app.session_index import EventLogCache
import app.session_service as session_module


class ProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        session_module.SESSION_ROOT = Path(self.temp.name).resolve()
        self.service = session_module.SessionService()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_event_log_cache_reads_only_complete_appended_lines(self) -> None:
        path = Path(self.temp.name) / "events.jsonl"
        cache = EventLogCache()
        first = {"seq": 1, "message": "开始"}
        second = {"seq": 2, "message": "完成"}
        cache.append("sess_cache", path, first)
        self.assertEqual(cache.read("sess_cache", path), [first])

        encoded = json.dumps(second, ensure_ascii=False).encode("utf-8")
        with path.open("ab") as handle:
            handle.write(encoded)
        self.assertEqual(cache.read("sess_cache", path), [first])

        with path.open("ab") as handle:
            handle.write(b"\n")
        self.assertEqual(cache.read("sess_cache", path), [first, second])
        self.assertEqual(cache.next_seq("sess_cache", path), 3)

    def test_all_web_skill_contracts_are_present(self) -> None:
        for stage, expected_name in STAGE_SKILLS.items():
            contract = mpos_skill_adapter.contract(stage)
            self.assertEqual(contract.name, expected_name)
            self.assertEqual(len(contract.sha256), 64)

    def test_permission_decisions_are_idempotent(self) -> None:
        state = self.service.create(
            SessionCreateRequest(
                idempotency_key="create-test-0001",
                prompt="做一个极简计算器",
                package_name="com.example.calculator",
                targets=["web-preview", "package-only"],
            )
        )
        permission = next(
            item for item in state["permissions"] if item["permission_type"] == "file_write"
        )
        request = PermissionDecisionRequest(
            idempotency_key="permission-test-0001",
            decision="allow_once",
        )
        first = self.service.decide_permission(permission["permission_id"], request)
        second = self.service.decide_permission(permission["permission_id"], request)
        self.assertEqual(first["updated_at"], second["updated_at"])
        decided = next(
            item
            for item in second["permissions"]
            if item["permission_id"] == permission["permission_id"]
        )
        self.assertEqual(decided["decision"], "allow_once")

    def test_all_permissions_can_be_allowed_atomically(self) -> None:
        state = self.service.create(
            SessionCreateRequest(
                idempotency_key="create-batch-permissions-0001",
                prompt="Build a touch calculator",
                package_name="com.example.batch_permissions",
                targets=["web-preview", "package-only"],
            )
        )
        required = [item for item in state["permissions"] if item["required"]]
        self.assertGreaterEqual(len(required), 4)

        request = PermissionBatchDecisionRequest(
            idempotency_key="allow-all-permissions-0001"
        )
        first = self.service.allow_all_permissions(state["session_id"], request)
        second = self.service.allow_all_permissions(state["session_id"], request)

        self.assertEqual(first["updated_at"], second["updated_at"])
        self.assertEqual(first["status"], "created")
        self.assertTrue(
            all(
                item["decision"] == "allow_once"
                for item in first["permissions"]
                if item["required"]
            )
        )

    def test_session_has_protocol_checkpoint_and_manifest_metadata(self) -> None:
        state = self.service.create(
            SessionCreateRequest(
                idempotency_key="create-test-0002",
                prompt="Build a calendar",
                package_name="com.example.calendar",
                targets=["package-only"],
            )
        )
        self.assertEqual(state["protocol_version"], "mpos-ai-app/v1")
        self.assertEqual(state["checkpoint_id"], "session_created")
        self.assertTrue(state["checkpoint_history"])
        self.assertEqual(len(state["input_hash"]), 64)
        self.assertIn("mpos_api_summary.json", state["api_summary_version"])
        self.assertNotIn(str(Path(self.temp.name)), str(state["permissions"]))
        roles = {item["role"] for item in state["artifacts"]}
        self.assertIn("plan_state", roles)
        self.assertIn("artifact_manifest", roles)

    def test_revision_keeps_previous_code_as_generation_input(self) -> None:
        state = self.service.create(
            SessionCreateRequest(
                idempotency_key="create-test-0003",
                prompt="做一个日历",
                package_name="com.example.calendar",
                targets=["package-only"],
            )
        )
        state["status"] = "completed"
        state["generation"] = {
            "files": [
                {
                    "path": "assets/main.py",
                    "content": "print('r1 calendar')\n",
                }
            ]
        }
        self.service._write_state(state)
        revised = self.service.create_revision(
            state["session_id"],
            RevisionRequest(
                idempotency_key="revision-test-0001",
                prompt="给日历增加返回今天按钮",
                prompt_language="zh-CN",
            ),
        )
        self.assertEqual(revised["revision_id"], "r2")
        self.assertEqual(
            revised["pending_repair"]["previous_code"],
            "print('r1 calendar')\n",
        )
        snapshot = (
            Path(self.temp.name)
            / state["session_id"]
            / "revisions"
            / "r1"
            / "session_state.json"
        )
        self.assertTrue(snapshot.is_file())

    def test_browser_device_result_is_persisted_as_deploy_artifact(self) -> None:
        state = self.service.create(
            SessionCreateRequest(
                idempotency_key="create-test-0004",
                prompt="做一个设备状态面板",
                package_name="com.example.device",
                targets=["physical-device", "package-only"],
            )
        )
        recorded = self.service.record_device_result(
            state["session_id"],
            DeviceResultRequest(
                idempotency_key="device-result-test-0001",
                result="launch_success",
                message="Started com.example.device",
                log_excerpt="Installed\nStarted: True",
            ),
        )
        self.assertTrue(recorded["hardware_verified"])
        self.assertEqual(recorded["checkpoint_id"], "completed")
        deploy = next(
            item for item in recorded["artifacts"] if item["role"] == "deploy_result"
        )
        payload = Path(self.temp.name, state["session_id"], deploy["path"])
        self.assertTrue(
            json.loads(payload.read_text(encoding="utf-8"))["app_launched"]
        )

    def test_publish_screenshot_upload_validates_and_registers_png(self) -> None:
        state = self.service.create(
            SessionCreateRequest(
                idempotency_key="create-test-0005",
                prompt="做一个日历",
                package_name="com.example.calendar",
                targets=["package-only"],
            )
        )
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
            "YAAAAAYAAjCB0C8AAAAASUVORK5CYII="
        )
        updated = self.service.upload_screenshot(
            state["session_id"],
            ScreenshotUploadRequest(
                idempotency_key="screenshot-test-0001",
                filename="../../calendar.png",
                media_type="image/png",
                data_base64=base64.b64encode(png).decode(),
                source="manual",
            ),
        )
        screenshot = next(
            item
            for item in updated["artifacts"]
            if item["role"] == "publish_screenshot"
        )
        self.assertTrue(screenshot["path"].startswith("artifacts/screenshots/"))
        self.assertNotIn("..", screenshot["path"])

    def test_demo_seed_is_deterministic_and_exports_redacted_bundle(self) -> None:
        request = DemoSessionRequest(
            idempotency_key="demo-seed-test-0001",
            seed="countdown",
        )
        first = self.service.create_demo(request)
        second = self.service.create_demo(request)
        self.assertEqual(first["session_id"], second["session_id"])
        self.assertEqual(first["status"], "completed")
        self.assertEqual(first["generation"]["model"], "deterministic-demo-seed")
        roles = {item["role"] for item in first["artifacts"]}
        self.assertIn("mpk", roles)
        self.assertIn("publish_result", roles)

        bundle, artifact = self.service.export_bundle(
            first["session_id"], kind="demo-artifacts"
        )
        self.assertEqual(artifact["role"], "demo_artifact_bundle")
        with zipfile.ZipFile(bundle) as archive:
            names = set(archive.namelist())
            self.assertIn("session_summary.json", names)
            self.assertIn("activity_log.redacted.jsonl", names)
            self.assertTrue(any(name.endswith("_r1.mpk") for name in names))

    def test_activity_log_export_redacts_secrets_paths_and_serial_ports(self) -> None:
        state = self.service.create(
            SessionCreateRequest(
                idempotency_key="redaction-test-0001",
                prompt="Build a safe log viewer",
                package_name="com.example.logviewer",
                targets=["package-only"],
            )
        )
        self.service._event(
            state,
            "status_update",
            "mpos-test-app-web",
            {
                "message": (
                    "Authorization: Bearer sk-super-secret-token-123456 "
                    "C:\\Users\\demo\\private\\file.py COM5"
                )
            },
        )
        exported = self.service.activity_log(
            state["session_id"], view="engineer", redacted=True
        )
        serialized = json.dumps(exported, ensure_ascii=False)
        self.assertNotIn("sk-super-secret", serialized)
        self.assertNotIn("C:\\Users\\demo", serialized)
        self.assertNotIn("COM5", serialized)
        self.assertIn("[REDACTED_TOKEN]", serialized)

    def test_demo_error_injection_is_disabled_by_default_and_audited(self) -> None:
        state = self.service.create_demo(
            DemoSessionRequest(
                idempotency_key="demo-error-seed-0001",
                seed="calendar",
            )
        )
        request = DemoErrorInjectionRequest(
            idempotency_key="demo-error-test-0001",
            code="LVGL_API_MISSING",
        )
        with patch.dict(os.environ, {"MPOS_DEMO_ERROR_INJECTION": "false"}):
            with self.assertRaises(PermissionError):
                self.service.inject_demo_error(state["session_id"], request)
        with patch.dict(os.environ, {"MPOS_DEMO_ERROR_INJECTION": "true"}):
            failed = self.service.inject_demo_error(state["session_id"], request)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["last_error"]["code"], "LVGL_API_MISSING")
        self.assertTrue(failed["last_error"]["details"]["injected"])

    def test_retry_archives_failed_state_and_result_files(self) -> None:
        state = self.service.create(
            SessionCreateRequest(
                idempotency_key="retry-archive-create-0001",
                prompt="Build a calendar",
                package_name="com.example.retrycalendar",
                targets=["package-only"],
            )
        )
        state["status"] = "failed"
        state["checkpoint_id"] = "failed"
        state["last_error"] = {"code": "APP_GENERATION_FAILED"}
        self.service._write_artifact_json(
            state,
            "generation_result",
            "mpos-gen-app-web",
            {"result": "failed"},
        )
        generation_run = "generation-attempts/run-001"
        self.service._write_generation_attempt(
            state,
            generation_run,
            {
                "attempt": 1,
                "status": "validation_failed",
                "candidate": "print('candidate')\n",
                "validation": {
                    "status": "failed",
                    "code": "GENERATION_VALIDATION_FAILED",
                    "message": "第 8 行阻塞式 while",
                    "line": 8,
                },
                "model_meta": {
                    "model": "test-model",
                    "request_id": "req-private",
                    "usage": {"total_tokens": 42},
                    "api_key": "sk-model-secret",
                },
                "prompt": "sk-prompt-secret",
                "headers": {"Authorization": "Bearer header-secret"},
                "cookie": "session=private",
            },
        )
        self.service._write_state(state)
        self.service._archive_failed_attempt(state, "retry-archive-run-0001")
        updated = self.service.get(state["session_id"])
        self.assertEqual(len(updated["retry_history"]), 1)
        archive = (
            Path(self.temp.name)
            / state["session_id"]
            / updated["retry_history"][0]["activity_log"]
        )
        self.assertTrue(archive.is_file())
        self.assertTrue(updated["retry_history"][0]["result_files"])
        run_root = (
            Path(self.temp.name)
            / state["session_id"]
            / generation_run
            / "attempt-001"
        )
        self.assertTrue((run_root / "candidate.py").is_file())
        diagnostic_text = (
            (run_root / "validation.json").read_text(encoding="utf-8")
            + (run_root / "model_meta.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("sk-model-secret", diagnostic_text)
        self.assertNotIn("sk-prompt-secret", diagnostic_text)
        self.assertNotIn("Authorization", diagnostic_text)
        archived_run = updated["retry_history"][0]["generation_attempt_run"]
        self.assertTrue(
            (
                Path(self.temp.name)
                / state["session_id"]
                / archived_run
                / "attempt-001"
                / "candidate.py"
            ).is_file()
        )


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        session_module.SESSION_ROOT = Path(self.temp.name).resolve()
        self.service = session_module.SessionService()

    def tearDown(self) -> None:
        self.temp.cleanup()

    async def test_pipeline_writes_required_protocol_artifacts(self) -> None:
        state = self.service.create(
            SessionCreateRequest(
                idempotency_key="pipeline-create-0001",
                prompt="做一个设备状态面板",
                package_name="com.example.dashboard",
                targets=["package-only"],
            )
        )
        for permission in state["permissions"]:
            if not permission["required"]:
                continue
            state = self.service.decide_permission(
                permission["permission_id"],
                PermissionDecisionRequest(
                    idempotency_key=f"allow-{permission['permission_id']}",
                    decision="allow_once",
                ),
            )
        manifest = {
            "fullname": "com.example.dashboard",
            "name": "Dashboard",
            "publisher": "erkou111",
            "version": "0.1.0",
            "activities": [
                {
                    "entrypoint": "assets/main.py",
                    "classname": "GeneratedApp",
                }
            ],
        }
        app_code = "print('dashboard')\n"
        generated = GenerateResponse(
            package_name="com.example.dashboard",
            summary="设备状态面板",
            manifest=manifest,
            files=[
                GeneratedFile(
                    path="MANIFEST.JSON",
                    content=json.dumps(manifest),
                ),
                GeneratedFile(path="assets/main.py", content=app_code),
                GeneratedFile(
                    path="generation_result.json",
                    content=json.dumps({"result": "success"}),
                ),
            ],
            mpk_base64=_build_mpk(
                "com.example.dashboard", manifest, app_code
            ),
            model="test-model",
            acceptance_tests=["state updates", "controls work"],
            mpk_filename="com.example.dashboard_r1.mpk",
            prompt_normalized_zh="创建一个设备状态面板。",
            prompt_normalized_en="Create a device status dashboard.",
            store_metadata={
                "display_name_zh": "设备面板",
                "display_name_en": "Device Dashboard",
                "short_description_zh": "显示设备状态",
                "short_description_en": "Shows device status",
                "long_description_zh": "显示设备的主要状态。",
                "long_description_en": "Shows primary device status.",
                "release_notes_zh": "首次发布",
                "release_notes_en": "Initial release",
                "category": "tools",
            },
        )
        with patch.object(
            session_module, "generate_app", new=AsyncMock(return_value=generated)
        ):
            self.service.start_generation(
                state["session_id"],
                SessionActionRequest(idempotency_key="pipeline-run-0001"),
            )
            await self.service._tasks[state["session_id"]]
        completed = self.service.get(state["session_id"])
        self.assertEqual(completed["status"], "completed")
        roles = {item["role"] for item in completed["artifacts"]}
        self.assertTrue(
            {
                "analysis_result",
                "dependency_handoff",
                "generation_result",
                "app_test_result",
                "package_result",
                "app_index_entry",
                "deploy_result",
                "publish_result",
                "artifact_manifest",
                "session_bundle",
            }.issubset(roles)
        )
        self.assertTrue(
            any(role.startswith("phase_complete.") for role in roles)
        )

    async def test_analyze_endpoint_runs_only_analyze_stage(self) -> None:
        state = self.service.create(
            SessionCreateRequest(
                idempotency_key="stage-create-0001",
                prompt="做一个日历",
                package_name="com.example.calendar",
                targets=["package-only"],
            )
        )
        for permission in state["permissions"]:
            if permission["required"]:
                state = self.service.decide_permission(
                    permission["permission_id"],
                    PermissionDecisionRequest(
                        idempotency_key=f"allow-{permission['permission_id']}",
                        decision="allow_once",
                    ),
                )
        started = self.service.start_action(
            state["session_id"],
            "analyze",
            SessionActionRequest(idempotency_key="stage-analyze-0001"),
        )
        self.assertEqual(started["current_phase"], "mpos-analyze-app-web")
        await self.service._tasks[state["session_id"]]
        completed = self.service.get(state["session_id"])
        roles = {item["role"] for item in completed["artifacts"]}
        self.assertIn("analysis_result", roles)
        self.assertNotIn("generation_result", roles)
        self.assertIsNone(completed["generation"])


if __name__ == "__main__":
    unittest.main()
