import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource


SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models import DemoSessionRequest, DeviceResultRequest, SessionCreateRequest
import app.session_service as session_module


EXPECTED_SCHEMAS = {
    "analysis-result.schema.json",
    "app-test-result.schema.json",
    "artifact-manifest.schema.json",
    "common-defs.schema.json",
    "dependency-handoff.schema.json",
    "deploy-result.schema.json",
    "generation-result.schema.json",
    "package-result.schema.json",
    "permission-request.schema.json",
    "phase-complete.schema.json",
    "protocol-envelope.schema.json",
    "publish-result.schema.json",
    "structured-error.schema.json",
}


class ProtocolSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schemas = {}
        resources = []
        for path in sorted(SCHEMA_DIR.glob("*.schema.json")):
            with path.open(encoding="utf-8") as handle:
                schema = json.load(handle)
            cls.schemas[path.name] = schema
            resources.append((schema["$id"], Resource.from_contents(schema)))
        cls.registry = Registry().with_resources(resources)

    def validator(self, name):
        return Draft202012Validator(
            self.schemas[name],
            registry=self.registry,
            format_checker=FormatChecker(),
        )

    def assert_valid(self, name, instance):
        self.validator(name).validate(instance)

    def test_schema_set_and_metaschemas(self):
        self.assertEqual(EXPECTED_SCHEMAS, set(self.schemas))
        for schema in self.schemas.values():
            Draft202012Validator.check_schema(schema)

    def test_protocol_envelope_supports_current_statuses(self):
        base = {
            "protocol_version": "mpos-ai-app/v1",
            "session_id": "sess_20260723_001",
            "checkpoint_id": "package_done",
            "idempotency_key": "user-click-uuid",
            "operation": "deploy_app",
            "status": "waiting_device",
            "capabilities": {
                "desktop_preview": True,
                "web_preview": True,
                "physical_device": True,
            },
            "input": {"fullname": "com.example.calculator"},
        }
        for status in ("waiting_device", "waiting_preview", "partial", "completed"):
            instance = copy.deepcopy(base)
            instance["status"] = status
            self.assert_valid("protocol-envelope.schema.json", instance)
        invalid = copy.deepcopy(base)
        invalid["status"] = "waiting"
        with self.assertRaises(ValidationError):
            self.assert_valid("protocol-envelope.schema.json", invalid)

    def test_structured_error_permission_artifact_and_phase_complete(self):
        error = {
            "error": {
                "code": "LVGL_API_MISSING",
                "message": "The requested LVGL API is unavailable",
                "stage": "generation",
                "phase": "mpos-publish-app-web",
                "retryable": True,
                "owner": "app",
                "details": {"symbol": "lv.obj.missing"},
                "logs": ["generation_result.json: API check failed"],
            }
        }
        self.assert_valid("structured-error.schema.json", error)
        invalid_phase = copy.deepcopy(error)
        invalid_phase["error"]["phase"] = "publish"
        with self.assertRaises(ValidationError):
            self.assert_valid("structured-error.schema.json", invalid_phase)

        permission = {
            "protocol_version": "mpos-ai-app/v1",
            "permission_id": "perm_001",
            "session_id": "sess_20260723_001",
            "stage": "deploy",
            "type": "device_write",
            "title": "Allow deployment",
            "description": "Copy the App to the selected serial device",
            "risk": "medium",
            "command_preview": "mpremote fs cp -r app :/apps/",
            "expires_at": "2026-07-23T12:00:00Z",
        }
        self.assert_valid("permission-request.schema.json", permission)

        artifact = {
            "protocol_version": "mpos-ai-app/v1",
            "session_id": "sess_20260723_001",
            "artifacts": [
                {
                    "id": "art_mpk",
                    "kind": "package",
                    "path": "artifacts/com.example.calculator_r1.mpk",
                    "mime": "application/octet-stream",
                    "role": "mpk",
                    "sha256": "a" * 64,
                    "size_bytes": 1024,
                    "stage": "package",
                }
            ],
        }
        self.assert_valid("artifact-manifest.schema.json", artifact)

        phase_complete = {
            "protocol_version": "mpos-ai-app/v1",
            "event": "phase_complete",
            "session_id": "sess_20260723_001",
            "stage": "package",
            "status": "success",
            "checkpoint_id": "package_done",
            "result_path": "artifacts/package_result.json",
            "artifact_manifest_path": "artifacts/artifact_manifest.json",
            "next_stage": "deploy",
        }
        self.assert_valid("phase-complete.schema.json", phase_complete)

        for name, instance in (
            ("structured-error.schema.json", error),
            ("permission-request.schema.json", permission),
            ("artifact-manifest.schema.json", artifact),
            ("phase-complete.schema.json", phase_complete),
        ):
            invalid = copy.deepcopy(instance)
            invalid["unexpected"] = True
            with self.assertRaises(ValidationError, msg=name):
                self.assert_valid(name, invalid)

    def test_session_service_writers_match_result_schemas(self):
        with tempfile.TemporaryDirectory() as temp_root, patch.object(
            session_module, "SESSION_ROOT", Path(temp_root).resolve()
        ):
            service = session_module.SessionService()
            state = service.create(
                SessionCreateRequest(
                    idempotency_key="runner-writer-deploy-0001",
                    prompt="Build a device status panel",
                    package_name="com.example.runnerwriter",
                    targets=["physical-device", "package-only"],
                )
            )
            recorded = service.record_device_result(
                state["session_id"],
                DeviceResultRequest(
                    idempotency_key="runner-writer-result-0001",
                    result="launch_success",
                    message="Browser client reports launch success",
                ),
            )
            deploy_artifact = next(
                item
                for item in recorded["artifacts"]
                if item["role"] == "deploy_result"
            )
            deploy_result = json.loads(
                Path(
                    temp_root,
                    state["session_id"],
                    deploy_artifact["path"],
                ).read_text(encoding="utf-8")
            )
            self.assert_valid("deploy-result.schema.json", deploy_result)
            self.assertTrue(deploy_result["client_attested"])
            self.assertFalse(deploy_result["server_verified"])

            demo = service.create_demo(
                DemoSessionRequest(
                    idempotency_key="runner-writer-publish-0001",
                    seed="countdown",
                )
            )
            publish_artifact = next(
                item
                for item in demo["artifacts"]
                if item["role"] == "publish_result"
            )
            publish_result = json.loads(
                Path(
                    temp_root,
                    demo["session_id"],
                    publish_artifact["path"],
                ).read_text(encoding="utf-8")
            )
            self.assert_valid("publish-result.schema.json", publish_result)
            self.assertIn("release_readiness", publish_result)
            self.assertIn("app", publish_result)
            self.assertIn("screenshot_readiness", publish_result)
            self.assertIn("upystore_comparison", publish_result)

    def test_all_seven_stage_results(self):
        base = {
            "result": "success",
            "warnings": [],
            "structured_errors": [],
        }
        samples = {
            "analysis-result.schema.json": {
                **base,
                "schema_version": "mpos-analyze-app-web-v1",
                "phase": "mpos-analyze-app-web",
                "app": {},
                "manifest_draft": {},
                "requirements": {},
                "api_plan": {},
                "dependency_plan": {},
                "test_plan": {},
                "deploy_plan": {},
                "handoff": {"next_phase": "mpos-gen-app-web"},
            },
            "dependency-handoff.schema.json": {
                **base,
                "schema_version": "mpos-prepare-deps-web-v1",
                "phase": "mpos-prepare-deps-web",
                "imports": [],
                "runtime_files": [],
                "adapter_requirements": [],
                "sync_needs_adapter": False,
                "async_compatible": True,
                "handoff": {"next_phase": "mpos-gen-app-web"},
            },
            "generation-result.schema.json": {
                **base,
                "schema_version": "mpos-gen-app-web-v1",
                "phase": "mpos-gen-app-web",
                "mode": "create",
                "app": {},
                "files_written": [],
                "api_usage": {"checked": True, "missing": []},
                "validation": {"gates": []},
                "handoff": {"next_phase": "mpos-test-app-web"},
            },
            "app-test-result.schema.json": {
                **base,
                "schema_version": "mpos-test-app-web-v1",
                "phase": "mpos-test-app-web",
                "desktop_launch": {"status": "passed"},
                "controller_smoke": {"status": "passed"},
                "web_preview": {"status": "skipped"},
                "screenshots": ["art_store_screenshot"],
                "manual_commands": ["run desktop smoke"],
                "handoff": {"next_phase": "mpos-package-app-web"},
            },
            "package-result.schema.json": {
                **base,
                "schema_version": "mpos-package-app-web-v1",
                "phase": "mpos-package-app-web",
                "app": {},
                "package": {
                    "revision": 1,
                    "mpk_path": "artifacts/com.example.app_r1.mpk",
                    "filename_policy": "<fullname>_rN.mpk",
                },
                "checks": [],
                "handoff": {"next_phase": "mpos-deploy-app-web"},
            },
            "deploy-result.schema.json": {
                **base,
                "schema_version": "mpos-deploy-app-web-v1",
                "phase": "mpos-deploy-app-web",
                "mode": "web-preview",
                "hardware_available": False,
                "board": None,
                "serial_port": None,
                "micropythonos_installed": "unknown",
                "permission_decisions": [],
                "commands": [],
                "logs": [],
                "client_attested": False,
                "server_verified": False,
                "handoff": {"next_phase": "mpos-publish-app-web"},
            },
            "publish-result.schema.json": {
                **base,
                "schema_version": "mpos-publish-app-web-v1",
                "phase": "mpos-publish-app-web",
                "publish_ready": True,
                "release_readiness": "ready_for_manual_upload",
                "blockers": [],
                "app": {},
                "mpk": {
                    "path": "artifacts/com.example.app_r1.mpk",
                    "filename": "com.example.app_r1.mpk",
                },
                "screenshot_readiness": {
                    "ready": True,
                    "artifact_ids": ["art_store_screenshot"],
                    "missing": [],
                },
                "upystore_comparison": {"status": "not_checked"},
                "manual_upload_guidance": {
                    "developer_url": "https://upystore.io/developer",
                    "steps": ["Upload the prepared MPK and screenshots"],
                },
                "handoff": {"next_phase": None},
            },
        }
        self.assertEqual(7, len(samples))
        for name, instance in samples.items():
            self.assert_valid(name, instance)
            invalid = copy.deepcopy(instance)
            invalid["unexpected"] = True
            with self.assertRaises(ValidationError, msg=name):
                self.assert_valid(name, invalid)


if __name__ == "__main__":
    unittest.main()
