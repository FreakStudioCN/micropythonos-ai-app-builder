/**
 * The two screens shown before the builder itself: maintenance and sign-in.
 *
 * Extracted from `App.tsx`, where they were two early returns sitting ahead of
 * ~600 lines of unrelated JSX. Presentational: every value and every action
 * comes from props.
 */

import type { FormEvent } from "react";

import type { AuthStatus, Language } from "./types";
import type { PublicSystemStatus } from "./platformUpgradeLibrary";

const translate = (language: Language) => (zh: string, en: string) =>
  language === "zh" ? zh : en;

export function MaintenanceScreen({
  language,
  systemStatus,
}: {
  language: Language;
  systemStatus: PublicSystemStatus;
}) {
  const tr = translate(language);
  return (
    <div className="auth-page">
      <section className="auth-card">
        <div className="auth-brand"><span>BM</span><div><strong>Blockless-Make-APP</strong><small>MicroPythonOS AI Builder</small></div></div>
        <div className="auth-heading">
          <span>{tr("维护模式", "MAINTENANCE")}</span>
          <h1>{tr("系统正在升级", "System upgrade in progress")}</h1>
          <p>{systemStatus.message || tr(
            "生成和设备部署已暂时关闭。页面会自动检查，服务恢复后无需重新登录。",
            "Generation and device deployment are temporarily unavailable. This page checks automatically and restores access without another sign-in.",
          )}</p>
        </div>
        <div className="auth-loading">{tr(
          `约 ${systemStatus.retry_after_seconds} 秒后再次检查…`,
          `Checking again in about ${systemStatus.retry_after_seconds} seconds…`,
        )}</div>
      </section>
    </div>
  );
}

export interface AuthScreenProps {
  language: Language;
  authStatus: AuthStatus;
  authMode: "login" | "register";
  authUsername: string;
  authPassword: string;
  authError: string;
  authBusy: boolean;
  onLanguageToggle: () => void;
  onModeToggle: () => void;
  onUsernameChange: (value: string) => void;
  onPasswordChange: (value: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}

export function AuthScreen({
  language,
  authStatus,
  authMode,
  authUsername,
  authPassword,
  authError,
  authBusy,
  onLanguageToggle,
  onModeToggle,
  onUsernameChange,
  onPasswordChange,
  onSubmit,
}: AuthScreenProps) {
  const tr = translate(language);
  const isZh = language === "zh";
  return (
    <div className="auth-page">
      <button
        className="language-button auth-language"
        onClick={onLanguageToggle}
      >{isZh ? "English" : "中文"}</button>
      <section className="auth-card">
        <div className="auth-brand"><span>BM</span><div><strong>Blockless-Make-APP</strong><small>MicroPythonOS AI Builder</small></div></div>
        {authStatus === "loading" ? (
          <div className="auth-loading">{tr("正在连接内测服务…", "Connecting to the beta service…")}</div>
        ) : (
          <>
            <div className="auth-heading">
              <span>{tr("正式内测", "PRIVATE BETA")}</span>
              <h1>{authMode === "login" ? tr("欢迎回来", "Welcome back") : tr("创建内测账号", "Create your beta account")}</h1>
              <p>{authMode === "login"
                ? tr("登录后继续查看自己的项目和剩余点数。", "Sign in to restore your projects and credits.")
                : tr("每个账号获得 50 个免费内测点数，可生成约 5 个版本。", "Each account receives 50 beta credits, enough for about 5 revisions.")}</p>
            </div>
            <form className="auth-form" onSubmit={onSubmit}>
              <label htmlFor="auth-username">{tr("用户名", "Username")}</label>
              <input
                id="auth-username"
                value={authUsername}
                onChange={(event) => onUsernameChange(event.target.value)}
                minLength={3}
                maxLength={32}
                autoComplete="username"
                required
                autoFocus
              />
              <label htmlFor="auth-password">{tr("密码", "Password")}</label>
              <input
                id="auth-password"
                type="password"
                value={authPassword}
                onChange={(event) => onPasswordChange(event.target.value)}
                minLength={8}
                maxLength={128}
                autoComplete={authMode === "login" ? "current-password" : "new-password"}
                required
              />
              {authError && <div className="auth-error">{authError}</div>}
              <button className="main-button auth-submit" type="submit" disabled={authBusy}>
                {authBusy
                  ? tr("请稍候…", "Please wait…")
                  : authMode === "login" ? tr("登录", "Sign in") : tr("注册并进入", "Create account")}
              </button>
            </form>
            <button
              className="auth-switch"
              onClick={onModeToggle}
            >{authMode === "login"
              ? tr("没有账号？免费注册", "No account? Register free")
              : tr("已经有账号？返回登录", "Already registered? Sign in")}</button>
            <small className="auth-notice">{tr(
              "当前版本不收费、不充值、不自动订阅。密码只以安全哈希保存在后端数据库。",
              "No payments, top-ups, or automatic subscriptions. Passwords are stored only as secure hashes.",
            )}</small>
          </>
        )}
      </section>
    </div>
  );
}
