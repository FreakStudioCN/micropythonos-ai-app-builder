"""Mandatory hardware policy gate for generated MicroPythonOS Apps.

The AST rules that reject board modules, GPIO/bus constructors and native
hardware drivers live in the vendored Skills submodule
(``mpos-gen-app/scripts/check_app_hardware_policy.py``). This module *invokes*
that script rather than reimplementing it, so the browser backend and the CLI
skill can never drift into disagreeing about what is forbidden.

On top of the gate it adds the checks the integration spec requires but the
script does not cover: runtime fallbacks, focus/keypad navigation, hardware
lifecycle cleanup, and hard-coded board identifiers.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

from .capabilities import (
    SKILLS_ROOT,
    CapabilityContractError,
    capability_error,
    capability_index,
)

POLICY_SCRIPT = SKILLS_ROOT / "mpos-gen-app" / "scripts" / "check_app_hardware_policy.py"
POLICY_SCHEMA_VERSION = "mpos-hardware-policy-v1"
_SAFE_FULLNAME_RE = re.compile(r"[^A-Za-z0-9_.-]")

# Hardware that holds a resource while open. These must release on pause/exit or
# the Launcher comes back with broken input, which is the failure the spec calls
# out explicitly.
STATEFUL_CAPABILITIES = ("camera", "audio.output", "audio.input", "lights.rgb")

LIFECYCLE_HOOKS = ("onPause", "onStop", "onDestroy")

# Manager release/stop calls that count as real cleanup.
_CLEANUP_CALL_RE = re.compile(
    r"\.(stop|close|deinit|release|clear|off|delete|stop_preview|stop_recording)\s*\(",
)

# A generated App must not pin itself to one board.
_BOARD_ID_RE = re.compile(
    r"\b(esp32[\w-]*|waveshare[\w-]*|lilygo[\w-]*|t-?display[\w-]*|"
    r"m5stack[\w-]*|pico[\w-]*)\b",
    re.IGNORECASE,
)
_GPIO_TABLE_RE = re.compile(
    r"\b(gpio|pin|scl|sda|mosi|miso|sclk|bus_?id|i2c_?id|spi_?id|uart_?id)\s*"
    r"[:=]\s*\d+",
    re.IGNORECASE,
)


_PROBE_METHOD_RE = re.compile(r"\b[A-Z][A-Za-z0-9]*Manager\.(\w+)")


def _probe_method(probe: str) -> str:
    """The Manager method a probe expression actually calls."""
    match = _PROBE_METHOD_RE.search(probe or "")
    return match.group(1) if match else ""


def _strip_comments(code: str) -> str:
    """Source with ``#`` comments removed, preserving line structure."""
    return '\n'.join(line.split("#", 1)[0] for line in code.splitlines())


class PolicyGateError(RuntimeError):
    """Raised when the gate itself cannot run; never a generated-code verdict."""


def _validate_stateful_names() -> None:
    index = capability_index()
    unknown = [name for name in STATEFUL_CAPABILITIES if not index.has(name)]
    if unknown:
        raise CapabilityContractError(
            f"STATEFUL_CAPABILITIES references unknown ids: {', '.join(unknown)}"
        )


def _python_executable() -> str:
    if sys.executable:
        return sys.executable
    found = shutil.which("python3") or shutil.which("python")
    if not found:
        raise PolicyGateError("TOOLCHAIN_MISSING: no Python interpreter for the gate")
    return found


def run_hardware_policy_gate(
    app_code: str,
    *,
    app_fullname: str,
    entrypoint: str = "app.py",
    allow_direct_hardware: bool = False,
    timeout: int = 30,
) -> dict[str, Any]:
    """Run the vendored policy script over freshly generated code.

    ``allow_direct_hardware`` is only ever true for a confirmed external
    accessory handoff. Onboard hardware never earns this exception.
    """
    if not POLICY_SCRIPT.is_file():
        raise PolicyGateError(
            "MPOS_CAPABILITY_SNAPSHOT_MISSING: "
            f"{POLICY_SCRIPT} is absent; update the vendored Skills submodule"
        )
    # The gate script only accepts [A-Za-z0-9_.-] as a directory name, while a
    # package name may legitimately contain CJK (pydantic's isalnum() allows
    # it). The verdict does not depend on the name, so stage under a safe one
    # rather than failing every generation with an opaque gate error.
    staged_fullname = _SAFE_FULLNAME_RE.sub("_", app_fullname) or "generated_app"
    with tempfile.TemporaryDirectory(prefix="mpos-policy-") as tmp:
        repo = Path(tmp)
        app_dir = repo / "internal_filesystem" / "apps" / staged_fullname
        (app_dir / "assets").mkdir(parents=True, exist_ok=True)
        (app_dir / "assets" / entrypoint).write_text(app_code, encoding="utf-8")
        command = [
            _python_executable(),
            str(POLICY_SCRIPT),
            "--repo",
            str(repo),
            "--app-fullname",
            staged_fullname,
        ]
        if allow_direct_hardware:
            command.append("--allow-direct-hardware")
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise PolicyGateError("SCRIPT_TIMEOUT: hardware policy gate") from exc
        except OSError as exc:
            raise PolicyGateError(f"hardware policy gate failed to start: {exc}") from exc

    stdout = (completed.stdout or "").strip()
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise PolicyGateError(
            "hardware policy gate returned non-JSON output: "
            f"{stdout[:400]!r} / {(completed.stderr or '')[:400]!r}"
        ) from exc
    if payload.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise PolicyGateError(
            f"unexpected policy schema {payload.get('schema_version')!r}"
        )
    return payload


def policy_violations_to_errors(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert gate output into structured errors the session can store."""
    return [
        capability_error(
            "DIRECT_HARDWARE_ACCESS_FORBIDDEN",
            str(item.get("message") or "Direct board hardware access is forbidden"),
            stage="generation",
            details={
                "symbol": item.get("symbol"),
                "path": item.get("path"),
                "line": item.get("line"),
            },
        )
        for item in payload.get("errors", [])
        if isinstance(item, dict)
    ]


def check_board_identifiers(code: str) -> list[str]:
    """Flag board names and GPIO/bus tables baked into a portable App."""
    warnings: list[str] = []
    # Only inspect string literals and assignments, so a word like "pico" inside
    # a comment does not fail an otherwise portable App.
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return warnings
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            match = _BOARD_ID_RE.search(node.value)
            if match:
                warnings.append(
                    f"第 {node.lineno} 行写入了板卡标识 {match.group(0)!r}；"
                    "可移植 App 不得绑定具体板卡"
                )
    # Comments are prose, not a pin map; scanning them contradicts the
    # AST-only rule this function documents above.
    for match in _GPIO_TABLE_RE.finditer(_strip_comments(code)):
        warnings.append(
            f"检测到 GPIO/总线映射 {match.group(0)!r}；引脚和总线归 MicroPythonOS 所有"
        )
    return warnings


def check_runtime_fallbacks(code: str, capabilities: Iterable[str]) -> list[str]:
    """Every generatable capability needs a runtime probe and a fallback path."""
    _validate_stateful_names()
    index = capability_index()
    warnings: list[str] = []
    for name in capabilities:
        contract = index.get(name)
        if contract is None or not contract.generatable:
            continue
        probe = contract.availability_probe
        if not probe:
            continue
        # Take the method off the *Manager* call, not off the outer expression:
        # `bool(AudioManager.get_outputs())` split naively yields "bool", which
        # both warns on correct code and is satisfied by any `bool(...)` call.
        method = _probe_method(probe)
        if method and method not in code:
            warnings.append(
                f"能力 {name} 缺少运行时探测（应调用 {probe}）"
            )
    return warnings


def check_focus_navigation(code: str) -> list[str]:
    """Interactive Apps must not assume a touchscreen.

    Pointer-only interaction locks out keypad and encoder devices, so a visible
    focus path is required whenever the App is interactive at all.
    """
    if "add_event_cb" not in code:
        return []
    focus_markers = (
        "lv.group",
        "group_create",
        "add_obj",
        "set_group",
        "INDEV_TYPE.KEYPAD",
        "INDEV_TYPE.ENCODER",
        "add_indev",
    )
    if any(marker in code for marker in focus_markers):
        return []
    return [
        "交互 App 只提供了指针操作，必须补充可见的 LVGL focus/keypad 导航"
    ]


def check_lifecycle_cleanup(code: str, capabilities: Iterable[str]) -> list[str]:
    """Stateful hardware must release on pause/exit."""
    _validate_stateful_names()
    stateful = [name for name in capabilities if name in STATEFUL_CAPABILITIES]
    if not stateful:
        return []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    hooks = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name in LIFECYCLE_HOOKS
    }
    if not hooks:
        return [
            "使用了 " + "、".join(stateful) + "，但没有实现 "
            + "/".join(LIFECYCLE_HOOKS)
            + "，退出后硬件资源不会释放"
        ]
    if not any(
        _CLEANUP_CALL_RE.search(ast.get_source_segment(code, node) or "")
        for node in hooks.values()
    ):
        return [
            "生命周期回调中没有释放硬件资源；"
            + "、".join(stateful)
            + " 必须在暂停或退出时停止并归还资源"
        ]
    return []


def evaluate_generated_app(
    code: str,
    *,
    capabilities: Iterable[str],
    app_fullname: str,
    allow_direct_hardware: bool = False,
) -> dict[str, Any]:
    """Full capability verdict for one generated App.

    Returns the gate payload, blocking structured errors, and advisory warnings.
    Callers must treat a non-empty ``errors`` list as a hard generation failure.
    """
    names = list(capabilities)
    gate = run_hardware_policy_gate(
        code,
        app_fullname=app_fullname,
        allow_direct_hardware=allow_direct_hardware,
    )
    errors = policy_violations_to_errors(gate) if gate.get("result") != "success" else []
    warnings = [
        *check_board_identifiers(code),
        *check_runtime_fallbacks(code, names),
        *check_focus_navigation(code),
        *check_lifecycle_cleanup(code, names),
    ]
    return {
        "policy": gate,
        "errors": errors,
        "warnings": warnings,
        "passed": not errors,
    }
