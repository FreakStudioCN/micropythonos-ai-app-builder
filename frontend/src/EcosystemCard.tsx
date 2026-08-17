/**
 * The hardware-ecosystem card: the verified-board reference table.
 *
 * Extracted from `App.tsx` with `boardText`, its only consumer. Presentational
 * and stateless — it reads the board table and the UI language, nothing else.
 */

import { verifiedBoards } from "./verifiedBoards";

export function EcosystemCard({ isZh }: { isZh: boolean }) {
  const tr = (zh: string, en: string) => isZh ? zh : en;
const boardText = (value: string) => {
  if (isZh) return value;
  const translations: Record<string, string> = {
    "触摸屏": "touch display",
    "徽章屏幕": "badge display",
    "大屏": "large display",
    "彩色小屏": "compact color display",
    "腕上触摸屏": "watch touch display",
    "2.8\" 触摸屏": "2.8\" touch display",
    "游戏屏幕": "game display",
    "彩色屏": "color display",
    "2\" 触摸屏": "2\" touch display",
    "入门交互": "beginner interaction",
    "活动徽章": "event badge",
    "活动作品": "event projects",
    "信息面板": "information panels",
    "便携工具": "portable tools",
    "人机界面": "HMI projects",
    "穿戴应用": "wearables",
    "新手创作": "beginner making",
    "传感器项目": "sensor projects",
    "视觉项目": "vision projects",
    "掌机应用": "handheld games",
    "桌面信息": "desktop information",
    "STEM 课堂": "STEM classrooms",
    "移动创作": "mobile making",
    "新手与展示": "beginners and demos",
  };
  return translations[value] || value;
};
  return (
      <section className="card ecosystem-card" id="devices">
        <div className="section-heading">
          <div><span>{tr("真实适配能力", "Verified targets")}</span><h2>{tr("硬件生态与运行目标", "Hardware Ecosystem")}</h2></div>
          <p>{tr("15 款物理板卡 + Linux/macOS 桌面目标 + WebAssembly Web 目标。", "15 physical boards, Linux/macOS desktop, and WebAssembly Web targets.")}</p>
        </div>
        <div className="target-strip">
          <article><b>15</b><span>{tr("真实适配板卡", "verified boards")}</span></article>
          <article><b>Web</b><span>WebAssembly</span></article>
          <article><b>Desktop</b><span>Linux / macOS</span></article>
        </div>
        <details className="board-details">
          <summary>{tr("查看全部 15 款真实适配板卡", "View all 15 verified boards")}</summary>
          <div className="board-grid">
            {verifiedBoards.map(([brand, model, platform, screen, use]) => (
              <article key={`${brand}-${model}`}>
                <div><span className="verified-dot" />{tr("已真实适配", "Verified")}</div>
                <strong>{brand}</strong>
                <h3>{model}</h3>
                <p>{platform} · {boardText(screen)}</p>
                <small>{tr("推荐：", "Best for: ")}{boardText(use)}</small>
              </article>
            ))}
          </div>
        </details>
        <div className="planned-note">
          <strong>Tuya / 涂鸦智能</strong>
          <span>{tr("3 款带屏板卡为重点适配方向；当前仅作概念演示，并非真实适配。", "Three display boards are a priority direction. Concept demo only; not currently verified.")}</span>
          <b>{tr("规划适配", "Planned")}</b>
        </div>
      </section>
  );
}
