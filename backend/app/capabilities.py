"""Capability contracts for cross-device MicroPythonOS App generation.

The browser product never asks the user to pick a board. Apps declare abstract
capabilities (``camera``, ``input.keypad``, ...) and the running MicroPythonOS
decides what actually exists. This module is the single place that reads the
pinned ``board_capabilities.json`` snapshot, so no other module has to keep a
hand-written list of capability names in sync with the Skills submodule.

Priority order mandated by docs/cross-device-capability-integration.md:

1. Runtime probe on the connected device.
2. ``DeviceInfo.hardware_id`` for diagnostics only.
3. This static snapshot as advisory metadata only.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

# Names a probe expression may rely on: Python builtins/keywords plus the two
# modules the probe harness imports for it. Anything else is a placeholder the
# caller has to fill in, which makes the probe non-auto-executable.
_PROBE_BOUND_NAMES = frozenset(
    {"bool", "int", "len", "lv", "None", "True", "False", "is", "not", "and", "or"}
)
_PROBE_IDENT_RE = re.compile(r"(?<![.\w])([A-Za-z_]\w*)")


def probe_free_names(probe: str) -> list[str]:
    """Identifiers a probe expression needs but the harness cannot bind.

    ``sensor.imu`` ships ``SensorManager.get_default_sensor(sensor_type)``,
    which is a template rather than a runnable expression. Executing it blind
    would raise NameError and look exactly like absent hardware, so callers
    must know up front that this probe cannot be auto-evaluated.
    """
    return sorted(
        {
            name
            for name in _PROBE_IDENT_RE.findall(probe or "")
            if name not in _PROBE_BOUND_NAMES and not name.endswith("Manager")
        }
    )

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = PROJECT_ROOT / "vendor" / "MicroPython_Skills"
MPOS_ROOT = PROJECT_ROOT / "vendor" / "MicroPythonOS"

BOARD_CAPABILITIES_PATH = (
    SKILLS_ROOT / "mpos-dev-web" / "reference" / "board_capabilities.json"
)
HARDWARE_DOC_PATH = (
    SKILLS_ROOT / "mpos-dev" / "reference" / "docs-hardware-capabilities.md"
)
EXPECTED_SCHEMA_VERSION = "mpos-board-capabilities-v1"

# Owner/retryable semantics are fixed by mpos-dev-web/reference/error_codes.md.
# A capability failure must be attributed to whoever can actually fix it, which
# is what keeps device and preview limits out of the App repair loop.
CAPABILITY_ERROR_SEMANTICS: dict[str, tuple[str, bool]] = {
    "MPOS_CAPABILITY_API_MISSING": ("micropythonos", False),
    "DIRECT_HARDWARE_ACCESS_FORBIDDEN": ("skill", True),
    "HARDWARE_CAPABILITY_UNAVAILABLE": ("device", False),
    "WEB_PREVIEW_UNSUPPORTED": ("external", False),
}

# Only DIRECT_HARDWARE_ACCESS_FORBIDDEN is the generator's fault, so it is the
# only capability error that may drive an automatic regeneration attempt.
REPAIRABLE_CAPABILITY_ERRORS = frozenset({"DIRECT_HARDWARE_ACCESS_FORBIDDEN"})


class CapabilityContractError(RuntimeError):
    """Raised when the pinned capability snapshot is missing or unusable."""


@dataclass(frozen=True)
class FeatureContract:
    """One capability's portability contract, straight from the snapshot."""

    name: str
    portable_api: bool
    contract_status: str = "full"
    availability_probe: str = ""
    preferred_api: str = ""
    allow_direct_driver: bool = False
    web_preview: str = ""
    physical_validation_required: bool = False
    limitations: tuple[str, ...] = ()
    destructive_operations: tuple[str, ...] = ()
    permission_required: bool = False
    reason: str = ""
    error_code: str = ""

    @property
    def generatable(self) -> bool:
        """Whether the generator may implement this capability at all."""
        return self.portable_api

    @property
    def partial(self) -> bool:
        return self.contract_status == "partial"

    @property
    def web_preview_supported(self) -> bool:
        """Web preview only counts as supported when the snapshot says so."""
        return self.web_preview in {"supported", "emulated"}

    def blocking_error_code(self) -> str:
        """Error code to emit when this capability cannot be generated."""
        if self.portable_api:
            return ""
        return self.error_code or "MPOS_CAPABILITY_API_MISSING"

    @property
    def unbound_probe_names(self) -> list[str]:
        return probe_free_names(self.availability_probe)

    @property
    def auto_executable_probe(self) -> bool:
        """Whether the probe can be run verbatim on a connected device."""
        return bool(
            self.portable_api
            and self.availability_probe
            and not self.unbound_probe_names
        )

    def to_public_dict(self) -> dict[str, Any]:
        """Shape handed to the frontend so it can label capabilities honestly."""
        return {
            "capability": self.name,
            "portable_api": self.portable_api,
            "contract_status": self.contract_status,
            "availability_probe": self.availability_probe,
            "auto_executable_probe": self.auto_executable_probe,
            "unbound_probe_names": self.unbound_probe_names,
            "preferred_api": self.preferred_api,
            "web_preview": self.web_preview,
            "web_preview_supported": self.web_preview_supported,
            "physical_validation_required": self.physical_validation_required,
            "permission_required": self.permission_required,
            "destructive_operations": list(self.destructive_operations),
            "limitations": list(self.limitations),
            "reason": self.reason,
            "blocking_error_code": self.blocking_error_code(),
        }


def _as_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    return ()


class BoardCapabilityIndex:
    """Read-only view over the pinned board capability snapshot."""

    def __init__(self, payload: dict[str, Any]) -> None:
        schema_version = payload.get("schema_version")
        if schema_version != EXPECTED_SCHEMA_VERSION:
            raise CapabilityContractError(
                "board_capabilities.json schema_version is "
                f"{schema_version!r}, expected {EXPECTED_SCHEMA_VERSION!r}"
            )
        contracts = payload.get("feature_contracts")
        if not isinstance(contracts, dict) or not contracts:
            raise CapabilityContractError(
                "board_capabilities.json has no feature_contracts"
            )
        self.schema_version: str = schema_version
        self.generated_at: str = str(payload.get("generated_at", ""))
        self.source: dict[str, Any] = payload.get("source") or {}
        self.selection_policy: dict[str, Any] = payload.get("selection_policy") or {}
        self.runtime_targets: list[Any] = payload.get("runtime_targets") or []
        self._boards: list[dict[str, Any]] = payload.get("boards") or []
        self._contracts: dict[str, FeatureContract] = {
            name: FeatureContract(
                name=name,
                portable_api=bool(entry.get("portable_api")),
                contract_status=str(entry.get("contract_status") or "full"),
                availability_probe=str(entry.get("availability_probe") or ""),
                preferred_api=str(entry.get("preferred_api") or ""),
                allow_direct_driver=bool(entry.get("allow_direct_driver")),
                web_preview=str(entry.get("web_preview") or ""),
                physical_validation_required=bool(
                    entry.get("physical_validation_required")
                ),
                limitations=_as_tuple(entry.get("limitations")),
                destructive_operations=_as_tuple(entry.get("destructive_operations")),
                permission_required=bool(entry.get("permission_required")),
                reason=str(entry.get("reason") or ""),
                error_code=str(entry.get("error_code") or ""),
            )
            for name, entry in contracts.items()
            if isinstance(entry, dict)
        }

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._contracts)

    @property
    def board_selection_required(self) -> bool:
        """Always false in this product; asserted so a snapshot flip is loud."""
        return bool(self.selection_policy.get("user_board_selection_required"))

    @property
    def unknown_board_allowed(self) -> bool:
        return bool(self.selection_policy.get("unknown_board_allowed", True))

    def has(self, name: str) -> bool:
        return name in self._contracts

    def contract(self, name: str) -> FeatureContract:
        try:
            return self._contracts[name]
        except KeyError as exc:
            raise CapabilityContractError(f"Unknown capability: {name}") from exc

    def get(self, name: str) -> FeatureContract | None:
        return self._contracts.get(name)

    def contracts_for(self, names: list[str] | tuple[str, ...]) -> list[FeatureContract]:
        """Contracts for known names, skipping anything not in the snapshot.

        Unknown names are not an error here: an unrecognised capability is
        reported separately so the caller can decide, and a future board that
        probes successfully at runtime must never be rejected by this table.
        """
        return [self._contracts[name] for name in names if name in self._contracts]

    def unknown_names(self, names: list[str] | tuple[str, ...]) -> list[str]:
        return [name for name in names if name not in self._contracts]

    def blocking(self, names: list[str] | tuple[str, ...]) -> list[FeatureContract]:
        """Capabilities MPOS has no portable API for; generation must stop."""
        return [c for c in self.contracts_for(names) if not c.generatable]

    def partial(self, names: list[str] | tuple[str, ...]) -> list[FeatureContract]:
        return [c for c in self.contracts_for(names) if c.partial]

    def preview_unsupported(
        self, names: list[str] | tuple[str, ...]
    ) -> list[FeatureContract]:
        """Capabilities the browser preview cannot honestly run."""
        return [
            c
            for c in self.contracts_for(names)
            if c.generatable and not c.web_preview_supported
        ]

    def physical_validation_required(self, names: list[str] | tuple[str, ...]) -> bool:
        return any(c.physical_validation_required for c in self.contracts_for(names))

    def destructive(self, names: list[str] | tuple[str, ...]) -> list[FeatureContract]:
        return [c for c in self.contracts_for(names) if c.destructive_operations]

    def board_hint(self, hardware_id: str) -> dict[str, Any] | None:
        """Advisory-only lookup. Never used to accept or reject a device."""
        if not hardware_id:
            return None
        for board in self._boards:
            if not isinstance(board, dict):
                continue
            candidates = {
                str(board.get("hardware_id") or ""),
                str(board.get("id") or ""),
                str(board.get("name") or ""),
            }
            if hardware_id in candidates - {""}:
                return board
        return None

    def public_contracts(
        self, names: list[str] | tuple[str, ...]
    ) -> list[dict[str, Any]]:
        return [c.to_public_dict() for c in self.contracts_for(names)]


def _read_snapshot(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise CapabilityContractError(
            "MPOS_CAPABILITY_SNAPSHOT_MISSING: "
            f"{path} is absent; update the vendored MicroPython_Skills submodule"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CapabilityContractError(
            f"board_capabilities.json is unreadable: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise CapabilityContractError("board_capabilities.json must be an object")
    return payload


@lru_cache(maxsize=1)
def capability_index() -> BoardCapabilityIndex:
    """Process-wide capability index built from the pinned snapshot."""
    return BoardCapabilityIndex(_read_snapshot(BOARD_CAPABILITIES_PATH))


def _submodule_commit(path: Path) -> str:
    """Resolve a vendored submodule commit, or 'unknown' when git is absent.

    Version pinning is metadata: a missing git binary must not take the API
    down, but it must also never silently look like a real commit.
    """
    if not path.is_dir():
        return "unknown"
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    commit = (result.stdout or "").strip()
    return commit if result.returncode == 0 and commit else "unknown"


@lru_cache(maxsize=1)
def capability_versions() -> dict[str, str]:
    """Pinned versions stored on every session so resume cannot drift.

    Restoring an old session must not silently pick up a different capability
    snapshot or a different set of MPOS APIs than the session was built with.
    """
    index = capability_index()
    return {
        "skill_commit": _submodule_commit(SKILLS_ROOT),
        "mpos_commit": _submodule_commit(MPOS_ROOT),
        "board_capabilities_schema": index.schema_version,
        "board_capabilities_generated_at": index.generated_at,
        "board_capabilities_snapshot_commit": str(
            index.source.get("snapshot_commit") or "unknown"
        ),
    }


def hardware_capability_doc() -> str:
    """Hardware capability guidance shipped with the pinned Skills submodule."""
    if not HARDWARE_DOC_PATH.is_file():
        raise CapabilityContractError(
            f"MPOS_CAPABILITY_SNAPSHOT_MISSING: {HARDWARE_DOC_PATH} is absent"
        )
    return HARDWARE_DOC_PATH.read_text(encoding="utf-8")


def capability_error(
    code: str,
    message: str,
    *,
    stage: str,
    capability: str = "",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a structured error with owner/retryable fixed by the code table."""
    try:
        owner, retryable = CAPABILITY_ERROR_SEMANTICS[code]
    except KeyError as exc:
        raise CapabilityContractError(f"Unknown capability error code: {code}") from exc
    payload = dict(details or {})
    if capability:
        payload["capability"] = capability
    return {
        "code": code,
        "message": message,
        "stage": stage,
        "owner": owner,
        "retryable": retryable,
        "details": payload,
        "logs": [],
    }


def is_capability_error(code: str) -> bool:
    return code in CAPABILITY_ERROR_SEMANTICS


def allows_code_repair(code: str) -> bool:
    """Whether a capability error may trigger automatic App regeneration.

    Preview limits, absent device hardware, and missing OS APIs are not the
    App's fault; feeding them into the repair loop is how sessions spin.
    """
    return code in REPAIRABLE_CAPABILITY_ERRORS
