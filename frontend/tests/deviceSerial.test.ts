import { spawnSync } from "node:child_process";

import { describe, expect, it } from "vitest";

import { buildFastPathExtractorLine } from "../src/deviceSerial";

describe("buildFastPathExtractorLine", () => {
  it("keeps the extractor inside the fast-path receiver try suite", () => {
    const extractorLine = buildFastPathExtractorLine(
      "apps/com.example.myapp",
      "com.example.myapp",
    );
    const source = [
      "try:",
      " pass",
      extractorLine,
      "except Exception:",
      " pass",
    ].join("\n");
    const result = spawnSync(
      "python3",
      [
        "-c",
        "import sys; compile(sys.stdin.read(), '<fast-path-receiver>', 'exec')",
      ],
      { input: source, encoding: "utf8" },
    );

    expect(result.status, result.stderr).toBe(0);
    expect(extractorLine).toMatch(/^ _extractor = StreamingUnzip\(/);
  });
});
