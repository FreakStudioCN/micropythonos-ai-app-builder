from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = PROJECT_ROOT / "vendor" / "MicroPython_Skills"

STAGE_SKILLS = {
    "analyze": "mpos-analyze-app-web",
    "generate": "mpos-gen-app-web",
    "test": "mpos-test-app-web",
    "package": "mpos-package-app-web",
    "deploy": "mpos-deploy-app-web",
    "publish-check": "mpos-publish-app-web",
}

STAGE_CHECKPOINTS = {
    "analyze": "requirements_analyzed",
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

    def contract(self, stage: str) -> SkillContract:
        name = STAGE_SKILLS.get(stage)
        if not name:
            raise SkillContractError(f"Unknown runner stage: {stage}")
        path = (self.skills_root / name / "SKILL.md").resolve()
        if self.skills_root.resolve() not in path.parents or not path.is_file():
            raise SkillContractError(f"MPOS_NOT_FOUND: {name}/SKILL.md is missing")
        data = path.read_bytes()
        text = data.decode("utf-8")
        if f"name: {name}" not in text:
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
        executable = shutil.which(self.ALLOWED[operation][0])
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


class DeviceService:
    """Read-only capability probe. Device writes remain unavailable by default."""

    def __init__(self) -> None:
        self._locks: dict[str, bool] = {}

    def capabilities(self) -> dict[str, Any]:
        mpremote = shutil.which("mpremote")
        return {
            "serial_port_scan": False,
            "physical_device": bool(mpremote),
            "mpremote": bool(mpremote),
            "firmware_flash": False,
            "install_url": "https://install.micropythonos.com/",
        }

    def scan(self) -> dict[str, Any]:
        # pyserial is intentionally not a mandatory backend dependency. Returning
        # a truthful empty result is safer than probing arbitrary host devices.
        return {
            "ports": [],
            "supported": False,
            "message": "当前服务未启用串口扫描；请先使用系统安装器安装 MicroPythonOS。",
            "install_url": "https://install.micropythonos.com/",
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
device_service = DeviceService()
