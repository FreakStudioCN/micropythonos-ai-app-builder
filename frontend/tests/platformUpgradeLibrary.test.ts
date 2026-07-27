import { describe, expect, it } from "vitest";

import {
  buildShowcaseRunMessage,
  encodeShowcaseMpk,
  isPlatformActionAllowed,
  MAX_SHOWCASE_MPK_BYTES,
} from "../src/platformUpgradeLibrary";

describe("platform action gate", () => {
  it("allows actions only after a confirmed non-maintenance status", () => {
    expect(isPlatformActionAllowed(false, false)).toBe(false);
    expect(isPlatformActionAllowed(true, true)).toBe(false);
    expect(isPlatformActionAllowed(true, false)).toBe(true);
  });
});

describe("showcase MPK preparation", () => {
  it("encodes a bounded MPK and builds the WASM bridge message", () => {
    const mpkBase64 = encodeShowcaseMpk(new TextEncoder().encode("MPK"));
    expect(mpkBase64).toBe("TVBL");
    expect(buildShowcaseRunMessage("com.blockless.demo001", mpkBase64)).toEqual({
      source: "mpos-builder",
      type: "RUN_MPK",
      packageName: "com.blockless.demo001",
      mpkBase64: "TVBL",
    });
  });

  it("rejects empty and oversized MPKs", () => {
    expect(() => encodeShowcaseMpk(new Uint8Array())).toThrow("empty");
    expect(() => encodeShowcaseMpk(
      new Uint8Array(MAX_SHOWCASE_MPK_BYTES + 1),
    )).toThrow("exceeds 4 MiB");
  });
});
