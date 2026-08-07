import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const bridgeSource = readFileSync(
  new URL("../public/mpos-web/index.html", import.meta.url),
  "utf8",
);

describe("MicroPythonOS WASM bridge contract", () => {
  it("uses the Web aiorepl paste protocol instead of waiting for raw REPL", () => {
    expect(bridgeSource).toContain("async function enterPasteRepl(runId)");
    expect(bridgeSource).toContain("Module.__webterm.push([0x05])");
    expect(bridgeSource).not.toContain('await waitFor("raw REPL; CTRL-B to exit');
  });

  it("waits for a non-echoable completion token", () => {
    expect(bridgeSource).toContain('var completionToken = "MPOS_EXEC_DONE_"');
    expect(bridgeSource).toContain('await waitFor(completionToken + "\\r\\n", 45000)');
  });

  it("reports bridge failures with a code and repairability signal", () => {
    expect(bridgeSource).toContain('code: String(error && error.code || "WASM_BRIDGE_ERROR")');
    expect(bridgeSource).toContain("repairable: Boolean(error && error.repairable)");
    expect(bridgeSource).toContain('"WASM_REPL_STARTUP_TIMEOUT"');
  });
});
