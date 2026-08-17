/**
 * The "App 预览" tab: the explanatory copy and the real MicroPythonOS
 * WebAssembly runtime iframe.
 *
 * Extracted from `App.tsx`. The iframe's onLoad handshake stays with the
 * caller, which owns the postMessage bridge and its timers.
 */

import type { RefObject } from "react";

import type { GenerationResult, Language } from "./types";
import type { ProviderResultSummary } from "./ProgressCard";

export interface PreviewPaneProps {
  language: Language;
  status: string;
  wasmReady: boolean;
  runtimeStatus: string;
  desktopScreenshotUrl: string | null;
  result: GenerationResult | null;
  providerResult: ProviderResultSummary | null;
  normalizedZh: string;
  normalizedEn: string;
  iframeRef: RefObject<HTMLIFrameElement>;
  wasmRuntimeUrl: string;
  onIframeLoad: () => void;
}

export function PreviewPane({
  language,
  status,
  wasmReady,
  runtimeStatus,
  desktopScreenshotUrl,
  result,
  providerResult,
  normalizedZh,
  normalizedEn,
  iframeRef,
  wasmRuntimeUrl,
  onIframeLoad,
}: PreviewPaneProps) {
  const tr = (zh: string, en: string) => language === "zh" ? zh : en;
  return (
    <div className="preview-pane">
      <div className="preview-copy">
        <h3>{tr("浏览器模拟屏幕", "Browser Simulator")}</h3>
        <p>{tr("右边不是假图片，里面运行的是实际的 MicroPythonOS WebAssembly；生成成功后可以直接点击 App。", "The device on the right runs real MicroPythonOS WebAssembly. You can interact with the app after generation.")}</p>
        <p className="preview-limit">{tr("Web 预览只是浏览器兼容性预览，不等于真机验证。摄像头、IMU、GPIO、串口、蓝牙、音频、SD 卡和实体按键必须上真机测试。", "Web preview is a browser compatibility preview, not hardware validation. Camera, IMU, GPIO, serial, Bluetooth, audio, SD card, and physical buttons require a real device.")}</p>
        <div className={`runtime-pill ${["failed", "timeout"].includes(status) ? "error" : wasmReady ? "ready" : ""}`}>
          <i />{runtimeStatus}
        </div>
        {desktopScreenshotUrl && <img className="desktop-screenshot" src={desktopScreenshotUrl} alt={tr("桌面测试截图", "Desktop smoke screenshot")} />}
        {result && <>
          <small className="preview-summary">{result.summary} · {providerResult?.provider} · {providerResult?.model}{providerResult?.failoverUsed ? tr(" · 已执行安全 failover", " · Safe failover used") : ""}</small>
          <small className="preview-summary">{tr(
            `AI 规范化需求：${normalizedZh}`,
            `Normalized requirement: ${normalizedEn}`,
          )}</small>
        </>}
      </div>
      <div className="device wasm-device">
        <div className="device-status"><span>10:24</span><span>● WiFi　87%</span></div>
        <iframe
          ref={iframeRef}
          title="MicroPythonOS WebAssembly Runtime"
          src={wasmRuntimeUrl}
          allow="clipboard-read; clipboard-write"
          onLoad={onIframeLoad}
        />
      </div>
    </div>
  );
}
