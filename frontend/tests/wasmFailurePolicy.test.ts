import { describe, expect, it } from "vitest";
import { classifyWasmFailure } from "../src/wasmFailurePolicy";

describe("classifyWasmFailure", () => {
  it("honors an explicit application repair signal", () => {
    expect(classifyWasmFailure({ code: "WASM_BRIDGE_ERROR", repairable: true })).toBe("application");
  });

  it("honors an explicit infrastructure signal", () => {
    expect(classifyWasmFailure({ code: "APP_RUNTIME_ERROR", repairable: false })).toBe("infrastructure");
  });

  it("does not send raw REPL startup timeouts to AI repair", () => {
    expect(classifyWasmFailure({
      message: '等待 MicroPython 输出超时："raw REPL; CTRL-B to exit\\r\\n"',
    })).toBe("infrastructure");
  });

  it("classifies runtime tracebacks as generated-app failures", () => {
    expect(classifyWasmFailure({ message: "Traceback (most recent call last): ValueError" })).toBe("application");
  });

  it("fails closed for unknown bridge failures", () => {
    expect(classifyWasmFailure({ message: "unexpected browser bridge state" })).toBe("infrastructure");
  });
});
