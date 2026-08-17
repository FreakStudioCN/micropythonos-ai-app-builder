/**
 * Capability model shared by the App creation flow and the device panel.
 *
 * The browser never offers a board selector. A request declares abstract
 * capabilities, and only a connected MicroPythonOS decides what exists. Every
 * label below therefore distinguishes "the OS has no API", "the preview cannot
 * run it", and "the device does not have it" — three different owners, three
 * different next steps.
 */

export interface CapabilityContract {
  capability: string;
  portable_api: boolean;
  contract_status: string;
  availability_probe: string;
  auto_executable_probe: boolean;
  unbound_probe_names: string[];
  preferred_api: string;
  web_preview: string;
  web_preview_supported: boolean;
  physical_validation_required: boolean;
  permission_required: boolean;
  destructive_operations: string[];
  limitations: string[];
  reason: string;
  blocking_error_code: string;
}

export interface CapabilityAnalysis {
  required_capabilities: string[];
  required_accessories: string[];
  runtime_fallbacks: Record<string, string>;
  physical_validation_required: boolean;
  capability_contracts: CapabilityContract[];
  blocking_capabilities: { capability: string; code: string; reason: string }[];
  partial_capabilities: { capability: string; limitations: string[] }[];
  web_preview_unsupported: string[];
  destructive_capabilities: { capability: string; operations: string[] }[];
  unrecognized_capabilities: string[];
}

export interface CapabilityProbeOutcome {
  capability: string;
  /** null means "not measured" — never render this as "unsupported". */
  available: boolean | null;
  probe: string;
  detail: string;
}

/**
 * How a capability should be presented before any device is connected.
 *
 * `waiting_os_api` and `preview_only` are deliberately distinct: the first is a
 * MicroPythonOS gap that no amount of regenerating the App can fix, the second
 * is just a browser limit.
 */
export type CapabilityStatus =
  | "portable"
  | "waiting_os_api"
  | "preview_only"
  | "device_required"
  | "device_available"
  | "device_unavailable"
  | "device_unknown";

export const capabilityStatus = (
  contract: CapabilityContract,
  probe?: CapabilityProbeOutcome,
): CapabilityStatus => {
  if (!contract.portable_api) return "waiting_os_api";
  if (probe) {
    if (probe.available === null) return "device_unknown";
    return probe.available ? "device_available" : "device_unavailable";
  }
  if (!contract.web_preview_supported) return "device_required";
  return contract.contract_status === "partial" ? "preview_only" : "portable";
};

const STATUS_TEXT: Record<CapabilityStatus, { zh: string; en: string }> = {
  portable: { zh: "可移植", en: "Portable" },
  waiting_os_api: { zh: "等待 OS API", en: "Waiting for OS API" },
  preview_only: { zh: "已模拟（有限制）", en: "Emulated (limited)" },
  device_required: { zh: "预览不支持，连接设备后可用", en: "Preview unsupported — needs a device" },
  device_available: { zh: "设备已确认可用", en: "Confirmed on device" },
  device_unavailable: { zh: "设备不可用", en: "Unavailable on this device" },
  device_unknown: { zh: "未测出，需人工确认", en: "Not measured — check manually" },
};

export const capabilityStatusText = (status: CapabilityStatus, language: "zh" | "en") =>
  STATUS_TEXT[status][language];

/**
 * Whether an "AI fixes the code" action may be offered for this status.
 *
 * Missing OS APIs, absent device hardware and preview limits are not App
 * defects, so offering a repair button there sends users into a loop that
 * cannot converge.
 */
export const allowsAutoRepair = (status: CapabilityStatus) =>
  status !== "waiting_os_api" &&
  status !== "device_unavailable" &&
  status !== "device_required" &&
  status !== "device_unknown";

/** Operations that must be confirmed one by one before they run. */
export const CONFIRMATION_REQUIRED = [
  "audio.input",
  "storage.sdcard",
] as const;

export const needsExplicitConfirmation = (contract: CapabilityContract) =>
  contract.permission_required ||
  contract.destructive_operations.length > 0 ||
  (CONFIRMATION_REQUIRED as readonly string[]).includes(contract.capability);
