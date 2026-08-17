import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  allowsAutoRepair,
  capabilityStatus,
  needsExplicitConfirmation,
  type CapabilityContract,
} from "../src/capabilities";
import { managersInProbe, probeCapability } from "../src/deviceCapabilities";

const contract = (over: Partial<CapabilityContract> = {}): CapabilityContract => ({
  capability: "camera",
  portable_api: true,
  contract_status: "full",
  availability_probe: "CameraManager.has_camera()",
  auto_executable_probe: true,
  unbound_probe_names: [],
  preferred_api: "CameraManager/CameraActivity",
  web_preview: "unsupported_without_emulation",
  web_preview_supported: false,
  physical_validation_required: true,
  permission_required: false,
  destructive_operations: [],
  limitations: [],
  reason: "",
  blocking_error_code: "",
  ...over,
});

describe("capabilityStatus", () => {
  it("separates an OS API gap from absent device hardware", () => {
    const missingApi = contract({
      portable_api: false,
      blocking_error_code: "MPOS_CAPABILITY_API_MISSING",
    });
    expect(capabilityStatus(missingApi)).toBe("waiting_os_api");

    const absent = capabilityStatus(contract(), {
      capability: "camera",
      available: false,
      probe: "",
      detail: "",
    });
    expect(absent).toBe("device_unavailable");
  });

  it("reports an unmeasured probe as unknown, not unsupported", () => {
    const status = capabilityStatus(contract(), {
      capability: "sensor.imu",
      available: null,
      probe: "",
      detail: "probe needs sensor_type",
    });
    expect(status).toBe("device_unknown");
  });

  it("marks preview-incapable hardware as needing a device", () => {
    expect(capabilityStatus(contract())).toBe("device_required");
  });
});

describe("allowsAutoRepair", () => {
  it("never offers an AI fix for OS, device or preview limits", () => {
    for (const status of [
      "waiting_os_api",
      "device_unavailable",
      "device_required",
      "device_unknown",
    ] as const) {
      expect(allowsAutoRepair(status)).toBe(false);
    }
    expect(allowsAutoRepair("portable")).toBe(true);
  });
});

describe("needsExplicitConfirmation", () => {
  it("requires separate confirmation for destructive and permissioned hardware", () => {
    expect(
      needsExplicitConfirmation(
        contract({ capability: "storage.sdcard", destructive_operations: ["format"] }),
      ),
    ).toBe(true);
    expect(needsExplicitConfirmation(contract({ capability: "audio.input" }))).toBe(true);
    expect(needsExplicitConfirmation(contract())).toBe(false);
  });
});

describe("managersInProbe", () => {
  it("collects the managers a probe expression needs imported", () => {
    expect(managersInProbe("bool(AudioManager.get_outputs())")).toEqual(["AudioManager"]);
    expect(
      managersInProbe("InputManager.has_indev_type(lv.INDEV_TYPE.KEYPAD)"),
    ).toEqual(["InputManager"]);
  });
});

describe("probeCapability", () => {
  it("returns available=true when the device prints True", async () => {
    const result = await probeCapability(async () => "MPOSCAP:True\n", contract());
    expect(result.available).toBe(true);
  });

  it("returns available=false when the device prints False", async () => {
    const result = await probeCapability(async () => "MPOSCAP:False\n", contract());
    expect(result.available).toBe(false);
  });

  it("keeps a raising probe unknown instead of calling the hardware absent", async () => {
    const result = await probeCapability(
      async () => "MPOSCAP:ERR:AttributeError('has_camera')\n",
      contract(),
    );
    expect(result.available).toBeNull();
    expect(result.detail).toContain("AttributeError");
  });

  it("does not execute a probe that still has a placeholder", async () => {
    let executed = false;
    const result = await probeCapability(
      async () => {
        executed = true;
        return "MPOSCAP:True\n";
      },
      contract({
        capability: "sensor.imu",
        availability_probe: "SensorManager.get_default_sensor(sensor_type) is not None",
        auto_executable_probe: false,
        unbound_probe_names: ["sensor_type"],
      }),
    );
    expect(executed).toBe(false);
    expect(result.available).toBeNull();
    expect(result.detail).toContain("sensor_type");
  });

  it("does not execute a probe for a capability MPOS has no API for", async () => {
    let executed = false;
    const result = await probeCapability(
      async () => {
        executed = true;
        return "MPOSCAP:True\n";
      },
      contract({ capability: "gps", portable_api: false, reason: "no portable API" }),
    );
    expect(executed).toBe(false);
    expect(result.available).toBeNull();
  });
});

/*
 * The capability panels shipped once with no stylesheet at all: every class
 * name resolved to nothing and the read-outs rendered as unstyled text. Nothing
 * failed, so nothing caught it. This is the cheapest guard against a repeat.
 */
describe("capability stylesheet", () => {
  const read = (name: string) =>
    readFileSync(fileURLToPath(new URL(`../src/${name}`, import.meta.url)), "utf8");
  const css = read("capabilityStyles.css");
  const panel = read("CapabilityPanel.tsx");

  const classNames = new Set<string>();
  for (const match of panel.matchAll(/className=(?:"([^"]*)"|\{`([^`]*)`\})/g)) {
    for (const token of (match[1] ?? match[2] ?? "").split(/\s+/)) {
      if (token && !token.includes("${")) classNames.add(token);
    }
  }

  it("styles every class the capability panels render", () => {
    expect(classNames.size).toBeGreaterThan(10);
    const missing = [...classNames].filter((name) => !css.includes(`.${name}`));
    expect(missing).toEqual([]);
  });

  it("gives every capability status its own visual treatment", () => {
    // "not measured" must never be styled as "hardware missing".
    const statuses = [
      "portable",
      "waiting_os_api",
      "preview_only",
      "device_required",
      "device_available",
      "device_unavailable",
      "device_unknown",
    ];
    const missing = statuses.filter((name) => !css.includes(`.status-${name}`));
    expect(missing).toEqual([]);
  });
});
