import { describe, expect, it } from "vitest";

import { getGenerationWaitTimeoutKind } from "../src/App";

describe("generation wait timeout", () => {
  it("does not impose a client deadline when both limits are disabled", () => {
    expect(
      getGenerationWaitTimeoutKind(
        24 * 60 * 60 * 1000,
        0,
        0,
        0,
        0,
      ),
    ).toBeNull();
  });

  it("still supports explicit deployment-specific limits", () => {
    expect(getGenerationWaitTimeoutKind(91_000, 0, 0, 90_000, 600_000)).toBe("idle");
    expect(getGenerationWaitTimeoutKind(601_000, 0, 600_000, 90_000, 600_000)).toBe("overall");
  });
});
