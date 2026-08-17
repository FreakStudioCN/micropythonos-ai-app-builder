from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .capabilities import HARDWARE_DOC_PATH, capability_index, capability_versions
from .device_service import DeviceService, device_service

__all__ = [
    "DeviceService",
    "MposSkillAdapter",
    "ScriptDispatcher",
    "SkillContract",
    "SkillContractError",
    "api_summary_version",
    "capability_reference",
    "device_service",
    "mpos_skill_adapter",
    "script_dispatcher",
]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = PROJECT_ROOT / "vendor" / "MicroPython_Skills"

STAGE_SKILLS = {
    "analyze": "mpos-analyze-app-web",
    "prepare-deps": "mpos-prepare-deps-web",
    "generate": "mpos-gen-app-web",
    "test": "mpos-test-app-web",
    "package": "mpos-package-app-web",
    "deploy": "mpos-deploy-app-web",
    "publish-check": "mpos-publish-app-web",
}

STAGE_CHECKPOINTS = {
    "analyze": "requirements_analyzed",
    "prepare-deps": "dependencies_prepared",
    "generate": "code_generated",
    "test": "desktop_test_done",
    "package": "package_done",
    "deploy": "device_deploy_done",
    "publish-check": "publish_check_done",
}


class SkillContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class SkillContract:
    stage: str
    name: str
    version: str
    sha256: str
    path: str


class MposSkillAdapter:
    """Loads the checked-in web Skill contracts used by the controlled runner.

    Skills in MicroPython_Skills are protocol documents rather than executable
    programs. The backend owns all effects and uses these immutable contracts to
    name phases, results, checkpoints, and handoffs.
    """

    def __init__(self, skills_root: Path = SKILLS_ROOT) -> None:
        self.skills_root = skills_root

    @staticmethod
    def _frontmatter(text: str) -> dict[str, str]:
        match = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
        if not match:
            raise SkillContractError("SKILL.md is missing YAML frontmatter")
        fields: dict[str, str] = {}
        for line in match.group(1).splitlines():
            if ":" not in line or line.lstrip().startswith("#"):
                continue
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip().strip("\"'")
        for required in ("name", "description"):
            if not fields.get(required):
                raise SkillContractError(
                    f"SKILL.md frontmatter is missing {required}"
                )
        return fields

    def contract(self, stage: str) -> SkillContract:
        name = STAGE_SKILLS.get(stage)
        if not name:
            raise SkillContractError(f"Unknown runner stage: {stage}")
        path = (self.skills_root / name / "SKILL.md").resolve()
        if self.skills_root.resolve() not in path.parents or not path.is_file():
            raise SkillContractError(f"MPOS_NOT_FOUND: {name}/SKILL.md is missing")
        data = path.read_bytes()
        text = data.decode("utf-8")
        fields = self._frontmatter(text)
        if fields["name"] != name:
            raise SkillContractError(f"Invalid Skill contract: {name}")
        version_path = self.skills_root / "VERSION"
        version = (
            version_path.read_text(encoding="utf-8").strip()
            if version_path.is_file()
            else "unknown"
        )
        return SkillContract(
            stage=stage,
            name=name,
            version=version,
            sha256=hashlib.sha256(data).hexdigest(),
            path=f"vendor/MicroPython_Skills/{name}/SKILL.md",
        )

    def describe(self, stage: str) -> dict[str, str]:
        contract = self.contract(stage)
        return {
            "stage": contract.stage,
            "skill": contract.name,
            "skill_version": contract.version,
            "skill_sha256": contract.sha256,
            "skill_path": contract.path,
        }


class ScriptDispatcher:
    """Executes only server-owned commands; requests can never supply a shell."""

    ALLOWED = {
        "python_syntax": ("python", "-m", "py_compile"),
    }

    @staticmethod
    def _resolve_interpreter(name: str) -> str | None:
        if name != "python":
            return shutil.which(name)
        if sys.executable:
            return sys.executable
        for candidate in ("python3", "python"):
            found = shutil.which(candidate)
            if found:
                return found
        return None

    def desktop_smoke_capability(self) -> dict[str, Any]:
        script = (
            SKILLS_ROOT
            / "mpos-test-app"
            / "scripts"
            / "run_app_smoke.py"
        )
        controller = PROJECT_ROOT / "vendor" / "MicroPythonOS" / "scripts" / "mpos_controller.py"
        build_dir = (
            PROJECT_ROOT
            / "vendor"
            / "MicroPythonOS"
            / "lvgl_micropython"
            / "build"
        )
        binary = next(
            (
                candidate
                for candidate in (
                    build_dir / "lvgl_micropy_unix",
                    build_dir / "lvgl_micropy_macOS",
                )
                if candidate.is_file()
            ),
            None,
        )
        available = (
            script.is_file()
            and controller.is_file()
            and binary is not None
            and self._resolve_interpreter("python")
        )
        return {
            "available": bool(available),
            "script": str(script) if script.is_file() else None,
            "controller": str(controller) if controller.is_file() else None,
            "binary": str(binary) if binary else None,
        }

    def run_desktop_smoke(
        self,
        repo: Path,
        app_fullname: str,
        app_source: Path,
        generation_result: Path | None,
        artifact_dir: Path,
        timeout: int = 90,
    ) -> dict[str, Any]:
        capability = self.desktop_smoke_capability()
        if not capability["available"]:
            return {
                "ok": False,
                "skipped": True,
                "error": {
                    "code": "TOOLCHAIN_MISSING",
                    "message": "Linux SDL desktop simulator/controller is unavailable",
                    "stage": "test",
                    "owner": "toolchain",
                    "retryable": True,
                    "details": capability,
                    "logs": [],
                },
            }
        isolated_repo = artifact_dir / "isolated-mpos"
        isolated_internal = isolated_repo / "internal_filesystem"
        isolated_app = isolated_internal / "apps" / app_fullname
        try:
            shutil.copytree(repo / "internal_filesystem", isolated_internal)
            if isolated_app.exists():
                shutil.rmtree(isolated_app)
            shutil.copytree(app_source, isolated_app)
            (isolated_repo / "scripts").symlink_to(
                repo / "scripts", target_is_directory=True
            )
            (isolated_repo / "lvgl_micropython").symlink_to(
                repo / "lvgl_micropython", target_is_directory=True
            )
        except OSError as exc:
            return {
                "ok": False,
                "skipped": False,
                "error": {
                    "code": "DESKTOP_ISOLATION_FAILED",
                    "message": "Could not prepare an isolated desktop smoke workspace",
                    "stage": "test",
                    "owner": "toolchain",
                    "retryable": True,
                    "details": {"error": str(exc)},
                    "logs": [],
                },
            }
        command = [
            self._resolve_interpreter("python") or "python",
            str(capability["script"]),
            "--repo",
            str(isolated_repo),
            "--app-fullname",
            app_fullname,
            "--artifact-dir",
            str(artifact_dir),
            "--screenshot",
        ]
        if generation_result and generation_result.is_file():
            command.extend(["--generation-result", str(generation_result)])
        try:
            result = subprocess.run(
                command,
                cwd=isolated_repo,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "ok": False,
                "skipped": False,
                "error": {
                    "code": "SCRIPT_TIMEOUT",
                    "message": "Desktop smoke timed out",
                    "stage": "test",
                    "owner": "toolchain",
                    "retryable": True,
                    "details": {"timeout": timeout},
                    "logs": [(exc.stdout or "")[-4000:], (exc.stderr or "")[-4000:]],
                },
            }
        output = (result.stdout or "").strip()
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            payload = {}
        return {
            "ok": result.returncode == 0,
            "skipped": False,
            "returncode": result.returncode,
            "result": payload,
            "stdout": output[-8000:],
            "stderr": (result.stderr or "")[-8000:],
        }

    def run(self, operation: str, target: Path, timeout: int = 30) -> dict[str, Any]:
        if operation not in self.ALLOWED:
            return {
                "ok": False,
                "error": {
                    "code": "PERMISSION_DENIED",
                    "message": "该脚本操作不在服务器白名单中",
                    "stage": "runner",
                    "owner": "backend",
                    "retryable": False,
                    "details": {"operation": operation},
                    "logs": [],
                },
            }
        executable = self._resolve_interpreter(self.ALLOWED[operation][0])
        if not executable:
            return {
                "ok": False,
                "error": {
                    "code": "TOOLCHAIN_MISSING",
                    "message": "服务器没有找到 Python 工具链",
                    "stage": "runner",
                    "owner": "toolchain",
                    "retryable": True,
                    "details": {"operation": operation},
                    "logs": [],
                },
            }
        try:
            result = subprocess.run(
                [executable, *self.ALLOWED[operation][1:], str(target)],
                cwd=target.parent,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "error": {
                    "code": "SCRIPT_TIMEOUT",
                    "message": "白名单脚本执行超时",
                    "stage": "runner",
                    "owner": "toolchain",
                    "retryable": True,
                    "details": {"operation": operation, "timeout": timeout},
                    "logs": [],
                },
            }
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout[-4000:],
            "stderr": result.stderr[-4000:],
        }


def capability_reference() -> dict[str, Any]:
    """Pinned capability snapshot plus the versions a session must record.

    Exposed here so the runner surface reports exactly which Skills/MPOS
    commits and which capability schema a generation ran against.
    """
    index = capability_index()
    return {
        "board_capabilities_schema": index.schema_version,
        "board_capabilities_generated_at": index.generated_at,
        "capability_names": list(index.names),
        "selection_policy": dict(index.selection_policy),
        "hardware_doc_available": HARDWARE_DOC_PATH.is_file(),
        **capability_versions(),
    }


def api_summary_version() -> dict[str, str]:
    reference = SKILLS_ROOT / "mpos-dev" / "reference"
    result: dict[str, str] = {}
    for filename in ("mpos_api_summary.json", "lvgl_api_summary.json"):
        path = reference / filename
        if not path.is_file():
            result[filename] = "missing"
            continue
        raw = path.read_bytes()
        try:
            payload = json.loads(raw)
            version = payload.get("version") or payload.get("schema_version")
        except (json.JSONDecodeError, UnicodeDecodeError):
            version = None
        result[filename] = str(version or hashlib.sha256(raw).hexdigest()[:16])
    return result


mpos_skill_adapter = MposSkillAdapter()
script_dispatcher = ScriptDispatcher()
