/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
  readonly VITE_WASM_BASE?: string;
  readonly VITE_UPLOAD_CHUNK_SIZE?: string;
  readonly VITE_GENERATION_TIMEOUT_MS?: string;
  readonly VITE_MPOS_API_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
