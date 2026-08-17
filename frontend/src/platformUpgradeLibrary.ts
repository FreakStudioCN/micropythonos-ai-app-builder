export const MAX_SHOWCASE_MPK_BYTES = 4 * 1024 * 1024;

export interface PublicSystemStatus {
  status: "ready" | "maintenance" | "unavailable";
  maintenance: boolean;
  message: string;
  retry_after_seconds: number;
}

export interface GenerationActivitySnapshot {
  status: string;
  checkpoint_id?: string;
  revision_id?: string;
}

export const unavailablePublicSystemStatus = (): PublicSystemStatus => ({
  status: "unavailable",
  maintenance: false,
  message: "",
  retry_after_seconds: 15,
});

export const normalizePublicSystemStatus = (
  value: unknown,
): PublicSystemStatus | null => {
  if (!value || typeof value !== "object") return null;
  const payload = value as Record<string, unknown>;
  if (payload.status !== "ready" && payload.status !== "maintenance") return null;
  const maintenance = typeof payload.maintenance_mode === "boolean"
    ? payload.maintenance_mode
    : typeof payload.maintenance === "boolean"
      ? payload.maintenance
      : null;
  if (maintenance === null) return null;
  if (
    typeof payload.maintenance_mode === "boolean"
    && typeof payload.maintenance === "boolean"
    && payload.maintenance_mode !== payload.maintenance
  ) return null;
  if ((payload.status === "maintenance") !== maintenance) return null;
  const retry = Number(payload.retry_after_seconds);
  return {
    status: payload.status,
    maintenance,
    message: typeof payload.message === "string" ? payload.message : "",
    retry_after_seconds: Number.isFinite(retry) && retry > 0 ? retry : 15,
  };
};

export const isPlatformActionAllowed = (
  systemStatusConfirmed: boolean,
  maintenance: boolean,
) => systemStatusConfirmed && !maintenance;

export const hasGenerationActivityChanged = (
  previous: GenerationActivitySnapshot | null,
  current: GenerationActivitySnapshot,
) => previous === null
  || previous.status !== current.status
  || previous.checkpoint_id !== current.checkpoint_id
  || previous.revision_id !== current.revision_id;

export const isValidShowcaseSha256 = (value: unknown): value is string => (
  typeof value === "string" && /^[a-f0-9]{64}$/i.test(value)
);

export const resolveTrustedShowcaseMpkUrl = (
  value: string,
  pageUrl: string,
) => {
  const base = new URL(pageUrl);
  const isRelative = !/^[a-z][a-z\d+.-]*:/i.test(value) && !value.startsWith("//");
  const normalizedRelative = value.replace(/^\.\/+/, "");
  if (
    isRelative
    && !value.startsWith("/showcase/mpks/")
    && !normalizedRelative.startsWith("showcase/mpks/")
  ) {
    throw new Error("The showcase MPK must use a relative showcase path");
  }
  const resolved = new URL(value, base);
  if (resolved.origin !== base.origin) {
    throw new Error("The showcase MPK must be same-origin");
  }
  if (!isRelative && resolved.protocol !== "https:") {
    throw new Error("The showcase MPK URL must use HTTPS");
  }
  if (
    !resolved.pathname.startsWith("/showcase/mpks/")
    || !resolved.pathname.endsWith(".mpk")
    || resolved.username
    || resolved.password
    || resolved.search
    || resolved.hash
    || /%(?:2e|2f|5c)/i.test(resolved.pathname)
  ) {
    throw new Error("The showcase MPK URL is not a trusted showcase path");
  }
  return resolved;
};

export const getBridgeTargetOrigin = (
  runtimeUrl: string,
  pageUrl: string,
) => {
  const resolved = new URL(runtimeUrl, pageUrl);
  if (resolved.protocol !== "https:" && resolved.protocol !== "http:") {
    throw new Error("The WASM bridge requires an HTTP(S) origin");
  }
  return resolved.origin;
};

export const encodeShowcaseMpk = (bytes: Uint8Array) => {
  if (!bytes.length) throw new Error("The showcase MPK is empty");
  if (bytes.length > MAX_SHOWCASE_MPK_BYTES) {
    throw new Error("The showcase MPK exceeds 4 MiB");
  }
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return globalThis.btoa(binary);
};

export const readVerifiedShowcaseMpk = async (
  response: Response,
  expectedSha256: string,
  subtle: SubtleCrypto | undefined = globalThis.crypto?.subtle,
) => {
  if (!response.ok) throw new Error(`MPK download returned ${response.status}`);
  if (!isValidShowcaseSha256(expectedSha256)) {
    throw new Error("The showcase MPK SHA-256 is invalid");
  }
  const contentLength = response.headers.get("content-length");
  if (contentLength !== null) {
    const normalizedLength = contentLength.trim();
    if (!/^\d+$/.test(normalizedLength)) {
      throw new Error("The showcase MPK Content-Length is invalid");
    }
    if (Number(normalizedLength) > MAX_SHOWCASE_MPK_BYTES) {
      throw new Error("The showcase MPK exceeds 4 MiB");
    }
  }
  if (!response.body) throw new Error("The showcase MPK response is not streamable");

  const chunks: Uint8Array[] = [];
  const reader = response.body.getReader();
  let total = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > MAX_SHOWCASE_MPK_BYTES) {
      await reader.cancel().catch(() => undefined);
      throw new Error("The showcase MPK exceeds 4 MiB");
    }
    chunks.push(value);
  }
  if (!total) throw new Error("The showcase MPK is empty");

  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  if (!subtle) throw new Error("SHA-256 verification is unavailable");
  const digest = new Uint8Array(await subtle.digest("SHA-256", bytes));
  const actualSha256 = Array.from(digest, (byte) => byte.toString(16).padStart(2, "0")).join("");
  if (actualSha256 !== expectedSha256.toLowerCase()) {
    throw new Error("The showcase MPK SHA-256 does not match the catalog");
  }
  return bytes;
};

export const fetchVerifiedShowcaseMpk = async (
  mpkUrl: string,
  expectedSha256: string,
  pageUrl: string,
  fetcher: typeof fetch = globalThis.fetch,
) => {
  const trustedUrl = resolveTrustedShowcaseMpkUrl(mpkUrl, pageUrl);
  const response = await fetcher(trustedUrl, {
    credentials: "same-origin",
    redirect: "error",
  });
  return readVerifiedShowcaseMpk(response, expectedSha256);
};

export const buildShowcaseRunMessage = (
  packageName: string,
  mpkBase64: string,
) => ({
  source: "mpos-builder" as const,
  type: "RUN_MPK" as const,
  packageName,
  mpkBase64,
});

/**
 * Which generation-wait budget ran out: no activity for a while, or the
 * overall ceiling. Lives here with the other generation-wait helpers.
 */
export type GenerationWaitTimeoutKind = "idle" | "overall";
export const getGenerationWaitTimeoutKind = (
  now: number,
  startedAt: number,
  lastActivityAt: number,
  idleTimeoutMs: number,
  overallTimeoutMs: number,
): GenerationWaitTimeoutKind | null => {
  if (now - startedAt >= overallTimeoutMs) return "overall";
  if (now - lastActivityAt >= idleTimeoutMs) return "idle";
  return null;
};

/**
 * Idempotency key for a browser preview result.
 *
 * The revision has to be part of it. The backend drops a preview result whose
 * key it has already seen, so a key built from the session alone meant every
 * revision after the first re-sent r1's key, was discarded as a duplicate, and
 * left that revision stuck in `waiting_preview` forever.
 */
export const previewResultKey = (
  outcome: "success" | "partial",
  sessionId: string,
  revision: number | undefined,
) => `preview-${outcome}-${sessionId}-r${revision ?? 1}`;
