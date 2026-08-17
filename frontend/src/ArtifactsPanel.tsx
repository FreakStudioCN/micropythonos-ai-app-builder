/**
 * The "生成产物" tab: generated files, the .mpk, and the publishing checklist.
 *
 * Extracted from `App.tsx`. Presentational — it lists what the session
 * produced and hands every download or upload back to the caller.
 */

import type { Artifact, GeneratedFile, GenerationResult, Language } from "./types";

export interface ArtifactsPanelProps {
  language: Language;
  result: GenerationResult | null;
  artifacts: Artifact[];
  screenshotBusy: boolean;
  onDownloadArtifact: (artifact: Artifact) => void;
  onDownloadFile: (file: GeneratedFile) => void;
  onDownloadMpk: () => void;
  onUploadScreenshot: (file: File) => void;
}

export function ArtifactsPanel({
  language,
  result,
  artifacts,
  screenshotBusy,
  onDownloadArtifact,
  onDownloadFile,
  onDownloadMpk,
  onUploadScreenshot,
}: ArtifactsPanelProps) {
  const tr = (zh: string, en: string) => language === "zh" ? zh : en;
  if (!result) {
    return <div className="not-ready">{tr("真实生成成功后，这里会出现 AI 生成的源码和 `.mpk`。", "AI-generated source files and the `.mpk` will appear here after generation.")}</div>;
  }
  return (
    <div className="artifacts">
      {artifacts.length
        ? <ul>{artifacts.map((artifact) => <li key={artifact.id}><span>▣　{artifact.path}<small>{artifact.role} · {artifact.kind} · {Math.ceil(artifact.size / 1024)} KB</small><small>{artifact.mime} · {artifact.phase}</small><code title={artifact.sha256}>sha256: {artifact.sha256.slice(0, 16)}…</code></span><button onClick={() => onDownloadArtifact(artifact)}>{tr("下载", "Download")}</button></li>)}</ul>
        : <ul>{result.files.map((file) => <li key={file.path}><span>▣　{file.path}</span><button onClick={() => onDownloadFile(file)}>{tr("下载", "Download")}</button></li>)}</ul>}
      <div className="mpk"><div><strong>{result.mpk_filename}</strong><small>{tr("包含真实 MANIFEST.JSON 和 assets/main.py，文件名符合 _rN 发布规则", "Contains MANIFEST.JSON and assets/main.py with the required _rN release name")}</small></div><button onClick={onDownloadMpk}>{tr("下载真实 .mpk", "Download .mpk")}</button></div>
      <div className="publish-guide">
        <strong>{tr("uPyStore 发布检查", "uPyStore checklist")}</strong>
        <span>{tr("✓ Manifest　✓ _rN.mpk　△ Web/真机验证　△ PNG/JPEG/WebP 截图。发布材料 ZIP 已进入上方产物列表；这里只提供手工上传引导。", "✓ Manifest  ✓ _rN.mpk  △ Web/device validation  △ PNG/JPEG/WebP screenshot. The publishing ZIP is listed above; upload remains manual.")}</span>
        <label className="secondary-button">
          {screenshotBusy ? tr("正在上传截图…", "Uploading screenshot…") : tr("添加发布截图", "Add publishing screenshot")}
          <input
            type="file"
            accept="image/png,image/jpeg,image/webp"
            disabled={screenshotBusy}
            hidden
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.currentTarget.value = "";
              if (file) onUploadScreenshot(file);
            }}
          />
        </label>
        <a href="https://upystore.io/developer" target="_blank" rel="noreferrer">{tr("打开 uPyStore 开发者入口", "Open uPyStore Developer")}</a>
      </div>
    </div>
  );
}
