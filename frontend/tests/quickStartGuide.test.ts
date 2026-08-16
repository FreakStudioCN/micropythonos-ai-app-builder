import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const appSource = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");

describe("quick start guide entry points", () => {
  it("exposes the public tutorial before and after sign-in", () => {
    expect(appSource).toContain("https://f1829ryac0m.feishu.cn/wiki/Kskcw9lZCiBHSgkswx7ctvGynPh");
    expect(appSource.match(/className=\"auth-guide-link\"/g)).toHaveLength(2);
    expect(appSource).toContain('className="quick-start-guide"');
    expect(appSource).toContain("快速上手 Blockless-Make");
  });
});
