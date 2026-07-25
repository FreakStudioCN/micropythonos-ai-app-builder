export const AI_PROVIDER_IDS = [
  "auto",
  "deepseek_primary",
  "deepseek_secondary",
  "aigocode",
] as const;

export type AiProviderId = (typeof AI_PROVIDER_IDS)[number];

export interface AiProviderMetadata {
  id: AiProviderId;
  label: string;
  configured: boolean;
  model: string;
}

export interface AiProviderCatalog {
  providers: AiProviderMetadata[];
  defaultProvider: AiProviderId;
}

export interface AiProviderResult {
  provider?: string;
  model?: string;
  failover_used?: boolean;
  attempted_providers?: string[];
}

const fallbackLabels: Record<AiProviderId, string> = {
  auto: "Auto",
  deepseek_primary: "DeepSeek Primary",
  deepseek_secondary: "DeepSeek Secondary",
  aigocode: "AIGoCode GLM",
};

export const isAiProviderId = (value: unknown): value is AiProviderId =>
  typeof value === "string" && AI_PROVIDER_IDS.includes(value as AiProviderId);

export const emptyProviderCatalog = (): AiProviderCatalog => ({
  providers: AI_PROVIDER_IDS.map((id) => ({
    id,
    label: fallbackLabels[id],
    configured: false,
    model: "",
  })),
  defaultProvider: "auto",
});

export const normalizeProviderCatalog = (value: unknown): AiProviderCatalog | null => {
  if (!value || typeof value !== "object") return null;
  const payload = value as Record<string, unknown>;
  if (!Array.isArray(payload.providers)) return null;

  const received = new Map<AiProviderId, AiProviderMetadata>();
  for (const item of payload.providers) {
    if (!item || typeof item !== "object") continue;
    const provider = item as Record<string, unknown>;
    if (!isAiProviderId(provider.id)) continue;
    received.set(provider.id, {
      id: provider.id,
      label: typeof provider.label === "string" && provider.label.trim()
        ? provider.label.trim()
        : fallbackLabels[provider.id],
      configured: provider.configured === true,
      model: typeof provider.model === "string" ? provider.model : "",
    });
  }

  const providers = AI_PROVIDER_IDS.map((id) => received.get(id) || {
    id,
    label: fallbackLabels[id],
    configured: false,
    model: "",
  });
  const requestedDefault = isAiProviderId(payload.default_provider)
    ? payload.default_provider
    : "auto";
  const defaultProvider = providers.find(
    (provider) => provider.id === requestedDefault && provider.configured,
  )?.id || providers.find((provider) => provider.configured)?.id || "auto";
  return { providers, defaultProvider };
};

export const providerLabel = (
  providerId: string | undefined,
  providers: AiProviderMetadata[],
) => providers.find((provider) => provider.id === providerId)?.label
  || providerId
  || "Unknown provider";

export const describeProviderResult = (
  result: AiProviderResult,
  providers: AiProviderMetadata[],
) => {
  const provider = providerLabel(result.provider, providers);
  const model = result.model || "Unknown model";
  const attempted = (result.attempted_providers || []).map(
    (providerId) => providerLabel(providerId, providers),
  );
  return {
    provider,
    model,
    failoverUsed: result.failover_used === true,
    attempted,
  };
};
