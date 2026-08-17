/**
 * The requirement-clarification chat modal.
 *
 * Extracted from `App.tsx` alongside the permission modal. Presentational: the
 * conversation and its pending state come in as props, every action goes back
 * out as a callback.
 */

import type { Language, RequirementChatResult, RequirementMessage } from "./types";

export interface RequirementModalProps {
  language: Language;
  messages: RequirementMessage[];
  input: string;
  busy: boolean;
  error: string;
  result: RequirementChatResult | null;
  onInputChange: (value: string) => void;
  onSend: () => void;
  onFinish: () => void;
  onApply: () => void;
  onClose: () => void;
}

export function RequirementModal({
  language,
  messages,
  input,
  busy,
  error,
  result,
  onInputChange,
  onSend,
  onFinish,
  onApply,
  onClose,
}: RequirementModalProps) {
  const tr = (zh: string, en: string) => language === "zh" ? zh : en;
  return (
    <div className="modal-backdrop"><div className="modal requirement-modal">
      <section className="requirement-modal-title">
        <div><span>AI</span><div><h2>{tr("需求刻画助手", "Requirement assistant")}</h2><p>{tr("先把想法聊清楚，再交给生成器", "Clarify the idea before sending it to the generator")}</p></div></div>
        <button aria-label={tr("关闭", "Close")} onClick={onClose}>×</button>
      </section>
      <div className="requirement-chat">
        {messages.map((message, index) => (
          <article className={message.role} key={`${message.role}-${index}`}>
            <b>{message.role === "user" ? tr("你", "You") : tr("AI 助手", "AI assistant")}</b>
            <p>{message.content}</p>
          </article>
        ))}
        {busy && <article className="assistant thinking"><b>{tr("AI 助手", "AI assistant")}</b><p>{tr("正在理解你的想法…", "Understanding your idea…")}</p></article>}
      </div>
      {error && <div className="requirement-chat-error">{error}</div>}
      {result?.ready && result.refined_prompt
        ? <div className="requirement-ready">
            <strong>✓ {tr("需求已经整理好", "Requirement ready")}</strong>
            <p>{result.refined_prompt}</p>
          </div>
        : <div className="requirement-compose">
            <textarea
              value={input}
              disabled={busy}
              placeholder={tr("回答这个问题，也可以直接说“按你的建议”", "Answer the question, or say “use your recommendation”")}
              onChange={(event) => onInputChange(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  onSend();
                }
              }}
            />
            <button className="main-button" disabled={!input.trim() || busy} onClick={onSend}>{tr("发送", "Send")}</button>
          </div>}
      <div className="requirement-actions">
        <button className="secondary-button" disabled={busy} onClick={onClose}>{tr("稍后再说", "Later")}</button>
        {!result?.ready && <button className="secondary-button" disabled={busy} onClick={onFinish}>{tr("现在整理成完整需求", "Finish requirement now")}</button>}
        {result?.ready && <button className="main-button" onClick={onApply}>{tr("采用这份需求", "Use this requirement")}</button>}
      </div>
      <small className="requirement-note">{tr("这里只整理产品需求，不会生成代码或扣除生成点数。", "This step only refines requirements. It does not generate code or consume generation credits.")}</small>
    </div></div>
  );
}
