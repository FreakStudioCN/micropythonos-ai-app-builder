/**
 * The per-session permission review modal.
 *
 * Extracted from `App.tsx`. Presentational: it renders the required
 * permissions and hands every decision back to the caller.
 */

import type { Language, Permission } from "./types";

export interface PermissionModalProps {
  language: Language;
  permissions: Permission[];
  /** permission_id of the decision in flight, "__all__" for approve-all. */
  permissionBusy: string;
  onDecide: (permission: Permission, decision: "allow_once" | "deny") => void;
  onAllowAll: () => void;
  onClose: () => void;
}

export function PermissionModal({
  language,
  permissions,
  permissionBusy,
  onDecide,
  onAllowAll,
  onClose,
}: PermissionModalProps) {
  const tr = (zh: string, en: string) => language === "zh" ? zh : en;
  return (
    <div className="modal-backdrop"><div className="modal permission-host">
      <h2>{tr("确认操作权限", "Review permissions")}</h2>
      <p>{tr("你可以逐项决定，也可以在下方一键允许全部必需权限。所有授权只对本次会话生效。", "Review permissions individually or allow all required permissions below. Approvals apply only to this session.")}</p>
      <div className="permission-list">
        {permissions.filter((item) => item.required).map((permission) => (
          <article className={`permission-card risk-${permission.risk} decision-${permission.decision}`} key={permission.permission_id}>
            <header><strong>{permission.title}</strong><span>{permission.risk}</span></header>
            <p>{permission.description}</p>
            <code>{permission.command_preview}</code>
            <small>{permission.permission_type} · {permission.permission_id}</small>
            {permission.decision === "pending"
              ? <div><button disabled={Boolean(permissionBusy)} className="secondary-button" onClick={() => onDecide(permission, "deny")}>{tr("拒绝", "Deny")}</button><button disabled={Boolean(permissionBusy)} className="main-button" onClick={() => onDecide(permission, "allow_once")}>{permissionBusy === permission.permission_id ? tr("保存中…", "Saving…") : tr("仅允许一次", "Allow once")}</button></div>
              : <b>{permission.decision === "allow_once" ? tr("✓ 已允许一次", "✓ Allowed once") : tr("✕ 已拒绝", "✕ Denied")}</b>}
          </article>
        ))}
      </div>
      <small>{tr("API Key 只保存在 backend/.env。模型不能发送任意 shell，也不能绕过这些权限。", "The API key stays in backend/.env. The model cannot send arbitrary shell commands or bypass these permissions.")}</small>
      <div>
        <button className="secondary-button" onClick={onClose}>{tr("稍后处理", "Later")}</button>
        <button
          className="main-button"
          disabled={Boolean(permissionBusy) || permissions.some((item) => item.required && item.decision === "deny")}
          onClick={onAllowAll}
        >{permissionBusy === "__all__" ? tr("正在一键确认…", "Approving…") : tr("一键确认并开始运行", "Approve all and run")}</button>
      </div>
    </div></div>
  );
}
