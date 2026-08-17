/**
 * Capability labels and the connected-device panel.
 *
 * These are read-outs, never selectors: the user describes an App, and this
 * reports what the request implies and what a connected device actually
 * confirmed. There is deliberately no board dropdown anywhere in this file.
 */

import {
  allowsAutoRepair,
  capabilityStatus,
  capabilityStatusText,
  needsExplicitConfirmation,
  type CapabilityAnalysis,
  type CapabilityContract,
  type CapabilityProbeOutcome,
} from "./capabilities";

type Language = "zh" | "en";

interface CapabilityTagsProps {
  analysis: CapabilityAnalysis;
  language: Language;
  probes?: CapabilityProbeOutcome[];
}

const probeFor = (probes: CapabilityProbeOutcome[] | undefined, capability: string) =>
  probes?.find((item) => item.capability === capability);

export function CapabilityTags({ analysis, language, probes }: CapabilityTagsProps) {
  const tr = (zh: string, en: string) => (language === "zh" ? zh : en);
  if (analysis.required_capabilities.length === 0) {
    return null;
  }
  return (
    <div className="capability-tags">
      <h3>{tr("这个 App 需要的硬件能力", "Hardware this App needs")}</h3>
      <p className="capability-hint">
        {tr(
          "不需要选择板子。能力是否真的存在，由连接的 MicroPythonOS 设备说了算。",
          "No board to pick. A connected MicroPythonOS device decides what actually exists.",
        )}
      </p>
      <ul>
        {analysis.capability_contracts.map((contract) => {
          const probe = probeFor(probes, contract.capability);
          const status = capabilityStatus(contract, probe);
          return (
            <li key={contract.capability} className={`capability-tag status-${status}`}>
              <strong>{contract.capability}</strong>
              <span>{capabilityStatusText(status, language)}</span>
              {contract.preferred_api && <small>{contract.preferred_api}</small>}
              {status === "waiting_os_api" && (
                <em>
                  {contract.reason}
                  {" · "}
                  {contract.blocking_error_code}
                </em>
              )}
              {probe?.detail && <em>{probe.detail}</em>}
              {analysis.runtime_fallbacks[contract.capability] && (
                <small className="capability-fallback">
                  {analysis.runtime_fallbacks[contract.capability]}
                </small>
              )}
            </li>
          );
        })}
      </ul>

      {analysis.required_accessories.length > 0 && (
        <div className="capability-accessories">
          <h4>{tr("外接配件（需要你确认接线）", "External accessories (wiring needs your confirmation)")}</h4>
          <ul>
            {analysis.required_accessories.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      )}

      {analysis.web_preview_unsupported.length > 0 && (
        <p className="capability-preview-note">
          {tr(
            "以下能力无法在网页预览中真实运行，预览里显示的是占位状态，这不是代码出错：",
            "These cannot really run in the browser preview. The placeholder shown there is not a code defect:",
          )}{" "}
          {analysis.web_preview_unsupported.join("、")}
        </p>
      )}

      {analysis.physical_validation_required && (
        <p className="capability-physical-note">
          {tr(
            "必须在真机上完成验证，一张截图不足以证明硬件功能通过。",
            "Real-device validation is required. One screenshot does not prove hardware works.",
          )}
        </p>
      )}
    </div>
  );
}

interface DeviceCapabilityPanelProps {
  analysis: CapabilityAnalysis;
  language: Language;
  micropythonosDetected: boolean | null;
  hardwareId: string;
  probes: CapabilityProbeOutcome[];
  warnings: string[];
  onProbe?: () => void;
  probing?: boolean;
}

export function DeviceCapabilityPanel({
  analysis,
  language,
  micropythonosDetected,
  hardwareId,
  probes,
  warnings,
  onProbe,
  probing,
}: DeviceCapabilityPanelProps) {
  const tr = (zh: string, en: string) => (language === "zh" ? zh : en);
  const confirmations = analysis.capability_contracts.filter(needsExplicitConfirmation);

  return (
    <div className="device-capability-panel">
      <h3>{tr("已连接设备", "Connected device")}</h3>
      <dl>
        <dt>{tr("检测到 MicroPythonOS", "MicroPythonOS detected")}</dt>
        <dd>
          {micropythonosDetected === null
            ? tr("尚未检测", "Not checked yet")
            : micropythonosDetected
              ? tr("是", "Yes")
              : tr("否", "No")}
          {micropythonosDetected === false && (
            <a href="https://install.micropythonos.com/" target="_blank" rel="noreferrer">
              {tr("前往安装", "Install it")}
            </a>
          )}
        </dd>
        <dt>{tr("硬件 ID（仅诊断）", "Hardware ID (diagnostics only)")}</dt>
        <dd>{hardwareId || tr("未读取", "Not read")}</dd>
      </dl>

      {onProbe && (
        <button type="button" onClick={onProbe} disabled={probing}>
          {probing
            ? tr("正在探测能力…", "Probing capabilities…")
            : tr("探测所需能力", "Probe required capabilities")}
        </button>
      )}

      {probes.length > 0 && (
        <ul className="capability-probe-results">
          {probes.map((probe) => {
            const contract = analysis.capability_contracts.find(
              (item: CapabilityContract) => item.capability === probe.capability,
            );
            const status = contract ? capabilityStatus(contract, probe) : "device_unknown";
            return (
              <li key={probe.capability} className={`status-${status}`}>
                <strong>{probe.capability}</strong>
                <span>{capabilityStatusText(status, language)}</span>
                {probe.probe && <code>{probe.probe}</code>}
                {probe.detail && <em>{probe.detail}</em>}
                {!allowsAutoRepair(status) && (
                  <small className="capability-no-repair">
                    {tr(
                      "这不是 App 代码的问题，不提供自动修复。",
                      "Not an App code defect — no automatic fix is offered.",
                    )}
                  </small>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {warnings.length > 0 && (
        <ul className="capability-warnings">
          {warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}

      {confirmations.length > 0 && (
        <div className="capability-confirmations">
          <h4>{tr("需要逐项明确确认的操作", "Actions needing separate explicit confirmation")}</h4>
          <ul>
            {confirmations.map((contract) => (
              <li key={contract.capability}>
                <strong>{contract.capability}</strong>
                {contract.destructive_operations.length > 0 && (
                  <span>
                    {tr("破坏性操作：", "Destructive: ")}
                    {contract.destructive_operations.join("、")}
                  </span>
                )}
                {contract.limitations.map((limitation) => (
                  <em key={limitation}>{limitation}</em>
                ))}
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="capability-advisory-note">
        {tr(
          "静态板卡表只作诊断参考。未收录的新板卡只要探测通过，就是合法设备。",
          "The static board table is advisory. An unlisted board that probes successfully is a valid device.",
        )}
      </p>
    </div>
  );
}
