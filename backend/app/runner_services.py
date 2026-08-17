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

BOARD_CAPABILITIES_PATH = (
    SKILLS_ROOT / "mpos-dev-web" / "reference" / "board_capabilities.json"
)
BUNDLED_BOARD_CAPABILITIES_PATH = (
    Path(__file__).resolve().parent / "contracts" / "board_capabilities.json"
)
HARDWARE_CAPABILITIES_DOC = (
    SKILLS_ROOT / "mpos-dev" / "reference" / "docs-hardware-capabilities.md"
)


class SkillContractError(RuntimeError):
    pass


class HardwareCapabilityRegistry:
    """Authoritative capability contract; static board rows are advisory only."""

    def __init__(self, path: Path = BOARD_CAPABILITIES_PATH) -> None:
        self.path = path

    def _active_path(self) -> Path:
        if self.path.is_file():
            return self.path
        # The skills repository is a separately-versioned submodule.  A fresh
        # checkout can legitimately lack a newly-added reference file, so keep
        # the runtime contract bundled with the backend as a safe fallback.
        if self.path == BOARD_CAPABILITIES_PATH and BUNDLED_BOARD_CAPABILITIES_PATH.is_file():
            return BUNDLED_BOARD_CAPABILITIES_PATH
        raise SkillContractError("MPOS_CAPABILITY_CONTRACT_MISSING")

    def load(self) -> dict[str, Any]:
        path = self._active_path()
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload.get("feature_contracts"), dict):
            raise SkillContractError("MPOS_CAPABILITY_CONTRACT_INVALID")
        return payload

    def describe(self) -> dict[str, Any]:
        path = self._active_path()
        payload = self.load()
        raw = path.read_bytes()
        return {
            "schema_version": payload.get("schema_version"),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "path": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "documentation": str(HARDWARE_CAPABILITIES_DOC.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        }

    def infer(self, prompt: str) -> list[str]:
        text = prompt.casefold()
        keywords = {
            "camera": ("camera", "摄像", "相机", "拍照", "二维码"),
            "audio.input": ("microphone", "mic", "麦克风", "录音", "语音输入"),
            "audio.output": ("speaker", "audio", "扬声器", "播放声音", "蜂鸣"),
            "sensor.imu": ("imu", "accelerometer", "gyroscope", "陀螺仪", "加速度"),
            "sensor.environmental": ("temperature sensor", "humidity", "温湿度", "气压"),
            "lights.rgb": ("rgb", "neopixel", "彩灯", "灯带"),
            "battery": ("battery", "电池", "电量"),
            "storage.sdcard": ("sd card", "sdcard", "存储卡", "sd 卡"),
            "network": ("wifi", "network", "联网", "网络"),
            "gps": ("gps", "定位", "经纬度"),
            "infrared": ("infrared", "ir remote", "红外"),
            "lora": ("lora", "远距离无线"),
            "input.pointer": ("touch", "pointer", "触摸", "鼠标"),
            "input.encoder": ("encoder", "旋钮", "编码器"),
            "input.keypad": ("keypad", "键盘", "按键矩阵"),
        }
        return [name for name, terms in keywords.items() if any(term in text for term in terms)]

    def resolve(
        self,
        required: list[str],
        fallbacks: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        payload = self.load()
        contracts = payload["feature_contracts"]
        unknown = sorted(set(required) - set(contracts))
        if unknown:
            return {
                "status": "blocked",
                "error": {
                    "code": "MPOS_CAPABILITY_API_MISSING",
                    "message": "MicroPythonOS has no capability contract for: " + ", ".join(unknown),
                    "owner": "os_api",
                    "retryable": False,
                    "details": {"capabilities": unknown},
                },
            }
        selected = {name: contracts[name] for name in required}
        missing_api = [name for name, item in selected.items() if not item.get("portable_api")]
        if missing_api:
            return {
                "status": "blocked",
                "error": {
                    "code": "MPOS_CAPABILITY_API_MISSING",
                    "message": "Required capability is not exposed by a portable Manager API: " + ", ".join(missing_api),
                    "owner": "os_api",
                    "retryable": False,
                    "details": {"capabilities": missing_api},
                },
            }
        partial = [name for name, item in selected.items() if item.get("contract_status") == "partial"]
        return {
            "status": "partial" if partial else "portable" if selected else "not_required",
            "required_capabilities": required,
            "contracts": selected,
            "runtime_fallbacks": fallbacks or {},
            "physical_validation_required": any(
                bool(item.get("physical_validation_required")) for item in selected.values()
            ),
            "warnings": [
                f"{name}: " + "; ".join(item.get("limitations", []))
                for name, item in selected.items()
                if item.get("limitations")
            ],
            "partial_capabilities": partial,
        }


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

    def run_hardware_policy(
        self, repo: Path, app_fullname: str, timeout: int = 30
    ) -> dict[str, Any]:
        script = SKILLS_ROOT / "mpos-gen-app" / "scripts" / "check_app_hardware_policy.py"
        executable = self._resolve_interpreter("python")
        if not script.is_file() or not executable:
            return {"ok": False, "error": {"code": "TOOLCHAIN_MISSING", "owner": "toolchain", "retryable": True}}
        try:
            result = subprocess.run(
                [
                    executable,
                    str(script),
                    "--repo",
                    str(repo),
                    "--app-fullname",
                    app_fullname,
                ],
                cwd=repo,
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
                    "owner": "toolchain",
                    "retryable": True,
                    "details": {"timeout": timeout},
                },
            }
        try:
            payload = json.loads((result.stdout or "").strip())
        except json.JSONDecodeError:
            payload = {}
        return {
            "ok": result.returncode == 0,
            "result": payload,
            "stdout": result.stdout[-8000:],
            "stderr": result.stderr[-8000:],
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
hardware_capability_registry = HardwareCapabilityRegistry()
script_dispatcher = ScriptDispatcher()
device_service = DeviceService()
