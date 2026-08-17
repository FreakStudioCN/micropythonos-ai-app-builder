/**
 * Pipeline stage list and the mapping from session state to a stage index.
 *
 * Extracted from `App.tsx`; re-exported there so existing imports keep working.
 */

export const stages = [
  ["analysis", "需求分析"],
  ["api_check", "MicroPythonOS / LVGL API 校验"],
  ["generation", "AI 生成代码"],
  ["test", "桌面 / Web 预览测试"],
  ["package", "生成真实 MPK"],
  ["deploy", "真实设备部署"],
  ["publish", "发布准备检查"],
] as const;
export const stageIndexForError = (stage?: string) => {
  const indexByStage: Record<string, number> = {
    analysis: 0,
    api: 1,
    api_check: 1,
    generation: 2,
    test: 3,
    package: 4,
    deploy: 5,
    publish: 6,
  };
  return stage ? (indexByStage[stage] ?? 2) : 2;
};
export const stageIndexForCheckpoint = (checkpoint?: string) => {
  const indexByCheckpoint: Record<string, number> = {
    session_created: 0,
    requirements_analyzed: 1,
    api_checked: 2,
    code_generated: 3,
    desktop_test_done: 4,
    web_preview_done: 4,
    // A partial preview still finished the test stage; it only means a physical
    // capability could not be exercised in the browser.
    web_preview_partial: 4,
    package_done: 5,
    device_deploy_done: 6,
    publish_check_done: 6,
    completed: 6,
  };
  return checkpoint ? (indexByCheckpoint[checkpoint] ?? 0) : 0;
};
interface StageSessionSnapshot {
  status: string;
  checkpoint_id?: string;
  last_error?: { stage: string } | null;
  generation?: unknown;
}
export const stageIndexForSession = (session: StageSessionSnapshot) => {
  if (session.last_error) return stageIndexForError(session.last_error.stage);
  if (session.status === "waiting_device") return stageIndexForError("deploy");
  if (session.status === "waiting_preview") return stageIndexForError("test");
  if (session.status === "completed") return stages.length - 1;
  const checkpointStage = stageIndexForCheckpoint(session.checkpoint_id);
  if (checkpointStage > 0) return checkpointStage;
  return session.generation ? stageIndexForError("test") : 0;
};
