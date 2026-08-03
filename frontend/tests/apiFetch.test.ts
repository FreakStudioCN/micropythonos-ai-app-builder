import { afterEach, describe, expect, it, vi } from "vitest";

import { apiErrorMessage, apiFetch } from "../src/apiFetch";

describe("apiFetch", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("always includes the login cookie for cross-port API requests", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await apiFetch("http://localhost:8000/api/sessions/session-1/permissions/allow-all", {
      method: "POST",
      credentials: "omit",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/sessions/session-1/permissions/allow-all",
      expect.objectContaining({ method: "POST", credentials: "include" }),
    );
  });

  it("keeps the backend error message instead of replacing it with a generic failure", async () => {
    const response = new Response(
      JSON.stringify({ error: { message: "登录状态已失效，请重新登录" } }),
      { status: 401, headers: { "Content-Type": "application/json" } },
    );

    await expect(apiErrorMessage(response, "生成失败")).resolves.toBe(
      "登录状态已失效，请重新登录",
    );
  });
});
