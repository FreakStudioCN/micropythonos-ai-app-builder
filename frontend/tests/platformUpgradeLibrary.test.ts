import { describe, expect, it } from "vitest";

import {
  buildShowcaseRunMessage,
  encodeShowcaseMpk,
  fetchVerifiedShowcaseMpk,
  getBridgeTargetOrigin,
  hasGenerationActivityChanged,
  isPlatformActionAllowed,
  normalizePublicSystemStatus,
  MAX_SHOWCASE_MPK_BYTES,
  readVerifiedShowcaseMpk,
  resolveTrustedShowcaseMpkUrl,
  unavailablePublicSystemStatus,
} from "../src/platformUpgradeLibrary";

describe("platform action gate", () => {
  it("allows actions only after a confirmed non-maintenance status", () => {
    expect(isPlatformActionAllowed(false, false)).toBe(false);
    expect(isPlatformActionAllowed(true, true)).toBe(false);
    expect(isPlatformActionAllowed(true, false)).toBe(true);
  });

  it("normalizes only explicit, internally consistent system status payloads", () => {
    expect(normalizePublicSystemStatus({
      status: "ready",
      maintenance_mode: false,
      message: "",
      retry_after_seconds: 300,
    })).toEqual({
      status: "ready",
      maintenance: false,
      message: "",
      retry_after_seconds: 300,
    });
    expect(normalizePublicSystemStatus({
      status: "maintenance",
      maintenance: true,
      message: "upgrade",
      retry_after_seconds: 5,
    })).toEqual({
      status: "maintenance",
      maintenance: true,
      message: "upgrade",
      retry_after_seconds: 5,
    });
    expect(normalizePublicSystemStatus({ status: "ready", maintenance: true })).toBeNull();
    expect(normalizePublicSystemStatus({
      status: "ready",
      maintenance: false,
      maintenance_mode: true,
    })).toBeNull();
    expect(normalizePublicSystemStatus({ maintenance: false })).toBeNull();
    expect(unavailablePublicSystemStatus().status).toBe("unavailable");
  });
});

describe("generation activity", () => {
  const initial = {
    status: "running",
    checkpoint_id: "requirements_analyzed",
    revision_id: "r1",
  };

  it("refreshes only for status, checkpoint, or revision changes", () => {
    expect(hasGenerationActivityChanged(null, initial)).toBe(true);
    expect(hasGenerationActivityChanged(initial, { ...initial })).toBe(false);
    expect(hasGenerationActivityChanged(initial, { ...initial, status: "completed" })).toBe(true);
    expect(hasGenerationActivityChanged(initial, { ...initial, checkpoint_id: "package_done" })).toBe(true);
    expect(hasGenerationActivityChanged(initial, { ...initial, revision_id: "r2" })).toBe(true);
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

  it("accepts only relative showcase paths or same-origin HTTPS URLs", () => {
    const pageUrl = "https://builder.example/apps";
    expect(resolveTrustedShowcaseMpkUrl(
      "/showcase/mpks/com.blockless.demo001_r1.mpk",
      pageUrl,
    ).href).toBe("https://builder.example/showcase/mpks/com.blockless.demo001_r1.mpk");
    expect(resolveTrustedShowcaseMpkUrl(
      "https://builder.example/showcase/mpks/com.blockless.demo001_r1.mpk",
      pageUrl,
    ).origin).toBe("https://builder.example");
    expect(() => resolveTrustedShowcaseMpkUrl(
      "https://evil.example/showcase/mpks/demo.mpk",
      pageUrl,
    )).toThrow("same-origin");
    expect(() => resolveTrustedShowcaseMpkUrl(
      "http://builder.example/showcase/mpks/demo.mpk",
      pageUrl,
    )).toThrow();
    expect(() => resolveTrustedShowcaseMpkUrl("../private/demo.mpk", pageUrl)).toThrow();
  });

  it("checks Content-Length, streams bytes, and verifies SHA-256", async () => {
    const bytes = new TextEncoder().encode("MPK");
    const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
    const sha256 = Array.from(digest, (byte) => byte.toString(16).padStart(2, "0")).join("");
    const verified = await readVerifiedShowcaseMpk(new Response(bytes, {
      headers: { "content-length": String(bytes.byteLength) },
    }), sha256);
    expect(verified).toEqual(bytes);
    await expect(readVerifiedShowcaseMpk(new Response(bytes, {
      headers: { "content-length": String(MAX_SHOWCASE_MPK_BYTES + 1) },
    }), sha256)).rejects.toThrow("exceeds 4 MiB");
    await expect(readVerifiedShowcaseMpk(new Response(bytes), "0".repeat(64))).rejects.toThrow("does not match");
  });

  it("validates the URL before fetching and uses a bounded verified response", async () => {
    const bytes = new TextEncoder().encode("MPK");
    const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
    const sha256 = Array.from(digest, (byte) => byte.toString(16).padStart(2, "0")).join("");
    const fetcher = async () => new Response(bytes);
    await expect(fetchVerifiedShowcaseMpk(
      "/showcase/mpks/demo.mpk",
      sha256,
      "https://builder.example/",
      fetcher,
    )).resolves.toEqual(bytes);
  });
});

describe("WASM bridge origin", () => {
  it("derives an exact HTTP(S) target origin", () => {
    expect(getBridgeTargetOrigin(
      "/mpos-web/index.html",
      "https://builder.example/apps",
    )).toBe("https://builder.example");
    expect(getBridgeTargetOrigin(
      "https://runtime.example/index.html",
      "https://builder.example/apps",
    )).toBe("https://runtime.example");
    expect(() => getBridgeTargetOrigin(
      "data:text/html,unsafe",
      "https://builder.example/apps",
    )).toThrow("HTTP(S)");
  });
});
