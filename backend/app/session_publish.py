"""Publish-result normalisation and redaction.

Extracted from ``session_service`` as a mixin: turning a raw publish result
into the redacted, schema-stable artifact the store expects is self-contained
work that does not belong in the session state machine.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .session_redaction import _redact_text


class SessionPublishMixin:
    """Publish-result helpers mixed into :class:`SessionService`."""

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
                key: SessionPublishMixin._publish_bundle_value(item)
                for key, item in value.items()
                if not any(
                    marker in key.lower()
                    for marker in blocked_keys | {"api_key"}
                )
            }
        if isinstance(value, list):
            return [SessionPublishMixin._publish_bundle_value(item) for item in value]
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
            "hardware_id",
            "detected_hardware_id",
            "runtime_capability_results",
            "board_metadata_is_advisory",
            "capability_versions",
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
