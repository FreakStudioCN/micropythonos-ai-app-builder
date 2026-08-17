"""Physical-device result recording.

Extracted from ``session_service`` as a mixin. Judging runtime capability
evidence from a connected device is self-contained work with one entry point;
the session state machine only needs the resulting state transition.
"""

from __future__ import annotations

from typing import Any

from .device_service import device_service
from .models import DeviceResultRequest
from .session_common import _json_load


class SessionDeviceMixin:
    """Device-result helpers mixed into :class:`SessionService`."""

    def record_device_result(
        self, session_id: str, request: DeviceResultRequest
    ) -> dict[str, Any]:
        state = self._read(session_id)
        if state.get("device_result_idempotency_key") == request.idempotency_key:
            return state
        state["device_result_idempotency_key"] = request.idempotency_key
        # Evidence accumulates per capability: a later install/launch report
        # carries whatever the browser happens to hold in memory, and after a
        # page reload that is nothing. Replacing outright would let an unrelated
        # install erase a probe the device really did answer.
        merged_results: dict[str, Any] = {
            str(item.get("capability")): item
            for item in state.get("runtime_capability_results", [])
            if isinstance(item, dict) and item.get("capability")
        }
        for probe in request.runtime_capability_results:
            merged_results[probe.capability] = probe.model_dump()
        # Runtime probes are authoritative. The static board table may only add
        # advisory notes, so an unlisted board that probes fine still passes.
        capability_verdict = device_service.evaluate_probe_results(
            required_capabilities=state["input"].get("required_capabilities", []),
            results=list(merged_results.values()),
            hardware_id=request.hardware_id,
        )
        # Device verdicts are per-probe. Leaving the previous run's blockers in
        # place means a later successful probe can never unblock the session.
        state["structured_errors"] = [
            error
            for error in state.get("structured_errors", [])
            if error.get("code") != "HARDWARE_CAPABILITY_UNAVAILABLE"
        ]
        state["detected_hardware_id"] = capability_verdict["detected_hardware_id"]
        state["runtime_capability_results"] = capability_verdict[
            "runtime_capability_results"
        ]
        state["warnings"] = list(
            dict.fromkeys(
                [*state.get("warnings", []), *capability_verdict["warnings"]]
            )
        )
        for error in capability_verdict["errors"]:
            error["phase"] = "mpos-deploy-app-web"
            error["logs"] = ["activity_log.jsonl"]
            state["structured_errors"].append(error)
            self._event(state, "structured_error", "mpos-deploy-app-web", error)
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
                    "owner": "device",
                    "retryable": True,
                    "details": {
                        "transport": request.transport,
                        "hardware_id": request.hardware_id,
                        "usb_vendor_id": request.usb_vendor_id,
                        "usb_product_id": request.usb_product_id,
                        "hardware_available": hardware_available,
                        "micropythonos_installed": micropythonos_installed,
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
            "hardware_id": request.hardware_id,
            "detected_hardware_id": capability_verdict["detected_hardware_id"],
            "runtime_capability_results": capability_verdict[
                "runtime_capability_results"
            ],
            "board_metadata_is_advisory": True,
            "capability_versions": capability_verdict["capability_versions"],
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
                        "hardware_id": request.hardware_id,
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
                "hardware_id": request.hardware_id,
                "transport": request.transport,
                "client_attested": True,
                "server_verified": False,
            },
        )
        return self.get(session_id)
