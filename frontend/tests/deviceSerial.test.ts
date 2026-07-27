import { spawnSync } from "node:child_process";

import { describe, expect, it } from "vitest";

import {
  buildFastPathExtractorLine,
  DeviceProbeError,
  runDeviceInstallStage,
  runInstallPreflight,
} from "../src/deviceSerial";
import {
  stageIndexForCheckpoint,
  stageIndexForError,
  stages,
} from "../src/App";

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

describe("runInstallPreflight", () => {
  it("does not enter MEMORY or install work when PING does not answer", async () => {
    const commands: string[] = [];
    const execute = async (source: string) => {
      commands.push(source);
      throw new Error("Timed out waiting for the ESP32");
    };

    const failure = runInstallPreflight({
      execute,
      remainingTime: (timeoutMs) => timeoutMs,
      tokenFactory: () => "fixed",
    });

    await expect(failure).rejects.toMatchObject({
      name: "DeviceProbeError",
      stage: "PING",
    });
    expect(commands).toHaveLength(1);
    expect(commands[0]).toContain("__MPOS_PING_fixed__");
    expect(commands[0]).not.toContain("__MPOS_MEMORY_");
  });

  it("reports MEMORY separately after a successful PING", async () => {
    let callCount = 0;
    const execute = async (source: string) => {
      callCount += 1;
      if (callCount === 1) {
        return source.match(/__MPOS_PING_[A-Za-z0-9_]+__/)?.[0] || "";
      }
      throw new Error("Timed out waiting for the ESP32");
    };

    await expect(runInstallPreflight({
      execute,
      remainingTime: (timeoutMs) => timeoutMs,
      tokenFactory: () => "fixed",
    })).rejects.toMatchObject({
      name: "DeviceProbeError",
      stage: "MEMORY",
    });
    expect(callCount).toBe(2);
  });
});

describe("device install stage errors", () => {
  it.each(["READY", "TRANSFER"] as const)("labels %s timeouts", async (stage) => {
    await expect(runDeviceInstallStage(stage, async () => {
      throw new Error("Timed out waiting for the ESP32");
    })).rejects.toEqual(expect.objectContaining({
      name: "DeviceProbeError",
      stage,
      message: expect.stringContaining(`[${stage}]`),
    }));
  });

  it("keeps DeviceProbeError identifiable", () => {
    expect(new DeviceProbeError("PING", "no response")).toMatchObject({
      name: "DeviceProbeError",
      stage: "PING",
    });
  });
});

describe("onboarding stage mapping", () => {
  it("uses the fixed seven frontend stages", () => {
    expect(stages.map(([stage]) => stage)).toEqual([
      "analysis",
      "api_check",
      "generation",
      "test",
      "package",
      "deploy",
      "publish",
    ]);
  });

  it("maps deploy and publish to separate stages", () => {
    expect(stageIndexForError("deploy")).toBe(5);
    expect(stageIndexForError("publish")).toBe(6);
    expect(stageIndexForCheckpoint("package_done")).toBe(5);
    expect(stageIndexForCheckpoint("device_deploy_done")).toBe(6);
  });
});
