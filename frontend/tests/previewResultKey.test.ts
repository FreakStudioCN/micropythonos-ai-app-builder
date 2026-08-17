import { describe, expect, it } from "vitest";

import { previewResultKey } from "../src/App";

describe("previewResultKey", () => {
  it("scopes the key to the revision so a later preview is not deduped away", () => {
    // The backend drops a preview result whose key it has already recorded.
    // Equal keys here meant every revision after r1 hung in waiting_preview.
    expect(previewResultKey("sess_abc", 1)).not.toBe(previewResultKey("sess_abc", 2));
  });

  it("keeps a retry of the same preview idempotent", () => {
    expect(previewResultKey("sess_abc", 3)).toBe(previewResultKey("sess_abc", 3));
  });

  it("defaults a missing revision to r1 rather than undefined", () => {
    expect(previewResultKey("sess_abc", undefined)).toBe("preview-success-sess_abc-r1");
  });
});
