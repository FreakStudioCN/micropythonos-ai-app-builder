import { useEffect, useRef, useState } from "react";

type Status = "idle" | "created" | "running" | "waiting_preview" | "completed" | "failed" | "blocked" | "cancelled" | "timeout";
type Language = "zh" | "en";
interface GeneratedFile {
  path: string;
  content: string;
}
interface GenerationResult {
  package_name: string;
  summary: string;
  manifest: Record<string, unknown>;
  files: GeneratedFile[];
  mpk_base64: string;
  model: string;
  warnings: string[];
  acceptance_tests: string[];
  mpk_filename: string;
  revision: number;
}
interface Artifact {
  id: string;
  role: string;
  path: string;
  mime: string;
  size: number;
  display_name: string;
  phase: string;
  sha256: string;
  kind: string;
}
interface Permission {
  permission_id: string;
  permission_type: string;
  title: string;
  description: string;
  risk: "low" | "medium" | "high";
  command_preview: string;
  required: boolean;
  decision: "pending" | "allow_once" | "deny";
}
interface StructuredError {
  code: string;
  message: string;
  stage: string;
  owner: string;
  retryable: boolean;
}
interface SessionState {
  session_id: string;
  revision_id: string;
  status: "blocked" | "created" | "running" | "waiting_preview" | "completed" | "failed" | "cancelled" | "timeout";
  checkpoint_id: string;
  current_phase: string;
  permissions: Permission[];
  artifacts: Artifact[];
  warnings: string[];
  last_error: StructuredError | null;
  generation: GenerationResult | null;
  input: {
    prompt_original: string;
    package_name: string;
    display_name: string;
    publisher: string;
    version: string;
    targets: string[];
  };
}
type SessionSummary = Omit<SessionState, "generation"> & { generation?: GenerationResult | null };

const defaultPrompt = "做一个极简四则运算计算器，按钮要大，适合触摸屏";
const defaultPromptEn = "Build a minimal four-function calculator with large touch-friendly buttons";
const wasmRuntimeUrl = "http://127.0.0.1:8000/mpos-web/index.html?embed=1&bridge=2";
const apiUrl = "http://localhost:8000";
const stages = [
  ["analysis", "需求分析"],
  ["generation", "DeepSeek 生成代码"],
  ["test", "静态检查 / WASM 测试"],
  ["package", "生成真实 MPK"],
  ["publish", "发布准备检查"],
] as const;

export default function App() {
  const [language, setLanguage] = useState<Language>(() => localStorage.getItem("mpos-language") === "en" ? "en" : "zh");
  const isZh = language === "zh";
  const tr = (zh: string, en: string) => isZh ? zh : en;
  const [prompt, setPrompt] = useState(defaultPrompt);
  const [packageName, setPackageName] = useState("com.example.myapp");
  const [displayName, setDisplayName] = useState("我的 App");
  const [publisher, setPublisher] = useState("erkou111");
  const [version, setVersion] = useState("0.1.0");
  const [desktopTarget, setDesktopTarget] = useState(false);
  const [webTarget, setWebTarget] = useState(true);
  const [physicalTarget, setPhysicalTarget] = useState(false);
  const [packageTarget, setPackageTarget] = useState(true);
  const [status, setStatus] = useState<Status>("idle");
  const [currentStage, setCurrentStage] = useState(-1);
  const [permissionOpen, setPermissionOpen] = useState(false);
  const [permissionBusy, setPermissionBusy] = useState("");
  const [activeTab, setActiveTab] = useState<"preview" | "logs" | "artifacts">("preview");
  const [logs, setLogs] = useState<string[]>([]);
  const [toast, setToast] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [result, setResult] = useState<GenerationResult | null>(null);
  const [sessionState, setSessionState] = useState<SessionState | null>(null);
  const [history, setHistory] = useState<SessionSummary[]>([]);
  const [deviceMessage, setDeviceMessage] = useState("");
  const [continuing, setContinuing] = useState(false);
  const [wasmReady, setWasmReady] = useState(false);
  const [runtimeStatus, setRuntimeStatus] = useState("正在启动 MicroPythonOS WASM…");
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const lastRun = useRef("");
  const requestAbort = useRef<AbortController | null>(null);
  const eventStream = useRef<EventSource | null>(null);
  const executionTimer = useRef<number | null>(null);
  const wasmTimer = useRef<number | null>(null);
  const resultRef = useRef<GenerationResult | null>(null);
  const repairAttempts = useRef(0);
  const repairing = useRef(false);
  const repairHandler = useRef<(detail: string) => boolean>(() => false);
  const languageRef = useRef<Language>(language);
  languageRef.current = language;
  const liveText = (zh: string, en: string) => languageRef.current === "zh" ? zh : en;

  const clearRuntimeTimers = () => {
    if (executionTimer.current !== null) window.clearTimeout(executionTimer.current);
    if (wasmTimer.current !== null) window.clearTimeout(wasmTimer.current);
    executionTimer.current = null;
    wasmTimer.current = null;
  };

  useEffect(() => () => {
    requestAbort.current?.abort();
    eventStream.current?.close();
    clearRuntimeTimers();
  }, []);
  useEffect(() => {
    localStorage.setItem("mpos-language", language);
  }, [language]);
  useEffect(() => {
    if (status === "idle" && !wasmReady) {
      setRuntimeStatus(tr("正在启动 MicroPythonOS WASM…", "Starting MicroPythonOS WASM…"));
    }
  }, [language, status, wasmReady]);
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(""), 2200);
    return () => window.clearTimeout(timer);
  }, [toast]);
  const refreshHistory = () => {
    void fetch(`${apiUrl}/api/sessions`)
      .then((response) => response.ok ? response.json() as Promise<SessionSummary[]> : [])
      .then(setHistory)
      .catch(() => setHistory([]));
  };
  useEffect(() => {
    refreshHistory();
  }, []);

  const applySession = (session: SessionState) => {
    localStorage.setItem("mpos-session-id", session.session_id);
    setSessionState(session);
    setPrompt(session.input.prompt_original);
    setPackageName(session.input.package_name);
    setDisplayName(session.input.display_name);
    setPublisher(session.input.publisher);
    setVersion(session.input.version);
    setDesktopTarget(session.input.targets.includes("desktop-preview"));
    setWebTarget(session.input.targets.includes("web-preview"));
    setPhysicalTarget(session.input.targets.includes("physical-device"));
    setPackageTarget(session.input.targets.includes("package-only"));
    if (session.generation) {
      setResult(session.generation);
      resultRef.current = session.generation;
    }
    setStatus(session.status);
    setCurrentStage(session.status === "completed" ? stages.length - 1 : session.generation ? 3 : -1);
    setErrorMessage(session.last_error?.message || "");
  };
  const restoreSession = async (sessionId: string) => {
    const response = await fetch(`${apiUrl}/api/sessions/${sessionId}`);
    if (!response.ok) {
      setToast(tr("会话恢复失败", "Could not restore session"));
      return;
    }
    const session = await response.json() as SessionState;
    applySession(session);
    setLogs((items) => [...items, tr(`[resume] 已打开 ${session.session_id} ${session.revision_id}`, `[resume] Opened ${session.session_id} ${session.revision_id}`)]);
    setToast(tr("历史会话已恢复", "Session restored"));
  };
  useEffect(() => {
    const savedSession = localStorage.getItem("mpos-session-id");
    if (!savedSession) return;
    void fetch(`${apiUrl}/api/sessions/${savedSession}`)
      .then(async (response) => {
        if (!response.ok) throw new Error("session unavailable");
        return response.json() as Promise<SessionState>;
      })
      .then((session) => {
        applySession(session);
        setLogs((items) => [...items, liveText(`[resume] 已恢复会话 ${session.session_id}（${session.checkpoint_id}）`, `[resume] Restored ${session.session_id} at ${session.checkpoint_id}`)]);
      })
      .catch(() => localStorage.removeItem("mpos-session-id"));
  }, []);

  const openEventStream = (sessionId: string) => {
    eventStream.current?.close();
    const stream = new EventSource(`${apiUrl}/api/sessions/${sessionId}/events`);
    stream.onmessage = (event) => {
      const item = JSON.parse(event.data) as { type: string; phase: string; payload: { message?: string; status?: string } };
      const message = item.payload.message || item.payload.status || item.type;
      setLogs((entries) => [...entries, `[${item.phase}] ${message}`]);
    };
    for (const eventName of ["start_phase", "status_update", "phase_complete", "structured_error"]) {
      stream.addEventListener(eventName, (event) => {
        const item = JSON.parse((event as MessageEvent).data) as { type: string; phase: string; payload: { message?: string; status?: string; result?: string } };
        const message = item.payload.message || item.payload.status || item.payload.result || item.type;
        setLogs((entries) => [...entries, `[${item.phase}] ${message}`]);
      });
    }
    eventStream.current = stream;
  };

  useEffect(() => {
    const receive = (event: MessageEvent) => {
      const message = event.data as { source?: string; type?: string; text?: string; message?: string };
      if (message?.source !== "mpos-web") return;
      if (message.type === "MPOS_READY") {
        if (wasmTimer.current !== null) window.clearTimeout(wasmTimer.current);
        wasmTimer.current = null;
        setWasmReady(true);
        setRuntimeStatus(liveText("MicroPythonOS WASM 已就绪", "MicroPythonOS WASM is ready"));
      } else if (message.type === "MPOS_INSTALLING") {
        setRuntimeStatus(liveText("正在把生成代码安装进 MicroPythonOS…", "Installing generated code into MicroPythonOS…"));
        setLogs((items) => [...items, liveText("[preview] 正在通过 raw REPL 安装 app.py…", "[preview] Installing app.py through raw REPL…")]);
      } else if (message.type === "MPOS_LOG" && message.text?.trim()) {
        setLogs((items) => [...items, `[wasm] ${message.text!.trim()}`]);
      } else if (message.type === "MPOS_APP_RUNNING") {
        if (executionTimer.current !== null) window.clearTimeout(executionTimer.current);
        executionTimer.current = null;
        setRuntimeStatus(liveText("App 正在真实 MicroPythonOS WASM 中运行", "App is running in real MicroPythonOS WASM"));
        setCurrentStage(stages.length - 1);
        setStatus("completed");
        setLogs((items) => [...items, liveText("[preview] App 已在 MicroPythonOS WASM 中启动 ✓", "[preview] App started in MicroPythonOS WASM ✓")]);
        const savedSession = localStorage.getItem("mpos-session-id");
        if (savedSession) {
          void fetch(`${apiUrl}/api/sessions/${savedSession}/actions/preview-result`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              idempotency_key: `preview-success-${savedSession}`,
              result: "success",
              message: "MicroPythonOS WASM started and self_test passed",
            }),
          }).then((response) => response.json()).then((session: SessionState) => setSessionState(session));
        }
      } else if (message.type === "MPOS_ERROR") {
        if (executionTimer.current !== null) window.clearTimeout(executionTimer.current);
        executionTimer.current = null;
        const detail = message.message || liveText("MicroPythonOS WASM 运行失败", "MicroPythonOS WASM failed");
        if (repairHandler.current(detail)) {
          setRuntimeStatus(liveText(`发现兼容问题，DeepSeek 正在自动修复（${repairAttempts.current}/2）…`, `Compatibility issue found. DeepSeek is repairing it (${repairAttempts.current}/2)…`));
          setLogs((items) => [...items, liveText(`[repair] WASM 发现兼容错误，正在自动修复：${detail}`, `[repair] WASM compatibility error found; repairing: ${detail}`)]);
          return;
        }
        setRuntimeStatus(detail);
        setErrorMessage(detail);
        setStatus("failed");
        setLogs((items) => [...items, `[preview] ${detail}`]);
        const savedSession = localStorage.getItem("mpos-session-id");
        if (savedSession) {
          void fetch(`${apiUrl}/api/sessions/${savedSession}/actions/preview-result`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              idempotency_key: `preview-failed-${crypto.randomUUID()}`,
              result: "failed",
              message: detail,
            }),
          });
        }
      }
    };
    window.addEventListener("message", receive);
    return () => window.removeEventListener("message", receive);
  }, []);

  useEffect(() => {
    if (!result || !wasmReady || !iframeRef.current || !sessionState?.input.targets.includes("web-preview")) return;
    const appCode = result.files.find((file) => file.path === "assets/main.py" || file.path === "app.py")?.content;
    if (!appCode || lastRun.current === result.mpk_base64) return;
    lastRun.current = result.mpk_base64;
    setCurrentStage(stages.length - 1);
    setRuntimeStatus(tr("正在发送生成代码到 MicroPythonOS WASM…", "Sending generated code to MicroPythonOS WASM…"));
    if (executionTimer.current !== null) window.clearTimeout(executionTimer.current);
    executionTimer.current = window.setTimeout(() => {
      const detail = liveText("App 在 WASM 中执行超时，可能包含阻塞循环或等待。", "App execution timed out in WASM, possibly due to blocking code.");
      setWasmReady(false);
      if (iframeRef.current) {
        iframeRef.current.src = `${wasmRuntimeUrl}&recovery=${Date.now()}`;
      }
      if (repairHandler.current(detail)) {
        setRuntimeStatus(liveText(`发现阻塞代码，DeepSeek 正在自动修复（${repairAttempts.current}/2）…`, `Blocking code found. DeepSeek is repairing it (${repairAttempts.current}/2)…`));
        setLogs((items) => [...items, liveText(`[repair] ${detail} 已重载 WASM，正在自动修复。`, `[repair] ${detail} WASM reloaded; automatic repair started.`)]);
        return;
      }
      setRuntimeStatus(detail);
      setErrorMessage(detail);
      setStatus("timeout");
      setLogs((items) => [...items, `[preview] ${detail}`]);
      const savedSession = localStorage.getItem("mpos-session-id");
      if (savedSession) {
        void fetch(`${apiUrl}/api/sessions/${savedSession}/actions/preview-result`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            idempotency_key: `preview-timeout-${crypto.randomUUID()}`,
            result: "timeout",
            message: detail,
          }),
        });
      }
    }, 60000);
    iframeRef.current.contentWindow?.postMessage({
      source: "mpos-builder",
      type: "RUN_APP",
      packageName: result.package_name,
      appCode,
      manifest: JSON.stringify(result.manifest),
    }, "*");
  }, [result, wasmReady, sessionState]);

  const run = async (repair?: { runtimeError: string; previousCode: string }) => {
    requestAbort.current?.abort();
    clearRuntimeTimers();
    setPermissionOpen(false);
    setStatus("running");
    setCurrentStage(0);
    setActiveTab("preview");
    setErrorMessage("");
    if (!repair) {
      setResult(null);
      resultRef.current = null;
      setSessionState(null);
    }
    lastRun.current = "";
    if (!repair) {
      repairAttempts.current = 0;
      setLogs([
        tr("[analysis] 已把你的需求发送到后端", "[analysis] Request sent to the backend"),
        tr("[generation] 正在等待 DeepSeek 生成 MicroPythonOS 代码…", "[generation] Waiting for DeepSeek to generate MicroPythonOS code…"),
      ]);
    } else {
      setLogs((items) => [...items, tr(`[repair] 第 ${repairAttempts.current}/2 次自动修复：正在让 DeepSeek 重写不兼容代码…`, `[repair] Automatic repair ${repairAttempts.current}/2: DeepSeek is rewriting incompatible code…`)]);
    }
    setCurrentStage(1);
    const controller = new AbortController();
    requestAbort.current = controller;
    const requestTimer = window.setTimeout(() => controller.abort(), 180000);
    try {
      let sessionId = repair || continuing || sessionState?.status === "blocked" || sessionState?.status === "created"
        ? localStorage.getItem("mpos-session-id")
        : null;
      if (!sessionId) {
        const selectedTargets = [
          desktopTarget ? "desktop-preview" : "",
          webTarget ? "web-preview" : "",
          physicalTarget ? "physical-device" : "",
          packageTarget ? "package-only" : "",
        ].filter(Boolean);
        const createResponse = await fetch(`${apiUrl}/api/sessions`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            protocol_version: "mpos-ai-app/v1",
            idempotency_key: `create-${crypto.randomUUID()}`,
            prompt,
            prompt_language: isZh ? "zh-CN" : "en-US",
            ui_locale: isZh ? "zh-CN" : "en-US",
            package_name: packageName,
            display_name: displayName,
            publisher,
            version,
            targets: selectedTargets,
          }),
          signal: controller.signal,
        });
        if (!createResponse.ok) throw new Error(tr("无法创建生成会话", "Could not create session"));
        const created = await createResponse.json() as SessionState;
        sessionId = created.session_id;
        localStorage.setItem("mpos-session-id", sessionId);
        setSessionState(created);
        if (created.permissions.some((item) => item.required && item.decision === "pending")) {
          setStatus("blocked");
          setPermissionOpen(true);
          setLogs((items) => [...items, tr("[permission] 等待你逐项确认操作权限", "[permission] Waiting for individual approvals")]);
          return;
        }
      } else if (continuing && !repair) {
        const revisionResponse = await fetch(`${apiUrl}/api/sessions/${sessionId}/revisions`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            idempotency_key: `revision-${crypto.randomUUID()}`,
            prompt,
            prompt_language: isZh ? "zh-CN" : "en-US",
          }),
          signal: controller.signal,
        });
        if (!revisionResponse.ok) throw new Error(tr("无法创建新版本", "Could not create revision"));
        const revised = await revisionResponse.json() as SessionState;
        setSessionState(revised);
        setContinuing(false);
      }
      openEventStream(sessionId);
      const actionResponse = await fetch(`${apiUrl}/api/sessions/${sessionId}/${repair ? "retry" : "actions/generate"}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          idempotency_key: `${repair ? "repair" : "generate"}-${crypto.randomUUID()}`,
          previous_code: repair?.previousCode,
          runtime_error: repair?.runtimeError,
        }),
        signal: controller.signal,
      });
      if (!actionResponse.ok) throw new Error(tr("后端拒绝启动生成任务", "Backend refused to start generation"));

      let session = await actionResponse.json() as SessionState;
      while (!["waiting_preview", "completed", "failed", "blocked", "cancelled", "timeout"].includes(session.status)) {
        await new Promise((resolve) => window.setTimeout(resolve, 700));
        const poll = await fetch(`${apiUrl}/api/sessions/${sessionId}`, { signal: controller.signal });
        if (!poll.ok) throw new Error(tr("读取会话状态失败", "Could not read session state"));
        session = await poll.json() as SessionState;
        setSessionState(session);
        if (session.checkpoint_id === "requirements_analyzed") setCurrentStage(1);
        if (session.checkpoint_id === "code_generated") setCurrentStage(2);
        if (session.checkpoint_id === "package_done") setCurrentStage(3);
      }
      setSessionState(session);
      refreshHistory();
      if (session.status === "failed" || session.status === "blocked" || session.status === "timeout") {
        const failure = session.last_error;
        throw new Error(failure ? `[${failure.code}] ${failure.message}` : tr("生成失败", "Generation failed"));
      }
      if (session.status === "cancelled") throw new Error(tr("任务已取消", "Task cancelled"));
      const generated = session.generation;
      if (!generated) throw new Error(tr("会话完成但没有生成产物", "Session finished without generated artifacts"));
      setCurrentStage(2);
      setLogs((items) => [
        ...items,
        tr(`[generation] ${generated.model} 已返回真实代码 ✓`, `[generation] ${generated.model} returned real code ✓`),
        tr("[validation] Python 语法和基础安全检查通过 ✓", "[validation] Python syntax and safety checks passed ✓"),
        tr(`[validation] 已生成 ${generated.acceptance_tests.length} 项功能验收，并将在 WASM 中执行 self_test ✓`, `[validation] Created ${generated.acceptance_tests.length} acceptance checks; self_test will run in WASM ✓`),
      ]);
      setCurrentStage(3);
      setLogs((items) => [
        ...items,
        tr(`[package] 已生成 ${generated.mpk_filename} ✓`, `[package] Created ${generated.mpk_filename} ✓`),
        tr("[preview] 正在等待 MicroPythonOS WASM 执行生成代码…", "[preview] Waiting for MicroPythonOS WASM to run the generated code…"),
      ]);
      setResult(generated);
      resultRef.current = generated;
      setCurrentStage(stages.length - 1);
      if (session.status === "completed") setStatus("completed");
      setActiveTab("preview");
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError" && requestAbort.current !== controller) {
        return;
      }
      const message = error instanceof DOMException && error.name === "AbortError"
        ? tr("DeepSeek 生成超过 3 分钟，已停止等待。请重试。", "DeepSeek took over 3 minutes. Please retry.")
        : error instanceof Error ? error.message : tr("未知错误", "Unknown error");
      setErrorMessage(message);
      setStatus(error instanceof DOMException && error.name === "AbortError" ? "timeout" : "failed");
      setLogs((items) => [...items, tr(`[失败] ${message}`, `[failed] ${message}`)]);
    } finally {
      window.clearTimeout(requestTimer);
      if (requestAbort.current === controller) requestAbort.current = null;
      repairing.current = false;
    }
  };

  const decidePermission = async (permission: Permission, decision: "allow_once" | "deny") => {
    if (permissionBusy || permission.decision !== "pending") return;
    setPermissionBusy(permission.permission_id);
    try {
      const response = await fetch(`${apiUrl}/api/permissions/${permission.permission_id}/decision`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          idempotency_key: `permission-${permission.permission_id}-${crypto.randomUUID()}`,
          decision,
        }),
      });
      if (!response.ok) throw new Error(tr("权限决定保存失败", "Could not save permission decision"));
      const session = await response.json() as SessionState;
      applySession(session);
      if (decision === "deny") {
        setErrorMessage(session.last_error?.message || tr("权限已拒绝", "Permission denied"));
      }
    } catch (error) {
      setToast(error instanceof Error ? error.message : tr("权限操作失败", "Permission action failed"));
    } finally {
      setPermissionBusy("");
    }
  };

  repairHandler.current = (detail: string) => {
    const generated = resultRef.current;
    const appCode = generated?.files.find((file) => file.path === "assets/main.py" || file.path === "app.py")?.content;
    if (repairing.current) return true;
    if (!generated || !appCode || repairAttempts.current >= 2) return false;
    repairAttempts.current += 1;
    repairing.current = true;
    void run({ runtimeError: detail, previousCode: appCode });
    return true;
  };

  const stop = () => {
    requestAbort.current?.abort();
    requestAbort.current = null;
    clearRuntimeTimers();
    setStatus("cancelled");
    setCurrentStage(-1);
    setLogs((items) => [...items, tr("[停止] 用户停止了本次任务", "[stopped] Task stopped by user")]);
    setToast(tr("任务已停止，可以重新生成", "Task stopped. You can generate again."));
    const savedSession = localStorage.getItem("mpos-session-id");
    if (savedSession) {
      void fetch(`${apiUrl}/api/sessions/${savedSession}/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ idempotency_key: `cancel-${crypto.randomUUID()}` }),
      });
    }
  };

  const retry = () => {
    setToast(tr("正在重新调用 DeepSeek", "Calling DeepSeek again"));
    void run();
  };

  const download = (filename: string, content: string) => {
    const blob = new Blob([content], { type: "application/octet-stream" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(url);
    setToast(tr(`已下载 ${filename}`, `Downloaded ${filename}`));
  };

  const downloadArtifact = (artifact: Artifact) => {
    const anchor = document.createElement("a");
    anchor.href = `${apiUrl}/api/artifacts/${artifact.id}`;
    anchor.download = artifact.display_name;
    anchor.click();
    setToast(tr(`已下载 ${artifact.display_name}`, `Downloaded ${artifact.display_name}`));
  };

  const scanDevices = async () => {
    if (!sessionState) {
      setToast(tr("请先创建生成会话", "Create a session first"));
      return;
    }
    const serialPermission = sessionState.permissions.find((item) => item.permission_type === "serial_scan");
    if (!serialPermission || serialPermission.decision !== "allow_once") {
      setPermissionOpen(true);
      setToast(tr("请先确认串口扫描权限", "Approve serial scan first"));
      return;
    }
    const response = await fetch(`${apiUrl}/api/sessions/${sessionState.session_id}/devices/scan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ idempotency_key: `device-scan-${crypto.randomUUID()}` }),
    });
    const data = await response.json() as { message?: string; ports?: unknown[] };
    setDeviceMessage(data.message || tr(`检测到 ${data.ports?.length || 0} 个设备`, `Found ${data.ports?.length || 0} devices`));
  };

  const downloadMpk = () => {
    if (!result) return;
    const binary = window.atob(result.mpk_base64);
    const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
    const blob = new Blob([bytes], { type: "application/zip" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = result.mpk_filename;
    anchor.click();
    URL.revokeObjectURL(url);
    setToast(tr(`已下载真实 ${result.mpk_filename}`, `Downloaded ${result.mpk_filename}`));
  };

  return (
    <div className="page">
      <header>
        <div className="brand"><span>MP</span><div><strong>MicroPythonOS AI App Builder</strong><small>{tr("用自然语言生成 App", "Build apps with natural language")}</small></div></div>
        <div className="header-actions">
          <button className="language-button" onClick={() => setLanguage(isZh ? "en" : "zh")} aria-label={tr("切换为英文", "Switch to Chinese")}>
            {isZh ? "English" : "中文"}
          </button>
          <div className={`run-state ${status}`}><i />{status === "running" ? tr("生成中", "Generating") : status === "blocked" ? tr("等待授权", "Permission required") : status === "cancelled" ? tr("已取消", "Cancelled") : status === "timeout" ? tr("已超时", "Timed out") : status === "failed" ? tr("生成失败", "Failed") : status === "completed" ? tr("已完成", "Completed") : tr("系统就绪", "Ready")}</div>
        </div>
      </header>

      <main>
        <section className="hero">
          <p>{tr("不用会写代码", "No coding required")}</p>
          <h1>{tr("说出你想要的 App，", "Describe your app. ")}<em>{tr("AI 帮你生成、测试并打包。", "AI builds, tests, and packages it.")}</em></h1>
        </section>

        <div className="workspace">
          <section className="card input-card">
            <label htmlFor="prompt">{tr("你想做什么 App？", "What app do you want to build?")}</label>
            <textarea id="prompt" value={prompt} disabled={status === "running"} onChange={(event) => setPrompt(event.target.value)} />
            <div className="chips">
              {(isZh ? [
                ["极简计算器", defaultPrompt],
                ["番茄钟", "做一个番茄专注计时器，可以开始、暂停和重置"],
                ["设备状态面板", "做一个设备状态面板，显示 CPU、内存、存储、WiFi 和电量"],
              ] : [
                ["Calculator", defaultPromptEn],
                ["Pomodoro", "Build a Pomodoro focus timer with start, pause, and reset"],
                ["Device dashboard", "Build a device dashboard showing CPU, memory, storage, WiFi, and battery"],
              ]).map(([name, value]) => <button key={name} onClick={() => setPrompt(value)}>{name}</button>)}
            </div>

            <details>
              <summary>{tr("App 信息（默认值可以直接用）", "App information (defaults are ready to use)")}</summary>
              <div className="meta-grid">
                <label>{tr("包名", "Package name")}<input value={packageName} onChange={(event) => setPackageName(event.target.value)} /></label>
                <label>{tr("显示名", "Display name")}<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} /></label>
                <label>Publisher<input value={publisher} onChange={(event) => setPublisher(event.target.value)} /></label>
                <label>Version<input value={version} onChange={(event) => setVersion(event.target.value)} /></label>
              </div>
            </details>

            <div className="targets">
              <label><input type="checkbox" checked={desktopTarget} onChange={(event) => setDesktopTarget(event.target.checked)} /> Desktop smoke test<small>{tr("当前主机不支持时会明确标为跳过", "Marked skipped when unavailable on this host")}</small></label>
              <label><input type="checkbox" checked={webTarget} onChange={(event) => setWebTarget(event.target.checked)} /> Web/WASM preview<small>{tr("在真实 MicroPythonOS WASM 中运行", "Run in real MicroPythonOS WASM")}</small></label>
              <label><input type="checkbox" checked={physicalTarget} onChange={(event) => setPhysicalTarget(event.target.checked)} /> Physical device deploy<small>{tr("未检测到设备时进入系统安装引导", "Shows OS installation guidance if no device is found")}</small></label>
              <label><input type="checkbox" checked={packageTarget} onChange={(event) => setPackageTarget(event.target.checked)} /> Package only<small>{tr("生成可以下载的 _rN.mpk", "Create a downloadable _rN.mpk")}</small></label>
            </div>
            <div className="device-guide">
              <div><strong>{tr("要在真实设备上运行？", "Want to run on a real device?")}</strong><span>{tr("先用 Chrome、Edge 或 Brave，通过 USB 给设备安装 MicroPythonOS，然后回到这里下载 MPK。", "First install MicroPythonOS over USB with Chrome, Edge, or Brave, then return here to download the MPK.")}</span></div>
              {sessionState && <button onClick={() => void scanDevices()}>{tr("扫描设备", "Scan devices")}</button>}
              <a href="https://install.micropythonos.com/" target="_blank" rel="noreferrer">{tr("打开系统安装器", "Open OS installer")}</a>
            </div>
            {deviceMessage && <small className="device-message">{deviceMessage}</small>}

            <div className="actions">
              {status === "running"
                ? <button className="danger-button" onClick={stop}>{tr("停止任务", "Stop")}</button>
                : <button className="main-button" disabled={!prompt.trim() || !(desktopTarget || webTarget || physicalTarget || packageTarget)} onClick={() => void run()}>{continuing ? tr("生成新版本", "Generate revision") : ["completed", "failed", "cancelled", "timeout"].includes(status) ? tr("重新生成 App", "Regenerate App") : tr("开始生成 App", "Generate App")}</button>}
              <span className="real-badge">{tr("真实调用 DeepSeek", "Real DeepSeek API")}</span>
            </div>
          </section>

          <section className="card progress-card">
            <h2>{tr("生成进度", "Generation progress")}</h2>
            {status === "idle" && currentStage < 0 && <div className="empty-progress"><b>1</b><span>{tr("输入你的想法", "Describe your idea")}</span><b>2</b><span>{tr("允许浏览器模拟运行", "Allow browser simulation")}</span><b>3</b><span>{tr("预览并下载 App", "Preview and download")}</span></div>}
            {(status !== "idle" || currentStage >= 0) && (
              <ol className="timeline">
                {stages.map(([english, chinese], index) => {
                  const stageStatus = ["failed", "timeout", "cancelled"].includes(status) && index === currentStage ? "error" : index < currentStage || (status === "completed" && index === currentStage) ? "done" : index === currentStage ? "active" : "waiting";
                  return <li className={stageStatus} key={english}><i>{stageStatus === "done" ? "✓" : stageStatus === "error" ? "!" : index + 1}</i><div><strong>{isZh ? chinese : english}</strong>{isZh && <small>{english}</small>}</div><span>{stageStatus === "done" ? tr("成功", "Done") : stageStatus === "active" ? tr("进行中", "Running") : stageStatus === "error" ? tr("失败", "Failed") : tr("等待", "Waiting")}</span></li>;
                })}
              </ol>
            )}
            {status === "completed" && <div className="success-box"><strong>{sessionState?.input.targets.includes("web-preview") ? tr("App 已在 MicroPythonOS WASM 中真实运行", "App is running in MicroPythonOS WASM") : tr("所选生成和打包阶段已完成", "Selected generation and packaging stages are complete")}</strong><span>{tr(`当前版本 ${sessionState?.revision_id || "r1"}；可以继续描述修改，不会覆盖上一成功版本。`, `Current revision ${sessionState?.revision_id || "r1"}. Continue editing without overwriting the last successful revision.`)}</span><button onClick={() => { setContinuing(true); setStatus("idle"); setToast(tr("请修改上方需求，然后点击“生成新版本”", "Edit the prompt, then click Generate revision")); }}>{tr("继续修改这个 App", "Continue editing this app")}</button></div>}
            {["failed", "timeout", "cancelled", "blocked"].includes(status) && <div className={`error-box state-${status}`}>
              <strong>{status === "timeout" ? tr("运行超时", "Timed out") : status === "cancelled" ? tr("任务已取消", "Cancelled") : status === "blocked" ? tr("等待处理", "Blocked") : tr("真实生成失败", "Generation failed")}</strong>
              {sessionState?.last_error && <code>{sessionState.last_error.code} · {sessionState.last_error.stage} · owner: {sessionState.last_error.owner}</code>}
              <span>{errorMessage}</span>
              <div>{status === "blocked" && sessionState?.permissions.some((item) => item.required && item.decision === "pending") && <button onClick={() => setPermissionOpen(true)}>{tr("处理权限", "Review permissions")}</button>}<button onClick={() => { void navigator.clipboard.writeText(`${sessionState?.last_error?.code || "ERROR"}\n${errorMessage}\nSession: ${sessionState?.session_id || "unknown"}`); setToast(tr("错误已复制，可以发给 AI", "Error copied for AI")); }}>{tr("复制给 AI 修复", "Copy for AI")}</button><button onClick={retry}>{tr("从失败检查点重试", "Retry from checkpoint")}</button></div>
            </div>}
            {sessionState?.warnings.length ? <div className="warning-box"><strong>{tr("警告（不等于失败）", "Warnings (not failures)")}</strong>{sessionState.warnings.map((warning) => <span key={warning}>⚠ {warning}</span>)}</div> : null}
          </section>
        </div>

        {history.length > 0 && <section className="card history-card">
          <div><h2>{tr("历史会话", "Session history")}</h2><span>{tr("刷新页面或关闭浏览器后仍可恢复", "Restore work after refresh or closing the browser")}</span></div>
          <div className="history-list">{history.slice(0, 5).map((item) => <button key={item.session_id} onClick={() => void restoreSession(item.session_id)}><strong>{item.input.display_name}</strong><span>{item.revision_id} · {item.status} · {item.checkpoint_id}</span><small>{item.input.prompt_original}</small></button>)}</div>
        </section>}

        <section className="card result-card">
          <div className="tabs">
            <button className={activeTab === "preview" ? "active" : ""} onClick={() => setActiveTab("preview")}>{tr("App 预览", "App Preview")}</button>
            <button className={activeTab === "logs" ? "active" : ""} onClick={() => setActiveTab("logs")}>{tr("运行日志", "Runtime Logs")}</button>
            <button className={activeTab === "artifacts" ? "active" : ""} onClick={() => setActiveTab("artifacts")}>{tr("生成产物", "Artifacts")}</button>
          </div>
          {activeTab === "preview" && (
            <div className="preview-pane">
              <div className="preview-copy">
                <h3>{tr("浏览器模拟屏幕", "Browser Simulator")}</h3>
                <p>{tr("右边不是假图片，里面运行的是实际的 MicroPythonOS WebAssembly；生成成功后可以直接点击 App。", "The device on the right runs real MicroPythonOS WebAssembly. You can interact with the app after generation.")}</p>
                <p className="preview-limit">{tr("Web 预览只是浏览器兼容性预览，不等于真机验证。摄像头、IMU、GPIO、串口、蓝牙、音频、SD 卡和实体按键必须上真机测试。", "Web preview is a browser compatibility preview, not hardware validation. Camera, IMU, GPIO, serial, Bluetooth, audio, SD card, and physical buttons require a real device.")}</p>
                <div className={`runtime-pill ${["failed", "timeout"].includes(status) ? "error" : wasmReady ? "ready" : ""}`}>
                  <i />{runtimeStatus}
                </div>
                {sessionState?.artifacts.find((item) => item.role === "desktop_screenshot") && <img className="desktop-screenshot" src={`${apiUrl}/api/artifacts/${sessionState.artifacts.find((item) => item.role === "desktop_screenshot")!.id}`} alt={tr("桌面测试截图", "Desktop smoke screenshot")} />}
                {result && <small className="preview-summary">{result.summary} · {result.model}</small>}
              </div>
              <div className="device wasm-device">
                <div className="device-status"><span>10:24</span><span>● WiFi　87%</span></div>
                <iframe
                  ref={iframeRef}
                  title="MicroPythonOS WebAssembly Runtime"
                  src={wasmRuntimeUrl}
                  allow="clipboard-read; clipboard-write"
                  onLoad={() => {
                    setWasmReady(false);
                    setRuntimeStatus(tr("正在启动 MicroPythonOS WASM…", "Starting MicroPythonOS WASM…"));
                    iframeRef.current?.contentWindow?.postMessage({ source: "mpos-builder", type: "PING" }, "*");
                    window.setTimeout(() => iframeRef.current?.contentWindow?.postMessage({ source: "mpos-builder", type: "PING" }, "*"), 1500);
                    if (wasmTimer.current !== null) window.clearTimeout(wasmTimer.current);
                    wasmTimer.current = window.setTimeout(() => {
                      const detail = tr("MicroPythonOS WASM 启动超时，请刷新页面后重试。", "MicroPythonOS WASM startup timed out. Refresh and retry.");
                      setRuntimeStatus(detail);
                      setErrorMessage(detail);
                      setStatus("timeout");
                    }, 120000);
                  }}
                />
              </div>
            </div>
          )}
          {activeTab === "logs" && <div className="logs"><button onClick={() => { navigator.clipboard.writeText(logs.join("\n")); setToast(tr("日志已复制", "Logs copied")); }}>{tr("复制日志", "Copy logs")}</button><pre>{logs.length ? logs.join("\n") : tr("// 点击“开始生成 App”后，这里会显示日志。", "// Logs will appear here after you generate an app.")}</pre></div>}
          {activeTab === "artifacts" && (
            result
              ? <div className="artifacts">
                  {sessionState?.artifacts.length
                    ? <ul>{sessionState.artifacts.map((artifact) => <li key={artifact.id}><span>▣　{artifact.path}<small>{artifact.role} · {artifact.kind} · {Math.ceil(artifact.size / 1024)} KB</small><small>{artifact.mime} · {artifact.phase}</small><code title={artifact.sha256}>sha256: {artifact.sha256.slice(0, 16)}…</code></span><button onClick={() => downloadArtifact(artifact)}>{tr("下载", "Download")}</button></li>)}</ul>
                    : <ul>{result.files.map((file) => <li key={file.path}><span>▣　{file.path}</span><button onClick={() => download(file.path, file.content)}>{tr("下载", "Download")}</button></li>)}</ul>}
                  <div className="mpk"><div><strong>{result.mpk_filename}</strong><small>{tr("包含真实 MANIFEST.JSON 和 assets/main.py，文件名符合 _rN 发布规则", "Contains MANIFEST.JSON and assets/main.py with the required _rN release name")}</small></div><button onClick={downloadMpk}>{tr("下载真实 .mpk", "Download .mpk")}</button></div>
                  <div className="publish-guide">
                    <strong>{tr("uPyStore 发布检查", "uPyStore checklist")}</strong>
                    <span>{tr("✓ Manifest　✓ _rN.mpk　△ Web/真机验证　△ PNG/JPEG/WebP 截图。发布材料 ZIP 已进入上方产物列表；这里只提供手工上传引导。", "✓ Manifest  ✓ _rN.mpk  △ Web/device validation  △ PNG/JPEG/WebP screenshot. The publishing ZIP is listed above; upload remains manual.")}</span>
                    <a href="https://upystore.io/developer" target="_blank" rel="noreferrer">{tr("打开 uPyStore 开发者入口", "Open uPyStore Developer")}</a>
                  </div>
                </div>
              : <div className="not-ready">{tr("真实生成成功后，这里会出现 DeepSeek 生成的源码和 `.mpk`。", "DeepSeek source files and the `.mpk` will appear here after generation.")}</div>
          )}
        </section>
      </main>

      {permissionOpen && sessionState && <div className="modal-backdrop"><div className="modal permission-host">
        <h2>{tr("确认操作权限", "Review permissions")}</h2>
        <p>{tr("每项操作都必须单独确认。同一个 permission_id 只能回答一次。", "Each operation requires a separate decision. A permission_id can only be answered once.")}</p>
        <div className="permission-list">
          {sessionState.permissions.filter((item) => item.required).map((permission) => (
            <article className={`permission-card risk-${permission.risk} decision-${permission.decision}`} key={permission.permission_id}>
              <header><strong>{permission.title}</strong><span>{permission.risk}</span></header>
              <p>{permission.description}</p>
              <code>{permission.command_preview}</code>
              <small>{permission.permission_type} · {permission.permission_id}</small>
              {permission.decision === "pending"
                ? <div><button disabled={Boolean(permissionBusy)} className="secondary-button" onClick={() => void decidePermission(permission, "deny")}>{tr("拒绝", "Deny")}</button><button disabled={Boolean(permissionBusy)} className="main-button" onClick={() => void decidePermission(permission, "allow_once")}>{permissionBusy === permission.permission_id ? tr("保存中…", "Saving…") : tr("仅允许一次", "Allow once")}</button></div>
                : <b>{permission.decision === "allow_once" ? tr("✓ 已允许一次", "✓ Allowed once") : tr("✕ 已拒绝", "✕ Denied")}</b>}
            </article>
          ))}
        </div>
        <small>{tr("API Key 只保存在 backend/.env。模型不能发送任意 shell，也不能绕过这些权限。", "The API key stays in backend/.env. The model cannot send arbitrary shell commands or bypass these permissions.")}</small>
        <div>
          <button className="secondary-button" onClick={() => setPermissionOpen(false)}>{tr("稍后处理", "Later")}</button>
          <button
            className="main-button"
            disabled={sessionState.permissions.some((item) => item.required && item.decision === "pending") || sessionState.permissions.some((item) => item.required && item.decision === "deny")}
            onClick={() => { setPermissionOpen(false); void run(); }}
          >{tr("全部确认，开始运行", "Continue")}</button>
        </div>
      </div></div>}
      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}
