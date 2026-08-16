export interface WasmFailure {
  code?: string;
  message?: string;
  repairable?: boolean;
}

export type WasmFailureKind = "application" | "infrastructure";

const applicationCodes = new Set([
  "APP_EXECUTION_TIMEOUT",
  "APP_RUNTIME_ERROR",
  "APP_START_MARKER_MISSING",
]);

const infrastructureCodes = new Set([
  "WASM_BRIDGE_ERROR",
  "WASM_NOT_READY",
  "WASM_REPL_STARTUP_TIMEOUT",
  "WASM_RAW_REPL_TIMEOUT",
  "WASM_STARTUP_TIMEOUT",
  "WEB_PREVIEW_UNSUPPORTED",
  "HARDWARE_CAPABILITY_UNAVAILABLE",
  "MPOS_CAPABILITY_API_MISSING",
]);
const nonRepairableCapabilityCodes = new Set([
  "WEB_PREVIEW_UNSUPPORTED",
  "HARDWARE_CAPABILITY_UNAVAILABLE",
  "MPOS_CAPABILITY_API_MISSING",
]);

/**
 * Only generated-app failures may spend another AI generation. Unknown bridge
 * errors fail closed as infrastructure failures so a browser/runtime problem
 * can never start an expensive generation loop.
 */
export const classifyWasmFailure = (failure: WasmFailure): WasmFailureKind => {
  const code = String(failure.code || "").toUpperCase();
  if (nonRepairableCapabilityCodes.has(code)) return "infrastructure";
  if (failure.repairable === true) return "application";
  if (failure.repairable === false) return "infrastructure";

  if (applicationCodes.has(code) || code.startsWith("APP_")) return "application";
  if (infrastructureCodes.has(code) || code.startsWith("WASM_")) return "infrastructure";

  const message = String(failure.message || "");
  if (/Traceback \(most recent call last\)|generated app failed to start|packaged app failed to start|没有出现启动标记/i.test(message)) {
    return "application";
  }
  if (/raw REPL|asyncio REPL|_webterm|WASM.*(?:尚未就绪|启动超时|not ready|startup)|等待 MicroPython 输出超时/i.test(message)) {
    return "infrastructure";
  }
  return "infrastructure";
};
