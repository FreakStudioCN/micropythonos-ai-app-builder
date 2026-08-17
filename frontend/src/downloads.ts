/**
 * The three ways this app hands a file to the user.
 *
 * Extracted from `App.tsx`. Each one reports its own outcome through `notify`
 * — success or failure — because a download button that quietly does nothing
 * is indistinguishable from a broken app.
 */

import type { Artifact, SaveFilePickerWindow } from "./types";

type Notify = (message: string) => void;
type Translate = (zh: string, en: string) => string;

const saveBlob = (blob: Blob, filename: string) => {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
};

export const downloadText = (
  filename: string,
  content: string,
  tr: Translate,
  notify: Notify,
) => {
  saveBlob(new Blob([content], { type: "application/octet-stream" }), filename);
  notify(tr(`已下载 ${filename}`, `Downloaded ${filename}`));
};

export const downloadArtifactFile = async (
  artifact: Artifact,
  fetcher: (url: string) => Promise<Response>,
  artifactUrl: string,
  tr: Translate,
  notify: Notify,
) => {
  try {
    const response = await fetcher(artifactUrl);
    if (!response.ok) throw new Error(tr("无权下载该产物", "Artifact download is unavailable"));
    saveBlob(await response.blob(), artifact.display_name);
    notify(tr(`已下载 ${artifact.display_name}`, `Downloaded ${artifact.display_name}`));
  } catch (error) {
    notify(error instanceof Error ? error.message : tr("下载失败", "Download failed"));
  }
};

export const downloadMpkFile = async (
  mpkBase64: string,
  filename: string,
  tr: Translate,
  notify: Notify,
) => {
  const binary = window.atob(mpkBase64);
  const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
  const blob = new Blob([bytes], { type: "application/zip" });
  const pickerWindow = window as SaveFilePickerWindow;
  if (pickerWindow.showSaveFilePicker) {
    try {
      const handle = await pickerWindow.showSaveFilePicker({
        suggestedName: filename,
        types: [{
          description: "MicroPythonOS package",
          accept: { "application/zip": [".mpk"] },
        }],
      });
      const writable = await handle.createWritable();
      await writable.write(blob);
      await writable.close();
    } catch (error) {
      // Cancelling is not a failure and needs no message.
      if (error instanceof DOMException && error.name === "AbortError") return;
      // Anything else used to be rethrown into an onClick with no catch, so a
      // failed save reached the user as nothing happening at all.
      notify(error instanceof Error ? error.message : tr("保存失败", "Save failed"));
      return;
    }
  } else {
    saveBlob(blob, filename);
  }
  notify(tr(`已下载真实 ${filename}`, `Downloaded ${filename}`));
};
