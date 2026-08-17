/**
 * The generation-progress card: stage timeline, capability tags, and the
 * success / error / warning boxes.
 *
 * Extracted from `App.tsx`. Purely presentational — every decision it makes is
 * derived from the props, and every action is handed back to the caller.
 */

import { CapabilityTags } from "./CapabilityPanel";
import { stages } from "./stages";
import type { CapabilityAnalysis, CapabilityProbeOutcome } from "./capabilities";
import type { Language, SessionState, Status } from "./types";

export interface ProviderResultSummary {
  provider: string;
  model: string;
  failoverUsed: boolean;
  attempted: string[];
}

export interface ProgressCardProps {
  language: Language;
  status: Status;
  currentStage: number;
  sessionState: SessionState | null;
  capabilityAnalysis: CapabilityAnalysis | null;
  capabilityProbes: CapabilityProbeOutcome[];
  providerResult: ProviderResultSummary | null;
  errorMessage: string;
  onContinueEditing: () => void;
  onReviewPermissions: () => void;
  onKeepWaiting: () => void;
  onRetry: () => void;
}

export function ProgressCard({
  language,
  status,
  currentStage,
  sessionState,
  capabilityAnalysis,
  capabilityProbes,
  providerResult,
  errorMessage,
  onContinueEditing,
  onReviewPermissions,
  onKeepWaiting,
  onRetry,
}: ProgressCardProps) {
  const isZh = language === "zh";
  const tr = (zh: string, en: string) => isZh ? zh : en;
  return (
    <section className="card progress-card">
      <h2>{tr("生成进度", "Generation progress")}</h2>
      {capabilityAnalysis && (
        <CapabilityTags
          analysis={capabilityAnalysis}
          language={language}
          probes={capabilityProbes.length > 0 ? capabilityProbes : undefined}
        />
      )}
      {status === "idle" && currentStage < 0 && <div className="empty-progress"><b>1</b><span>{tr("输入你的想法", "Describe your idea")}</span><b>2</b><span>{tr("允许浏览器模拟运行", "Allow browser simulation")}</span><b>3</b><span>{tr("预览并下载 App", "Preview and download")}</span></div>}
      {(status !== "idle" || currentStage >= 0) && (
        <ol className="timeline">
          {stages.map(([english, chinese], index) => {
            const stageStatus = ["failed", "timeout", "cancelled"].includes(status) && index === currentStage ? "error" : index < currentStage || (status === "completed" && index === currentStage) ? "done" : index === currentStage ? "active" : "waiting";
            return <li className={stageStatus} key={english}><i>{stageStatus === "done" ? "✓" : stageStatus === "error" ? "!" : index + 1}</i><div><strong>{isZh ? chinese : english}</strong>{isZh && <small>{english}</small>}</div><span>{stageStatus === "done" ? tr("成功", "Done") : stageStatus === "active" ? tr("进行中", "Running") : stageStatus === "error" ? tr("失败", "Failed") : tr("等待", "Waiting")}</span></li>;
          })}
        </ol>
      )}
      {status === "completed" && <div className="success-box"><strong>{sessionState?.input.targets.includes("web-preview") ? tr("App 已在 MicroPythonOS WASM 中真实运行", "App is running in MicroPythonOS WASM") : tr("所选生成和打包阶段已完成", "Selected generation and packaging stages are complete")}</strong>{providerResult && <span>AI: {providerResult.provider} · {providerResult.model}{providerResult.failoverUsed ? tr(` · 已安全切换（${providerResult.attempted.join(" → ")}）`, ` · Safe failover (${providerResult.attempted.join(" → ")})`) : ""}</span>}<span>{tr(`当前版本 ${sessionState?.revision_id || "r1"}；可以继续描述修改，不会覆盖上一成功版本。`, `Current revision ${sessionState?.revision_id || "r1"}. Continue editing without overwriting the last successful revision.`)}</span><button onClick={onContinueEditing}>{tr("继续修改这个 App", "Continue editing this app")}</button></div>}
      {["failed", "timeout", "cancelled", "blocked"].includes(status) && <div className={`error-box state-${status}`}>
        <strong>{status === "timeout" ? tr("运行超时", "Timed out") : status === "cancelled" ? tr("任务已取消", "Cancelled") : status === "blocked" ? tr("等待处理", "Blocked") : tr("真实生成失败", "Generation failed")}</strong>
        {sessionState?.last_error && <code>{sessionState.last_error.code} · {sessionState.last_error.stage} · owner: {sessionState.last_error.owner}</code>}
        <span>{errorMessage}</span>
        <div>
          {status === "blocked" && sessionState?.permissions.some((item) => item.required && item.decision === "pending") && <button onClick={onReviewPermissions}>{tr("处理权限", "Review permissions")}</button>}
          {status === "timeout" && sessionState?.status !== "timeout" && <button onClick={onKeepWaiting}>{tr("继续等待后台结果", "Keep waiting for backend")}</button>}
          {(status !== "timeout" || sessionState?.status === "timeout") && <button onClick={onRetry}>{tr("从失败检查点重试", "Retry from checkpoint")}</button>}
        </div>
      </div>}
      {sessionState?.warnings.length ? <div className="warning-box"><strong>{tr("警告（不等于失败）", "Warnings (not failures)")}</strong>{sessionState.warnings.map((warning) => <span key={warning}>⚠ {warning}</span>)}</div> : null}
    </section>
  );
}
