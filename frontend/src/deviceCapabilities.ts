/**
 * Runtime capability probing over Web Serial.
 *
 * Kept out of `deviceSerial.ts` so the transport client stays about transport.
 * These helpers only run once the user has authorised the device.
 *
 * The important rule here is that a probe which fails to evaluate reports
 * `available: null`, never `false`. A NameError from a probe template must not
 * be laundered into "this board has no camera".
 */

import type { CapabilityContract, CapabilityProbeOutcome } from "./capabilities";

export type DeviceExecute = (source: string, timeoutMs?: number) => Promise<string>;

const MARKER = "MPOSCAP";
const HW_MARKER = "MPOSHWID";
/** DeviceInfo default when a board never sets a real id. */
const MISSING_HARDWARE_ID = "missing-hardware-info";

/** Manager names a probe expression needs imported from `mpos`. */
export const managersInProbe = (probe: string): string[] =>
  [...new Set(probe.match(/\b[A-Z][A-Za-z0-9]*Manager\b/g) ?? [])];

const pyStr = (value: string) => JSON.stringify(value);

/**
 * Read `DeviceInfo.hardware_id`. Diagnostics only: an empty result is not a
 * failure, and an unrecognised id never disqualifies the device.
 */
export const readHardwareId = async (execute: DeviceExecute): Promise<string> => {
  const source = [
    "try:",
    "    from mpos import DeviceInfo",
    `    print(${pyStr(HW_MARKER)} + ":" + str(DeviceInfo.hardware_id))`,
    "except Exception as exc:",
    `    print(${pyStr(HW_MARKER)} + ":")`,
  ].join("\n");
  const output = await execute(source, 10_000);
  const match = output.match(new RegExp(`${HW_MARKER}:(.*)`));
  const value = match ? match[1].trim() : "";
  // MicroPythonOS ships `hardware_id = "missing-hardware-info"` as the class
  // default, so ports that never call set_hardware_id return that literal.
  // Reporting it as a detected id produces a misleading "unlisted board" note.
  return value === MISSING_HARDWARE_ID ? "" : value;
};

/**
 * Evaluate one capability probe on the device.
 *
 * Contracts whose probe still contains a placeholder (`sensor.imu` ships
 * `SensorManager.get_default_sensor(sensor_type)`) are reported as unmeasured
 * rather than executed blind.
 */
export const probeCapability = async (
  execute: DeviceExecute,
  contract: CapabilityContract,
): Promise<CapabilityProbeOutcome> => {
  const base: CapabilityProbeOutcome = {
    capability: contract.capability,
    available: null,
    probe: contract.availability_probe,
    detail: "",
  };
  if (!contract.portable_api) {
    return { ...base, detail: contract.reason || "no portable MicroPythonOS API" };
  }
  if (!contract.auto_executable_probe) {
    return {
      ...base,
      detail: contract.unbound_probe_names.length
        ? `probe needs ${contract.unbound_probe_names.join(", ")}; verify manually`
        : "no runnable probe expression",
    };
  }
  const managers = managersInProbe(contract.availability_probe);
  const source = [
    "try:",
    ...(managers.length ? [`    from mpos import ${managers.join(", ")}`] : []),
    "    import lvgl as lv",
    `    print(${pyStr(MARKER)} + ":" + str(bool(${contract.availability_probe})))`,
    "except Exception as exc:",
    `    print(${pyStr(MARKER)} + ":ERR:" + repr(exc))`,
  ].join("\n");

  const output = await execute(source, 15_000);
  const match = output.match(new RegExp(`${MARKER}:(ERR:)?([^\\r\\n]*)`));
  if (!match) {
    return { ...base, detail: "probe produced no parsable output" };
  }
  if (match[1]) {
    // The probe raised. That is a measurement failure, not a hardware verdict.
    return { ...base, detail: match[2].trim() };
  }
  return { ...base, available: match[2].trim() === "True" };
};

/** Probe every required capability, in order, tolerating individual failures. */
export const probeCapabilities = async (
  execute: DeviceExecute,
  contracts: CapabilityContract[],
): Promise<CapabilityProbeOutcome[]> => {
  const results: CapabilityProbeOutcome[] = [];
  for (const contract of contracts) {
    try {
      results.push(await probeCapability(execute, contract));
    } catch (error) {
      results.push({
        capability: contract.capability,
        available: null,
        probe: contract.availability_probe,
        detail: error instanceof Error ? error.message : String(error),
      });
    }
  }
  return results;
};
