import { describe, expect, it } from "vitest";

import {
  describeProviderResult,
  emptyProviderCatalog,
  normalizeProviderCatalog,
} from "../src/providerRouting";

describe("normalizeProviderCatalog", () => {
  it("keeps only safe metadata for the fixed provider ids", () => {
    const catalog = normalizeProviderCatalog({
      default_provider: "deepseek_primary",
      providers: [{
        id: "deepseek_primary",
        label: "Primary",
        configured: true,
        model: "deepseek-chat",
        api_key: "must-not-leak",
        base_url: "https://secret.example",
      }, {
        id: "unknown",
        label: "Unknown",
        configured: true,
        model: "unknown",
      }],
    });

    expect(catalog?.defaultProvider).toBe("deepseek_primary");
    expect(catalog?.providers).toHaveLength(4);
    expect(catalog?.providers[1]).toEqual({
      id: "deepseek_primary",
      label: "Primary",
      configured: true,
      model: "deepseek-chat",
    });
    expect(JSON.stringify(catalog)).not.toContain("must-not-leak");
    expect(JSON.stringify(catalog)).not.toContain("secret.example");
    expect(JSON.stringify(catalog)).not.toContain("unknown\"");
  });

  it("disables providers missing from backend metadata", () => {
    const catalog = normalizeProviderCatalog({
      providers: [{ id: "aigocode", label: "AIGoCode", configured: true, model: "code" }],
      default_provider: "auto",
    });

    expect(catalog?.defaultProvider).toBe("aigocode");
    expect(catalog?.providers.find((item) => item.id === "auto")?.configured).toBe(false);
    expect(catalog?.providers.find((item) => item.id === "aigocode")?.configured).toBe(true);
  });

  it("rejects an invalid response shape", () => {
    expect(normalizeProviderCatalog(null)).toBeNull();
    expect(normalizeProviderCatalog({ providers: "invalid" })).toBeNull();
  });
});

describe("describeProviderResult", () => {
  it("shows the actual provider, model, and safe failover path", () => {
    const providers = emptyProviderCatalog().providers.map((provider) => ({
      ...provider,
      configured: true,
    }));
    expect(describeProviderResult({
      provider: "aigocode",
      model: "aigo-code",
      failover_used: true,
      attempted_providers: ["deepseek_primary", "aigocode"],
    }, providers)).toEqual({
      provider: "AIGoCode GLM",
      model: "aigo-code",
      failoverUsed: true,
      attempted: ["DeepSeek Primary", "AIGoCode GLM"],
    });
  });
});
