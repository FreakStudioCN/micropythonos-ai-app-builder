export const apiFetch = (
  input: RequestInfo | URL,
  init: RequestInit = {},
) => fetch(input, { ...init, credentials: "include" });

const messageFromPayload = (payload: unknown): string => {
  if (!payload || typeof payload !== "object") return "";
  const record = payload as Record<string, unknown>;
  if (typeof record.detail === "string") return record.detail;
  if (record.detail && typeof record.detail === "object") {
    const message = (record.detail as Record<string, unknown>).message;
    if (typeof message === "string") return message;
  }
  if (record.error && typeof record.error === "object") {
    const message = (record.error as Record<string, unknown>).message;
    if (typeof message === "string") return message;
  }
  return typeof record.message === "string" ? record.message : "";
};

export const apiErrorMessage = async (
  response: Response,
  fallback: string,
): Promise<string> => {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    const payload = await response.json().catch(() => null);
    return messageFromPayload(payload) || fallback;
  }
  const text = await response.text().catch(() => "");
  return text.trim() || fallback;
};
