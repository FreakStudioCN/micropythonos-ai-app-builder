"""Device capability probing for authorised, connected MicroPythonOS devices.

Split out of ``runner_services`` because device work now owns real logic: it
plans the runtime probes the browser must run over Web Serial, then judges the
returned evidence.

The judgement rule is fixed by the integration spec: the runtime probe wins.
``board_capabilities.json`` may only add advisory context, and an unlisted
board that probes successfully is a perfectly valid device.
"""

from __future__ import annotations

import shutil
from typing import Any, Iterable

from .capabilities import (
    capability_error,
    capability_index,
    capability_versions,
)


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

    def evaluate_probe_results(
        self,
        *,
        required_capabilities: Iterable[str],
        results: Iterable[Any],
        hardware_id: str = "",
    ) -> dict[str, Any]:
        """Judge runtime evidence from a connected device.

        Returns the evidence to persist, blocking structured errors for
        capabilities the device lacks, and advisory notes where the static
        snapshot disagrees with what the device actually reported.
        """
        index = capability_index()
        required = list(required_capabilities)
        observed: dict[str, dict[str, Any]] = {}
        for item in results:
            entry = item if isinstance(item, dict) else item.model_dump()
            name = str(entry.get("capability") or "")
            if name:
                observed[name] = entry

        errors: list[dict[str, Any]] = []
        warnings: list[str] = []
        evidence: list[dict[str, Any]] = []
        board = index.board_hint(hardware_id)

        for name in required:
            contract = index.get(name)
            probe = observed.get(name)
            if contract is not None and not contract.portable_api:
                errors.append(
                    capability_error(
                        contract.blocking_error_code(),
                        contract.reason
                        or f"MicroPythonOS 暂无 {name} 的可移植 App 能力 API",
                        stage="deploy",
                        capability=name,
                    )
                )
                continue
            if probe is None:
                warnings.append(f"能力 {name} 尚未在设备上完成运行时探测")
                continue
            available = probe.get("available")
            evidence.append(
                {
                    "capability": name,
                    "available": available,
                    "probe": probe.get("probe")
                    or (contract.availability_probe if contract else ""),
                    "detail": probe.get("detail", ""),
                    "source": "runtime_probe",
                }
            )
            if available is None:
                # Not measured is not the same as absent. Ask for a manual
                # check instead of declaring the hardware missing.
                warnings.append(
                    f"能力 {name} 的探测未能求值"
                    + (f"（{probe.get('detail')}）" if probe.get("detail") else "")
                    + "，需要人工在真机确认，不能据此判定硬件缺失"
                )
                continue
            if not available:
                errors.append(
                    capability_error(
                        "HARDWARE_CAPABILITY_UNAVAILABLE",
                        f"已连接设备没有提供 {name} 能力",
                        stage="deploy",
                        capability=name,
                        details={"hardware_id": hardware_id},
                    )
                )
                continue
            # Runtime probe succeeded. If the static table disagreed, the table
            # is the thing that is out of date.
            if board is not None:
                # The snapshot key is os_registrations; "capabilities" never
                # existed, so this drift warning could never fire.
                listed = board.get("os_registrations")
                if isinstance(listed, (list, tuple)) and name not in listed:
                    warnings.append(
                        f"设备实测支持 {name}，但静态板卡表未收录；以运行时探测为准"
                    )

        if hardware_id and board is None:
            warnings.append(
                f"板卡 {hardware_id} 不在静态快照中；运行时探测通过即视为合法设备"
            )

        return {
            "detected_hardware_id": hardware_id,
            "runtime_capability_results": evidence,
            "errors": errors,
            "warnings": warnings,
            "board_metadata_is_advisory": True,
            "capability_versions": capability_versions(),
        }


device_service = DeviceService()
