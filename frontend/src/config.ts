const env = import.meta.env as Record<string, string | undefined>;

const stripTrailingSlash = (value: string) => value.replace(/\/+$/, "");

export const API_BASE_URL = stripTrailingSlash(
  env.VITE_API_BASE?.trim() || "http://localhost:8000",
);

const wasmBaseUrl = stripTrailingSlash(
  env.VITE_WASM_BASE?.trim() || API_BASE_URL,
);

export const WASM_RUNTIME_URL =
  `${wasmBaseUrl}/mpos-web/index.html?embed=1&bridge=3`;

export const UPLOAD_CHUNK_SIZE = (() => {
  const raw = Number(env.VITE_UPLOAD_CHUNK_SIZE);
  if (!Number.isFinite(raw) || raw < 128 || raw > 8192) return 512;
  return Math.floor(raw / 4) * 4;
})();

export const GENERATION_TIMEOUT_MS = (() => {
  const raw = Number(env.VITE_GENERATION_TIMEOUT_MS);
  if (!Number.isFinite(raw) || raw < 30_000) return 600_000;
  return raw;
})();
