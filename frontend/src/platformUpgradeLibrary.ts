export const MAX_SHOWCASE_MPK_BYTES = 4 * 1024 * 1024;

export const isPlatformActionAllowed = (
  systemStatusConfirmed: boolean,
  maintenance: boolean,
) => systemStatusConfirmed && !maintenance;

export const encodeShowcaseMpk = (bytes: Uint8Array) => {
  if (!bytes.length) throw new Error("The showcase MPK is empty");
  if (bytes.length > MAX_SHOWCASE_MPK_BYTES) {
    throw new Error("The showcase MPK exceeds 4 MiB");
  }
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return globalThis.btoa(binary);
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
