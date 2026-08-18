import { describe, expect, it } from "vitest";

import { buildHardwareRequirements } from "../src/App";

describe("hardware requirement request", () => {
  it("does not turn portable board capabilities into external accessories", () => {
    expect(buildHardwareRequirements(["camera", "sensor.imu"])).toEqual({
      requiredAccessories: [],
      runtimeFallbacks: {
        camera: "show unavailable state",
        "sensor.imu": "show unavailable state",
      },
      physicalValidationRequired: true,
    });
  });

  it("does not require physical validation without hardware capabilities", () => {
    expect(buildHardwareRequirements([])).toEqual({
      requiredAccessories: [],
      runtimeFallbacks: {},
      physicalValidationRequired: false,
    });
  });
});
