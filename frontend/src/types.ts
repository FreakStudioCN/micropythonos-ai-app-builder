/**
 * Shared session, artifact and account types for the App builder UI.
 *
 * Extracted from `App.tsx` so the component file is about rendering.
 */

import type { CapabilityAnalysis, CapabilityProbeOutcome } from "./capabilities";
import { isValidShowcaseSha256 } from "./platformUpgradeLibrary";

export type Status = "idle" | "created" | "running" | "waiting_preview" | "waiting_device" | "completed" | "failed" | "blocked" | "cancelled" | "timeout";
export type Language = "zh" | "en";
export type AuthStatus = "loading" | "signed_out" | "signed_in";
export type AccountRole = "user" | "superadmin";
export interface GeneratedFile {
  path: string;
  content: string;
}
export interface GenerationResult {
  package_name: string;
  summary: string;
  manifest: Record<string, unknown>;
  files: GeneratedFile[];
  mpk_base64: string;
  model: string;
  warnings: string[];
  acceptance_tests: string[];
  mpk_filename: string;
  revision: number;
  provider?: string;
  failover_used?: boolean;
  attempted_providers?: string[];
  prompt_normalized_zh?: string;
  prompt_normalized_en?: string;
  store_metadata?: Record<string, string>;
}
export interface Artifact {
  id: string;
  role: string;
  path: string;
  mime: string;
  size: number;
  display_name: string;
  phase: string;
  sha256: string;
  kind: string;
}
export interface Permission {
  permission_id: string;
  permission_type: string;
  title: string;
  description: string;
  risk: "low" | "medium" | "high";
  command_preview: string;
  required: boolean;
  decision: "pending" | "allow_once" | "deny";
}
export interface StructuredError {
  code: string;
  message: string;
  stage: string;
  owner: string;
  retryable: boolean;
}
export interface SessionState {
  session_id: string;
  revision_id: string;
  status: "blocked" | "created" | "running" | "waiting_preview" | "waiting_device" | "completed" | "failed" | "cancelled" | "timeout";
  checkpoint_id: string;
  current_phase: string;
  permissions: Permission[];
  artifacts: Artifact[];
  warnings: string[];
  last_error: StructuredError | null;
  generation: GenerationResult | null;
  input: {
    prompt_original: string;
    package_name: string;
    display_name: string;
    publisher: string;
    version: string;
    ai_provider?: string;
    targets: string[];
    prompt_normalized_zh?: string;
    prompt_normalized_en?: string;
    required_capabilities?: string[];
    required_accessories?: string[];
    runtime_fallbacks?: Record<string, string>;
    physical_validation_required?: boolean;
  };
  capability_analysis?: CapabilityAnalysis;
  capability_versions?: Record<string, string>;
  detected_hardware_id?: string;
  runtime_capability_results?: CapabilityProbeOutcome[];
}
export type SessionSummary = Omit<SessionState, "generation"> & { generation?: GenerationResult | null };
export interface BillingAccount {
  user_id: string;
  username: string;
  role: AccountRole;
  credits: number;
  unlimited_credits: boolean;
  generations_remaining: number;
  generation_limit: number;
  generation_cost: number;
  initial_credits: number;
}
export interface RequirementMessage {
  role: "user" | "assistant";
  content: string;
}
export interface RequirementChatResult {
  assistant_message: string;
  ready: boolean;
  refined_prompt: string;
  missing_fields: string[];
  brief: Record<string, unknown>;
  model: string;
}
export interface SaveFileHandle {
  createWritable(): Promise<{
    write(data: Blob): Promise<void>;
    close(): Promise<void>;
  }>;
}
export type SaveFilePickerWindow = Window & {
  showSaveFilePicker?: (options: {
    suggestedName: string;
    types: Array<{
      description: string;
      accept: Record<string, string[]>;
    }>;
  }) => Promise<SaveFileHandle>;
};
export interface ShowcaseApp {
  fullname: string;
  name: string;
  category: string;
  version: string;
  shortDescription: string;
  longDescription: string;
  screenshotUrl: string;
  mpkUrl: string;
  sha256: string;
  featured: boolean;
}

/** Runtime guard for store payloads, which arrive as untrusted JSON. */
export const isShowcaseApp = (value: unknown): value is ShowcaseApp => {
  if (!value || typeof value !== "object") return false;
  const item = value as Record<string, unknown>;
  return [
    "fullname",
    "name",
    "category",
    "version",
    "shortDescription",
    "longDescription",
    "screenshotUrl",
    "mpkUrl",
  ].every((key) => typeof item[key] === "string")
    && isValidShowcaseSha256(item.sha256)
    && typeof item.featured === "boolean";
};
