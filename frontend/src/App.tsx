import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  API_BASE_URL,
  GENERATION_IDLE_TIMEOUT_MS,
  GENERATION_OVERALL_TIMEOUT_MS,
  WASM_RUNTIME_URL,
} from "./config";
import { WebSerialDeviceClient, type DeviceConnectionState } from "./deviceSerial";
import { DeviceCapabilityPanel } from "./CapabilityPanel";
import type { CapabilityProbeOutcome } from "./capabilities";
import { probeCapabilities, readHardwareId, type DeviceExecute } from "./deviceCapabilities";
import {
  describeProviderResult,
  emptyProviderCatalog,
  isAiProviderId,
  normalizeProviderCatalog,
  type AiProviderId,
  type AiProviderMetadata,
} from "./providerRouting";
import {
  buildShowcaseRunMessage,
  encodeShowcaseMpk,
  fetchVerifiedShowcaseMpk,
  getBridgeTargetOrigin,
  getGenerationWaitTimeoutKind,
  hasGenerationActivityChanged,
  isPlatformActionAllowed,
  normalizePublicSystemStatus,
  previewResultKey,
  unavailablePublicSystemStatus,
  type GenerationActivitySnapshot,
  type GenerationWaitTimeoutKind,
  type PublicSystemStatus,
} from "./platformUpgradeLibrary";

import type {
  AccountRole,
  Artifact,
  AuthStatus,
  BillingAccount,
  GeneratedFile,
  GenerationResult,
  Language,
  Permission,
  RequirementChatResult,
  RequirementMessage,
  SessionState,
  SessionSummary,
  ShowcaseApp,
  Status,
  StructuredError,
} from "./types";
import { isShowcaseApp } from "./types";
export { stages, stageIndexForError, stageIndexForCheckpoint, stageIndexForSession } from "./stages";
import { stages, stageIndexForError, stageIndexForCheckpoint, stageIndexForSession } from "./stages";
import { ArtifactsPanel } from "./ArtifactsPanel";
import { AuthScreen, MaintenanceScreen } from "./AuthGate";
import { downloadArtifactFile, downloadMpkFile, downloadText } from "./downloads";
import { EcosystemCard } from "./EcosystemCard";
import { PermissionModal } from "./PermissionModal";
import { PreviewPane } from "./PreviewPane";
import { ProgressCard } from "./ProgressCard";
import { RequirementModal } from "./RequirementModal";


const defaultPrompt = "做一个极简四则运算计算器，按钮要大，适合触摸屏";
const defaultPromptEn = "Build a minimal four-function calculator with large touch-friendly buttons";
const wasmRuntimeUrl = WASM_RUNTIME_URL;
const apiUrl = API_BASE_URL;
const bridgePageUrl = typeof window === "undefined" ? "https://localhost/" : window.location.href;
const wasmRuntimeOrigin = getBridgeTargetOrigin(wasmRuntimeUrl, bridgePageUrl);
const apiFetch = (input: RequestInfo | URL, init: RequestInit = {}) => fetch(
  input,
  { ...init, credentials: "include" },
);
export default function App() {
  const [language, setLanguage] = useState<Language>(() => localStorage.getItem("mpos-language") === "en" ? "en" : "zh");
  const isZh = language === "zh";
  const tr = (zh: string, en: string) => isZh ? zh : en;
  const [prompt, setPrompt] = useState(defaultPrompt);
  const [packageName, setPackageName] = useState("com.example.myapp");
  const [displayName, setDisplayName] = useState("我的 App");
  const [publisher, setPublisher] = useState("erkou111");
  const [version, setVersion] = useState("0.1.0");
  const [aiProvider, setAiProvider] = useState<AiProviderId>("auto");
  const [providerMetadata, setProviderMetadata] = useState<AiProviderMetadata[]>(
    () => emptyProviderCatalog().providers,
  );
  const [providerStatus, setProviderStatus] = useState<"loading" | "ready" | "error">("loading");
  const [desktopTarget, setDesktopTarget] = useState(false);
  const [desktopAvailable, setDesktopAvailable] = useState(false);
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
  const [serialConnected, setSerialConnected] = useState(false);
  const [deviceConnectionDetail, setDeviceConnectionDetail] = useState("");
  const [deviceState, setDeviceState] = useState<DeviceConnectionState>("disconnected");
  const [capabilityProbes, setCapabilityProbes] = useState<CapabilityProbeOutcome[]>([]);
  const [capabilityProbing, setCapabilityProbing] = useState(false);
  const [detectedHardwareId, setDetectedHardwareId] = useState("");
  // null = not checked yet. Never inferred from "a serial port opened": any
  // USB-serial board opens fine without MicroPythonOS on it.
  const [mposDetected, setMposDetected] = useState<boolean | null>(null);

  // Probe evidence belongs to one device and one session. Carrying it across a
  // disconnect or a session switch would attribute one board's hardware to
  // another, which is exactly what this feature exists to prevent.
  const previewUnsupportedRef = useRef<string[]>([]);

  const resetCapabilityEvidence = () => {
    setCapabilityProbes([]);
    setDetectedHardwareId("");
    setMposDetected(null);
    previewUnsupportedRef.current = [];
  };
  const [deviceLogs, setDeviceLogs] = useState("");
  const [deviceCommand, setDeviceCommand] = useState("");
  const [deviceBusy, setDeviceBusy] = useState("");
  const [deviceProgress, setDeviceProgress] = useState(0);
  const [deviceError, setDeviceError] = useState("");
  const [screenshotBusy, setScreenshotBusy] = useState(false);
  const [continuing, setContinuing] = useState(false);
  const [billingAccount, setBillingAccount] = useState<BillingAccount | null>(null);
  const [authStatus, setAuthStatus] = useState<AuthStatus>("loading");
  const [authMode, setAuthMode] = useState<"login" | "register">("login");
  const [authUsername, setAuthUsername] = useState("");
  const [authPassword, setAuthPassword] = useState("");
  const [authBusy, setAuthBusy] = useState(false);
  const [authError, setAuthError] = useState("");
  const [requirementOpen, setRequirementOpen] = useState(false);
  const [requirementMessages, setRequirementMessages] = useState<RequirementMessage[]>([]);
  const [requirementInput, setRequirementInput] = useState("");
  const [requirementBusy, setRequirementBusy] = useState(false);
  const [requirementError, setRequirementError] = useState("");
  const [requirementResult, setRequirementResult] = useState<RequirementChatResult | null>(null);
  const [showcaseApps, setShowcaseApps] = useState<ShowcaseApp[]>([]);
  const [showcaseStatus, setShowcaseStatus] = useState<"loading" | "ready" | "error">("loading");
  const [showcaseQuery, setShowcaseQuery] = useState("");
  const [showcaseCategory, setShowcaseCategory] = useState("all");
  const [showAllShowcase, setShowAllShowcase] = useState(false);
  const [showcaseAction, setShowcaseAction] = useState("");
  const [wasmReady, setWasmReady] = useState(false);
  const [runtimeStatus, setRuntimeStatus] = useState("正在启动 MicroPythonOS WASM…");
  const [publicSystemStatus, setPublicSystemStatus] = useState<PublicSystemStatus>(
    () => unavailablePublicSystemStatus(),
  );
  const [systemStatusConfirmed, setSystemStatusConfirmed] = useState(false);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const lastRun = useRef("");
  const requestAbort = useRef<AbortController | null>(null);
  const eventStream = useRef<EventSource | null>(null);
  const eventCursors = useRef<Record<string, number>>({});
  const executionTimer = useRef<number | null>(null);
  const wasmTimer = useRef<number | null>(null);
  const resultRef = useRef<GenerationResult | null>(null);
  const serialClientRef = useRef<WebSerialDeviceClient | null>(null);
  const deviceInfoRef = useRef<{ usbVendorId?: number; usbProductId?: number }>({});
  const showcasePreviewRef = useRef("");
  const generationActivityAt = useRef(Date.now());
  const generationActivitySnapshot = useRef<GenerationActivitySnapshot | null>(null);
  const generationEventKeys = useRef<Record<string, string>>({});
  const confirmedMaintenanceStatus = useRef<PublicSystemStatus | null>(null);
  const repairAttempts = useRef(0);
  const repairing = useRef(false);
  const repairHandler = useRef<(detail: string) => boolean>(() => false);
  const languageRef = useRef<Language>(language);
  languageRef.current = language;
  const liveText = (zh: string, en: string) => languageRef.current === "zh" ? zh : en;
  const markGenerationActivity = (snapshot: GenerationActivitySnapshot) => {
    if (!hasGenerationActivityChanged(generationActivitySnapshot.current, snapshot)) return;
    generationActivitySnapshot.current = {
      status: snapshot.status,
      checkpoint_id: snapshot.checkpoint_id,
      revision_id: snapshot.revision_id,
    };
    generationActivityAt.current = Date.now();
  };
  const showcaseCategoryText = (category: string) => {
    if (!isZh) return category.charAt(0).toUpperCase() + category.slice(1);
    const labels: Record<string, string> = {
      education: "教育",
      games: "游戏",
      graphics: "图形",
      health: "健康",
      productivity: "效率",
      utilities: "工具",
      weather: "天气",
    };
    return labels[category] || category;
  };
  const orderedShowcaseApps = [...showcaseApps].sort(
    (left, right) => Number(right.featured) - Number(left.featured) || left.fullname.localeCompare(right.fullname),
  );
  const showcaseCategories = Array.from(new Set(showcaseApps.map((item) => item.category))).sort();
  const normalizedShowcaseQuery = showcaseQuery.trim().toLocaleLowerCase();
  const filteredShowcaseApps = orderedShowcaseApps.filter((item) => {
    const matchesCategory = showcaseCategory === "all" || item.category === showcaseCategory;
    const matchesQuery = !normalizedShowcaseQuery || [
      item.name,
      item.fullname,
      item.category,
      item.shortDescription,
      item.longDescription,
    ].some((value) => value.toLocaleLowerCase().includes(normalizedShowcaseQuery));
    return matchesCategory && matchesQuery;
  });
  const visibleShowcaseApps = showAllShowcase
    ? filteredShowcaseApps
    : orderedShowcaseApps.filter((item) => item.featured).slice(0, 12);
  const selectedProvider = providerMetadata.find((provider) => provider.id === aiProvider);
  const providerReady = providerStatus === "ready" && selectedProvider?.configured === true;
  const providerResult = result ? describeProviderResult(result, providerMetadata) : null;

  const clearRuntimeTimers = () => {
    if (executionTimer.current !== null) window.clearTimeout(executionTimer.current);
    if (wasmTimer.current !== null) window.clearTimeout(wasmTimer.current);
    executionTimer.current = null;
    wasmTimer.current = null;
  };

  useEffect(() => {
    const releaseSerialPort = () => {
      void serialClientRef.current?.disconnect();
    };
    window.addEventListener("pagehide", releaseSerialPort);
    return () => {
      window.removeEventListener("pagehide", releaseSerialPort);
      requestAbort.current?.abort();
      eventStream.current?.close();
      releaseSerialPort();
      clearRuntimeTimers();
    };
  }, []);
  useEffect(() => {
    let stopped = false;
    let timer: number | null = null;
    const checkSystemStatus = async () => {
      let nextDelayMs = 30_000;
      try {
        const response = await apiFetch(`${apiUrl}/api/system/status`);
        if (!response.ok) throw new Error(`system status returned ${response.status}`);
        const normalized = normalizePublicSystemStatus(await response.json());
        if (!normalized) throw new Error("invalid system status");
        if (stopped) return;
        confirmedMaintenanceStatus.current = normalized.maintenance ? normalized : null;
        setPublicSystemStatus(normalized);
        setSystemStatusConfirmed(true);
        nextDelayMs = Math.min(60_000, Math.max(3_000, normalized.retry_after_seconds * 1_000));
        if (normalized.maintenance) void serialClientRef.current?.disconnect();
      } catch {
        if (stopped) return;
        const confirmedMaintenance = confirmedMaintenanceStatus.current;
        if (confirmedMaintenance) {
          setPublicSystemStatus(confirmedMaintenance);
          setSystemStatusConfirmed(true);
        } else {
          setPublicSystemStatus(unavailablePublicSystemStatus());
          setSystemStatusConfirmed(false);
        }
        void serialClientRef.current?.disconnect();
      }
      if (!stopped) timer = window.setTimeout(checkSystemStatus, nextDelayMs);
    };
    void checkSystemStatus();
    return () => {
      stopped = true;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    const loadShowcase = async () => {
      try {
        const response = await fetch("/showcase/catalog.json", { signal: controller.signal });
        if (!response.ok) throw new Error(`Showcase catalog returned ${response.status}`);
        const payload: unknown = await response.json();
        if (!Array.isArray(payload) || !payload.every(isShowcaseApp)) {
          throw new Error("Showcase catalog has an invalid shape");
        }
        setShowcaseApps(payload);
        setShowcaseStatus("ready");
      } catch {
        if (!controller.signal.aborted) setShowcaseStatus("error");
      }
    };
    void loadShowcase();
    return () => controller.abort();
  }, []);
  useEffect(() => {
    localStorage.setItem("mpos-language", language);
  }, [language]);
  useEffect(() => {
    if (authStatus !== "signed_in") return;
    const controller = new AbortController();
    const loadProviders = async () => {
      setProviderStatus("loading");
      try {
        const response = await apiFetch(`${apiUrl}/api/ai/providers`, {
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`provider metadata returned ${response.status}`);
        const catalog = normalizeProviderCatalog(await response.json());
        if (!catalog) throw new Error("provider metadata has an invalid shape");
        setProviderMetadata(catalog.providers);
        setAiProvider((current) => catalog.providers.some(
          (provider) => provider.id === current && provider.configured,
        ) ? current : catalog.defaultProvider);
        setProviderStatus("ready");
      } catch {
        if (!controller.signal.aborted) {
          setProviderMetadata(emptyProviderCatalog().providers);
          setProviderStatus("error");
        }
      }
    };
    void loadProviders();
    return () => controller.abort();
  }, [authStatus]);
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
  const refreshHistory = async () => {
    try {
      const response = await apiFetch(`${apiUrl}/api/sessions`);
      setHistory(response.ok ? await response.json() as SessionSummary[] : []);
    } catch {
      setHistory([]);
    }
  };
  const refreshBilling = async () => {
    const response = await apiFetch(`${apiUrl}/api/billing/account`);
    if (!response.ok) throw new Error("billing unavailable");
    const account = await response.json() as BillingAccount;
    setBillingAccount(account);
    return account;
  };
  const refreshCapabilities = async () => {
    const response = await apiFetch(`${apiUrl}/api/capabilities`);
    const payload = response.ok ? await response.json() : null;
    setDesktopAvailable(Boolean(payload?.capabilities?.desktop_preview));
  };
  const loadWorkspace = async () => {
    await Promise.all([refreshHistory(), refreshCapabilities()]);
  };
  useEffect(() => {
    const initialize = async () => {
      try {
        const response = await apiFetch(`${apiUrl}/api/user`);
        if (response.status === 401) {
          setAuthStatus("signed_out");
          return;
        }
        if (!response.ok) throw new Error("authentication unavailable");
        setBillingAccount(await response.json() as BillingAccount);
        setAuthStatus("signed_in");
        await loadWorkspace();
      } catch {
        setBillingAccount(null);
        setHistory([]);
        setDesktopAvailable(false);
        setAuthError(tr("无法连接内测服务，请稍后重试", "Could not reach the beta service. Try again shortly."));
        setAuthStatus("signed_out");
      }
    };
    void initialize();
  }, []);

  const submitAuth = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (authBusy) return;
    setAuthBusy(true);
    setAuthError("");
    try {
      const response = await apiFetch(`${apiUrl}/api/auth/${authMode}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: authUsername, password: authPassword }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          typeof payload.detail === "string"
            ? payload.detail
            : tr("账号操作失败", "Account request failed"),
        );
      }
      setBillingAccount(payload as BillingAccount);
      setAuthPassword("");
      setAuthStatus("signed_in");
      await loadWorkspace();
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : tr("账号操作失败", "Account request failed"));
    } finally {
      setAuthBusy(false);
    }
  };

  const logout = async () => {
    await apiFetch(`${apiUrl}/api/auth/logout`, { method: "POST" }).catch(() => undefined);
    requestAbort.current?.abort();
    eventStream.current?.close();
    localStorage.removeItem("mpos-session-id");
    setBillingAccount(null);
    setSessionState(null);
    resetCapabilityEvidence();
    setHistory([]);
    setResult(null);
    setStatus("idle");
    setAuthPassword("");
    setAuthStatus("signed_out");
  };

  const applySession = (session: SessionState) => {
    localStorage.setItem("mpos-session-id", session.session_id);
    resetCapabilityEvidence();
    setSessionState(session);
    setPrompt(session.input.prompt_original);
    setPackageName(session.input.package_name);
    setDisplayName(session.input.display_name);
    setPublisher(session.input.publisher);
    setVersion(session.input.version);
    if (isAiProviderId(session.input.ai_provider)) setAiProvider(session.input.ai_provider);
    setDesktopTarget(session.input.targets.includes("desktop-preview"));
    setWebTarget(session.input.targets.includes("web-preview"));
    setPhysicalTarget(session.input.targets.includes("physical-device"));
    setPackageTarget(session.input.targets.includes("package-only"));
    if (session.generation) {
      setResult(session.generation);
      resultRef.current = session.generation;
    }
    setStatus(session.status);
    setCurrentStage(stageIndexForSession(session));
    setErrorMessage(session.last_error?.message || "");
  };
  const restoreSession = async (sessionId: string) => {
    const response = await apiFetch(`${apiUrl}/api/sessions/${sessionId}`);
    if (!response.ok) {
      setToast(tr("会话恢复失败", "Could not restore session"));
      return;
    }
    const session = await response.json() as SessionState;
    applySession(session);
    setLogs((items) => [...items, tr(`[resume] 已打开 ${session.session_id} ${session.revision_id}`, `[resume] Opened ${session.session_id} ${session.revision_id}`)]);
    setToast(tr("历史会话已恢复", "Session restored"));
  };
  const continueWaiting = async () => {
    const sessionId = localStorage.getItem("mpos-session-id") || sessionState?.session_id;
    if (!sessionId || !isPlatformActionAllowed(systemStatusConfirmed, publicSystemStatus.maintenance)) return;
    requestAbort.current?.abort();
    const controller = new AbortController();
    requestAbort.current = controller;
    const startedAt = Date.now();
    generationActivityAt.current = startedAt;
    generationActivitySnapshot.current = sessionState;
    let timeoutKind: GenerationWaitTimeoutKind | null = null;
    const timer = window.setInterval(() => {
      timeoutKind = getGenerationWaitTimeoutKind(
        Date.now(),
        startedAt,
        generationActivityAt.current,
        GENERATION_IDLE_TIMEOUT_MS,
        GENERATION_OVERALL_TIMEOUT_MS,
      );
      if (timeoutKind) controller.abort();
    }, 1_000);
    setStatus("running");
    setErrorMessage("");
    openEventStream(sessionId);
    try {
      while (true) {
        const response = await apiFetch(`${apiUrl}/api/sessions/${sessionId}`, { signal: controller.signal });
        if (!response.ok) throw new Error(tr("读取会话状态失败", "Could not read session state"));
        const session = await response.json() as SessionState;
        markGenerationActivity(session);
        setSessionState(session);
        setCurrentStage(stageIndexForSession(session));
        if (["waiting_preview", "waiting_device", "completed", "failed", "blocked", "cancelled", "timeout"].includes(session.status)) {
          applySession(session);
          setErrorMessage(session.last_error ? `[${session.last_error.code}] ${session.last_error.message}` : "");
          await refreshHistory();
          return;
        }
        await new Promise((resolve) => window.setTimeout(resolve, 700));
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError" && requestAbort.current !== controller) return;
      if (error instanceof DOMException && error.name === "AbortError" && timeoutKind) {
        setStatus("timeout");
        setErrorMessage(timeoutKind === "idle"
          ? tr("后台会话仍在运行，但一段时间没有新状态。任务未取消，可继续等待。", "The backend session is still running but has not reported progress. It was not cancelled; you can keep waiting.")
          : tr("已达到本次前端等待上限。后台任务未取消，可稍后继续等待。", "The frontend wait limit was reached. The backend task was not cancelled; you can resume later."));
      } else {
        setStatus("failed");
        setErrorMessage(error instanceof Error ? error.message : tr("读取会话状态失败", "Could not read session state"));
      }
    } finally {
      window.clearInterval(timer);
      if (requestAbort.current === controller) requestAbort.current = null;
    }
  };
  useEffect(() => {
    if (authStatus !== "signed_in") return;
    const savedSession = localStorage.getItem("mpos-session-id");
    if (!savedSession) return;
    void apiFetch(`${apiUrl}/api/sessions/${savedSession}`)
      .then(async (response) => {
        if (!response.ok) throw new Error("session unavailable");
        return response.json() as Promise<SessionState>;
      })
      .then((session) => {
        if (["completed", "cancelled", "failed", "timeout"].includes(session.status)) {
          localStorage.removeItem("mpos-session-id");
          setStatus("idle");
          setSessionState(null);
          setErrorMessage("");
          setLogs([]);
          return;
        }
        applySession(session);
        setLogs((items) => [...items, liveText(`[resume] 已恢复会话 ${session.session_id}（${session.checkpoint_id}）`, `[resume] Restored ${session.session_id} at ${session.checkpoint_id}`)]);
      })
      .catch(() => localStorage.removeItem("mpos-session-id"));
  }, [authStatus]);

  const openEventStream = (sessionId: string) => {
    eventStream.current?.close();
    const cursor = eventCursors.current[sessionId] || 0;
    const stream = new EventSource(
      `${apiUrl}/api/sessions/${sessionId}/events?after=${cursor}`,
      { withCredentials: true },
    );
    const appendEvent = (event: MessageEvent) => {
      const item = JSON.parse(event.data) as {
        seq?: number;
        type: string;
        phase: string;
        payload: { message?: string; status?: string; result?: string };
      };
      const seq = Number(item.seq || 0);
      if (seq && seq <= (eventCursors.current[sessionId] || 0)) return;
      const eventKey = seq ? `seq:${seq}` : `${event.type}:${event.data}`;
      if (generationEventKeys.current[sessionId] === eventKey) return;
      generationEventKeys.current[sessionId] = eventKey;
      if (seq) eventCursors.current[sessionId] = seq;
      generationActivityAt.current = Date.now();
      const message = item.payload.message || item.payload.status || item.payload.result || item.type;
      setLogs((entries) => [...entries, `[${item.phase}] ${message}`]);
    };
    stream.onmessage = appendEvent;
    for (const eventName of ["start_phase", "status_update", "phase_complete", "structured_error"]) {
      stream.addEventListener(eventName, (event) => appendEvent(event as MessageEvent));
    }
    stream.addEventListener("stream_end", () => {
      stream.close();
      if (eventStream.current === stream) eventStream.current = null;
    });
    eventStream.current = stream;
  };
  useEffect(() => {
    const receive = (event: MessageEvent) => {
      if (
        event.source !== iframeRef.current?.contentWindow
        || event.origin !== wasmRuntimeOrigin
      ) return;
      const message = event.data as { source?: string; type?: string; text?: string; message?: string };
      if (message?.source !== "mpos-web") return;
      if (message.type === "MPOS_READY") {
        if (wasmTimer.current !== null) window.clearTimeout(wasmTimer.current);
        wasmTimer.current = null;
        setWasmReady(true);
        setRuntimeStatus(liveText("MicroPythonOS WASM 已就绪", "MicroPythonOS WASM is ready"));
      } else if (message.type === "MPOS_INSTALLING") {
        const showcaseName = showcasePreviewRef.current;
        setRuntimeStatus(showcaseName
          ? liveText(`正在把 ${showcaseName} MPK 安装进 MicroPythonOS…`, `Installing the ${showcaseName} MPK into MicroPythonOS…`)
          : liveText("正在把生成代码安装进 MicroPythonOS…", "Installing generated code into MicroPythonOS…"));
        setLogs((items) => [...items, showcaseName
          ? liveText(`[showcase] 正在安装 ${showcaseName} MPK…`, `[showcase] Installing the ${showcaseName} MPK…`)
          : liveText("[preview] 正在通过 raw REPL 安装 app.py…", "[preview] Installing app.py through raw REPL…")]);
      } else if (message.type === "MPOS_LOG" && message.text?.trim()) {
        setLogs((items) => [...items, `[wasm] ${message.text!.trim()}`]);
      } else if (message.type === "MPOS_APP_RUNNING") {
        if (executionTimer.current !== null) window.clearTimeout(executionTimer.current);
        executionTimer.current = null;
        if (showcasePreviewRef.current) {
          const showcaseName = showcasePreviewRef.current;
          showcasePreviewRef.current = "";
          setShowcaseAction("");
          setRuntimeStatus(liveText(`${showcaseName} 正在真实 MicroPythonOS WASM 中运行`, `${showcaseName} is running in real MicroPythonOS WASM`));
          setLogs((items) => [...items, liveText(`[showcase] ${showcaseName} 已在 WASM 中启动 ✓`, `[showcase] ${showcaseName} started in WASM ✓`)]);
          return;
        }
        const previewUnsupported = previewUnsupportedRef.current;
        setRuntimeStatus(liveText("App 正在真实 MicroPythonOS WASM 中运行", "App is running in real MicroPythonOS WASM"));
        setCurrentStage(stages.length - 1);
        setLogs((items) => [...items, liveText("[preview] App 已在 MicroPythonOS WASM 中启动 ✓", "[preview] App started in MicroPythonOS WASM ✓")]);
        if (previewUnsupported.length > 0) {
          setLogs((items) => [...items, liveText(
            `[preview] 预览无法验证 ${previewUnsupported.join("、")}，需在真机确认`,
            `[preview] Preview cannot verify ${previewUnsupported.join(", ")}; confirm on a real device`,
          )]);
        }
        const savedSession = localStorage.getItem("mpos-session-id");
        if (savedSession) {
          void apiFetch(`${apiUrl}/api/sessions/${savedSession}/actions/preview-result`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              idempotency_key: previewResultKey(previewUnsupported.length > 0 ? "partial" : "success", savedSession, resultRef.current?.revision),
              result: previewUnsupported.length > 0 ? "partial" : "success",
              message: "MicroPythonOS WASM installed and launched the generated app through AppManager",
              unsupported_capabilities: previewUnsupported,
            }),
          }).then((response) => response.json()).then((session: SessionState) => {
            setSessionState(session);
            // The server owns the verdict (the capability gate usually makes this "blocked"), and the history list stays on the old status unless it is re-fetched.
            setStatus(session.status);
            refreshHistory();
          }).catch((error: unknown) => {
            // Say so instead of leaving a green "✓ running" on screen: if this
            // post never lands the session stays in waiting_preview forever,
            // and the preview it is waiting for has already happened.
            const reason = error instanceof Error ? error.message : String(error);
            setErrorMessage(liveText(
              `预览已成功，但结果没能回报后端（${reason}）。请点击重试，否则会话会一直停在等待预览。`,
              `The preview succeeded but its result did not reach the backend (${reason}). Retry, or the session stays stuck waiting for a preview.`,
            ));
            setLogs((items) => [...items, `[preview] result POST failed: ${reason}`]);
          });
        } else {
          setStatus("completed");
        }
      } else if (message.type === "MPOS_ERROR") {
        if (executionTimer.current !== null) window.clearTimeout(executionTimer.current);
        executionTimer.current = null;
        const detail = message.message || liveText("MicroPythonOS WASM 运行失败", "MicroPythonOS WASM failed");
        if (showcasePreviewRef.current) {
          showcasePreviewRef.current = "";
          setShowcaseAction("");
          setRuntimeStatus(detail);
          setToast(liveText("公开 App 模拟运行失败，请查看运行日志。", "Public app preview failed. Check the runtime log."));
          setLogs((items) => [...items, `[showcase] ${detail}`]);
          return;
        }
        // Repair first: a real code defect must not be misread as a hardware
        // limit just because the App also happens to need a camera. The repair
        // path is already capped at 2 attempts, so this cannot loop forever.
        if (repairHandler.current(detail)) {
          setRuntimeStatus(liveText(`发现兼容问题，DeepSeek 正在自动修复（${repairAttempts.current}/2）…`, `Compatibility issue found. DeepSeek is repairing it (${repairAttempts.current}/2)…`));
          setLogs((items) => [...items, liveText(`[repair] WASM 发现兼容错误，正在自动修复：${detail}`, `[repair] WASM compatibility error found; repairing: ${detail}`)]);
          return;
        }
        // Repairs are exhausted: this is a genuine defect, not a preview
        // limit. Reclassifying it as "partial" here hid real bugs.
        setRuntimeStatus(detail);
        setErrorMessage(detail);
        setCurrentStage(3);
        setStatus("failed");
        setLogs((items) => [...items, `[preview] ${detail}`]);
        const savedSession = localStorage.getItem("mpos-session-id");
        if (savedSession) {
          void apiFetch(`${apiUrl}/api/sessions/${savedSession}/actions/preview-result`, {
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
    showcasePreviewRef.current = "";
    lastRun.current = result.mpk_base64;
    setCurrentStage(3);
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
      setCurrentStage(2);
      setStatus("timeout");
      setLogs((items) => [...items, `[preview] ${detail}`]);
      const savedSession = localStorage.getItem("mpos-session-id");
      if (savedSession) {
        void apiFetch(`${apiUrl}/api/sessions/${savedSession}/actions/preview-result`, {
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
    }, wasmRuntimeOrigin);
  }, [result, wasmReady, sessionState]);

  const run = async (repair?: { runtimeError: string; previousCode: string }) => {
    if (!isPlatformActionAllowed(systemStatusConfirmed, publicSystemStatus.maintenance)) {
      setToast(publicSystemStatus.maintenance
        ? tr("系统正在升级，暂时不能生成 App。", "The system is being upgraded. App generation is temporarily unavailable.")
        : tr("系统状态暂不可用，暂时不能生成 App。", "System status is unavailable. App generation is temporarily disabled."));
      return;
    }
    if (
      !repair
      && billingAccount
      && !billingAccount.unlimited_credits
      && billingAccount.credits < billingAccount.generation_cost
    ) {
      setToast(tr("内测点数已用完，请联系项目管理员补充测试点数", "Your beta credits are depleted. Contact the project administrator."));
      return;
    }
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
      resetCapabilityEvidence();
    }
    lastRun.current = "";
    if (!repair) {
      repairAttempts.current = 0;
      setLogs([
        tr("[analysis] 已把你的需求发送到后端", "[analysis] Request sent to the backend"),
        tr("[generation] 正在等待所选 AI Provider 生成 MicroPythonOS 代码…", "[generation] Waiting for the selected AI provider to generate MicroPythonOS code…"),
      ]);
    } else {
      setLogs((items) => [...items, tr(`[repair] 第 ${repairAttempts.current}/2 次自动修复：正在让 DeepSeek 重写不兼容代码…`, `[repair] Automatic repair ${repairAttempts.current}/2: DeepSeek is rewriting incompatible code…`)]);
    }
    setCurrentStage(0);
    const controller = new AbortController();
    requestAbort.current = controller;
    const startedAt = Date.now();
    generationActivityAt.current = startedAt;
    generationActivitySnapshot.current = sessionState;
    let clientTimeoutKind: GenerationWaitTimeoutKind | null = null;
    let latestSession: SessionState | null = null;
    const idleTimer = window.setInterval(() => {
      clientTimeoutKind = getGenerationWaitTimeoutKind(
        Date.now(),
        startedAt,
        generationActivityAt.current,
        GENERATION_IDLE_TIMEOUT_MS,
        GENERATION_OVERALL_TIMEOUT_MS,
      );
      if (clientTimeoutKind) controller.abort();
    }, 1_000);
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
        const createResponse = await apiFetch(`${apiUrl}/api/sessions`, {
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
            ai_provider: aiProvider,
            targets: selectedTargets,
            capabilities: {
              file_operation: true,
              script_run: true,
              approval_request: true,
              permission_request: true,
              checkpoint_resume: true,
              cancellation: true,
              retry: true,
              timeout: true,
              desktop_preview: desktopAvailable,
              web_preview: true,
              physical_device: "serial" in navigator,
              browser_webserial: "serial" in navigator,
              serial_port_scan: "serial" in navigator,
              mpremote: false,
              firmware_flash: false,
              network_read: true,
              network_upload: false,
              upystore_publish: false,
            },
          }),
          signal: controller.signal,
        });
        if (!createResponse.ok) throw new Error(tr("无法创建生成会话", "Could not create session"));
        const created = await createResponse.json() as SessionState;
        latestSession = created;
        markGenerationActivity(created);
        sessionId = created.session_id;
        localStorage.setItem("mpos-session-id", sessionId);
        setSessionState(created);
        if (created.permissions.some((item) => item.required && item.decision === "pending")) {
          setStatus("blocked");
          setPermissionOpen(true);
          setLogs((items) => [...items, tr("[permission] 等待权限确认，可使用一键确认", "[permission] Waiting for approval; approve all is available")]);
          return;
        }
      } else if (continuing && !repair) {
        const revisionResponse = await apiFetch(`${apiUrl}/api/sessions/${sessionId}/revisions`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            idempotency_key: `revision-${crypto.randomUUID()}`,
            prompt,
            prompt_language: isZh ? "zh-CN" : "en-US",
            ai_provider: aiProvider,
          }),
          signal: controller.signal,
        });
        if (!revisionResponse.ok) throw new Error(tr("无法创建新版本", "Could not create revision"));
        const revised = await revisionResponse.json() as SessionState;
        latestSession = revised;
        markGenerationActivity(revised);
        setSessionState(revised);
        setContinuing(false);
      }
      openEventStream(sessionId);
      const actionResponse = await apiFetch(`${apiUrl}/api/sessions/${sessionId}/${repair ? "retry" : "actions/run"}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          idempotency_key: `${repair ? "repair" : "generate"}-${crypto.randomUUID()}`,
          previous_code: repair?.previousCode,
          runtime_error: repair?.runtimeError,
          ai_provider: aiProvider,
        }),
        signal: controller.signal,
      });
      if (!actionResponse.ok) {
        const failure = await actionResponse.json().catch(() => null);
        if (actionResponse.status === 402) {
          await refreshBilling().catch(() => undefined);
          throw new Error(
            failure?.detail?.message
            || tr("内测点数已用完，请联系项目管理员", "Beta credits are depleted. Contact the project administrator."),
          );
        }
        throw new Error(tr("后端拒绝启动生成任务", "Backend refused to start generation"));
      }
      if (!repair) await refreshBilling().catch(() => undefined);

      let session = await actionResponse.json() as SessionState;
      latestSession = session;
      markGenerationActivity(session);
      while (!["waiting_preview", "waiting_device", "completed", "failed", "blocked", "cancelled", "timeout"].includes(session.status)) {
        await new Promise((resolve) => window.setTimeout(resolve, 700));
        const poll = await apiFetch(`${apiUrl}/api/sessions/${sessionId}`, { signal: controller.signal });
        if (!poll.ok) throw new Error(tr("读取会话状态失败", "Could not read session state"));
        session = await poll.json() as SessionState;
        latestSession = session;
        markGenerationActivity(session);
        setSessionState(session);
        setCurrentStage(stageIndexForCheckpoint(session.checkpoint_id));
      }
      setSessionState(session);
      refreshHistory();
      if (session.status === "failed" || session.status === "blocked" || session.status === "timeout") {
        const failure = session.last_error;
        if (failure) setCurrentStage(stageIndexForError(failure.stage));
        throw new Error(failure ? `[${failure.code}] ${failure.message}` : tr("生成失败", "Generation failed"));
      }
      if (session.status === "cancelled") throw new Error(tr("任务已取消", "Task cancelled"));
      if (session.status === "waiting_device") {
        setLogs((items) => [...items, tr(
          "[deploy] 生成与打包完成，等待你连接 ESP32 并安装生成的 MPK。",
          "[deploy] Build and packaging are complete. Connect the ESP32 and install the generated MPK.",
        )]);
      }
      const generated = session.generation;
      if (!generated) throw new Error(tr("会话完成但没有生成产物", "Session finished without generated artifacts"));
      setCurrentStage(3);
      setLogs((items) => [
        ...items,
        tr(`[generation] ${generated.provider || aiProvider} · ${generated.model} 已返回真实代码 ✓`, `[generation] ${generated.provider || aiProvider} · ${generated.model} returned real code ✓`),
        tr("[validation] Python 语法和基础安全检查通过 ✓", "[validation] Python syntax and safety checks passed ✓"),
        tr(`[validation] 已生成 ${generated.acceptance_tests.length} 项功能验收，并将在 WASM 中执行 self_test ✓`, `[validation] Created ${generated.acceptance_tests.length} acceptance checks; self_test will run in WASM ✓`),
      ]);
      setCurrentStage(4);
      setLogs((items) => [
        ...items,
        tr(`[package] 已生成 ${generated.mpk_filename} ✓`, `[package] Created ${generated.mpk_filename} ✓`),
        tr("[preview] 正在等待 MicroPythonOS WASM 执行生成代码…", "[preview] Waiting for MicroPythonOS WASM to run the generated code…"),
      ]);
      setResult(generated);
      resultRef.current = generated;
      setCurrentStage(stageIndexForSession(session));
      if (session.status === "completed") setStatus("completed");
      if (session.status === "waiting_device") setStatus("waiting_device");
      setActiveTab("preview");
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError" && requestAbort.current !== controller) {
        return;
      }
      const backendTimedOut = latestSession?.status === "timeout"
        || Boolean(latestSession?.last_error?.code.includes("TIMEOUT"));
      const clientTimedOut = error instanceof DOMException
        && error.name === "AbortError"
        && clientTimeoutKind !== null;
      const message = clientTimedOut
        ? clientTimeoutKind === "idle"
          ? tr(
              `后台任务仍在运行，但 ${Math.round(GENERATION_IDLE_TIMEOUT_MS / 1000)} 秒没有新状态。任务未取消，可继续等待。`,
              `The backend task is still running but reported no progress for ${Math.round(GENERATION_IDLE_TIMEOUT_MS / 1000)} seconds. It was not cancelled; you can keep waiting.`,
            )
          : tr(
              `已达到 ${Math.round(GENERATION_OVERALL_TIMEOUT_MS / 60000)} 分钟前端等待上限。后台任务未取消，可稍后继续等待。`,
              `The ${Math.round(GENERATION_OVERALL_TIMEOUT_MS / 60000)}-minute frontend wait limit was reached. The backend task was not cancelled; you can resume later.`,
            )
        : error instanceof Error ? error.message : tr("未知错误", "Unknown error");
      setErrorMessage(message);
      setStatus(clientTimedOut || backendTimedOut ? "timeout" : "failed");
      setLogs((items) => [...items, clientTimedOut || backendTimedOut
        ? tr(`[等待超时] ${message}`, `[wait timeout] ${message}`)
        : tr(`[失败] ${message}`, `[failed] ${message}`)]);
    } finally {
      window.clearInterval(idleTimer);
      if (requestAbort.current === controller) requestAbort.current = null;
      repairing.current = false;
    }
  };

  const decidePermission = async (permission: Permission, decision: "allow_once" | "deny") => {
    if (permissionBusy || permission.decision !== "pending") return;
    setPermissionBusy(permission.permission_id);
    try {
      const response = await apiFetch(`${apiUrl}/api/permissions/${permission.permission_id}/decision`, {
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

  const allowAllPermissions = async () => {
    if (permissionBusy || !sessionState) return;
    const pending = sessionState.permissions.filter(
      (item) => item.required && item.decision === "pending",
    );
    if (!pending.length) {
      setPermissionOpen(false);
      void run();
      return;
    }
    setPermissionBusy("__all__");
    try {
      // apiFetch, not fetch: this endpoint is behind the session cookie, and a
      // bare fetch omits credentials, so approve-all 401s on every split
      // frontend/backend deployment and the run can never start.
      const response = await apiFetch(
        `${apiUrl}/api/sessions/${sessionState.session_id}/permissions/allow-all`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            idempotency_key: `permission-all-${crypto.randomUUID()}`,
          }),
        },
      );
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(
          typeof payload?.detail === "string"
            ? payload.detail
            : tr("一键确认权限失败", "Could not approve all permissions"),
        );
      }
      applySession(payload as SessionState);
      setPermissionOpen(false);
      setToast(tr(`已一次确认 ${pending.length} 项权限`, `Approved ${pending.length} permissions`));
      window.setTimeout(() => void run(), 0);
    } catch (error) {
      setToast(error instanceof Error ? error.message : tr("一键确认权限失败", "Could not approve all permissions"));
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
      void apiFetch(`${apiUrl}/api/sessions/${savedSession}/cancel`, {
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

  const download = (filename: string, content: string) =>
    downloadText(filename, content, tr, setToast);

  const downloadArtifact = (artifact: Artifact) => downloadArtifactFile(
    artifact, apiFetch, `${apiUrl}/api/artifacts/${artifact.id}`, tr, setToast,
  );

  const uploadScreenshot = async (file: File) => {
    const sessionId = sessionState?.session_id;
    if (!sessionId) {
      setToast(tr("请先生成 App", "Generate an app first"));
      return;
    }
    if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
      setToast(tr("只支持 PNG、JPEG 或 WebP", "Only PNG, JPEG, or WebP is supported"));
      return;
    }
    if (file.size > 10 * 1024 * 1024) {
      setToast(tr("截图不能超过 10 MB", "Screenshot must be 10 MB or smaller"));
      return;
    }
    setScreenshotBusy(true);
    try {
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ""));
        reader.onerror = () => reject(reader.error || new Error("File read failed"));
        reader.readAsDataURL(file);
      });
      const response = await apiFetch(`${apiUrl}/api/sessions/${sessionId}/screenshots`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          idempotency_key: `screenshot-${crypto.randomUUID()}`,
          filename: file.name,
          media_type: file.type,
          data_base64: dataUrl.split(",", 2)[1] || "",
          source: "manual",
        }),
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || tr("截图上传失败", "Screenshot upload failed"));
      }
      const updated = await response.json() as SessionState;
      setSessionState(updated);
      // The screenshot is often the last missing publish artifact, so this call
      // is what completes the session. Without mirroring the server's verdict
      // the UI keeps showing "blocked" and a retry button for finished work.
      setStatus(updated.status);
      setCurrentStage(stageIndexForSession(updated));
      refreshHistory();
      setToast(tr("截图已加入发布材料", "Screenshot added to publishing materials"));
    } catch (error) {
      setToast(error instanceof Error ? error.message : tr("截图上传失败", "Screenshot upload failed"));
    } finally {
      setScreenshotBusy(false);
    }
  };

  const scanDevices = async () => {
    if (!WebSerialDeviceClient.isSupported()) {
      setDeviceMessage(tr(
        "当前浏览器不支持串口连接。请在 Chrome、Edge 或 Brave 中打开本页面。",
        "This browser does not support serial connections. Open this page in Chrome, Edge, or Brave.",
      ));
      return;
    }
    setDeviceError("");
    setDeviceLogs("");
    const client = new WebSerialDeviceClient({
      onData: (text) => setDeviceLogs((previous) => `${previous}${text}`.slice(-100_000)),
      onState: (nextState, detail) => {
        setDeviceState(nextState);
        setSerialConnected(nextState === "connected");
        setDeviceConnectionDetail(nextState === "connected" ? detail || "" : "");
        if (nextState === "connected") {
          setDeviceError("");
          setDeviceMessage(tr(
            `已连接 ${detail || "USB 串口设备"}。`,
            `Connected to ${detail || "USB serial device"}.`,
          ));
        } else if (nextState === "error") {
          setDeviceError(detail || tr("串口连接异常", "Serial connection error"));
        }
      },
    });
    try {
      serialClientRef.current = client;
      deviceInfoRef.current = await client.connect();
    } catch (error) {
      serialClientRef.current = null;
      deviceInfoRef.current = {};
      setDeviceState("error");
      setSerialConnected(false);
      const reason = error instanceof Error ? error.message : String(error);
      if (error instanceof DOMException && error.name === "NotFoundError") {
        setDeviceMessage(tr("没有选择设备。请重试并选择 ESP32 对应的串口设备。", "No device selected. Try again and choose the serial device for your ESP32."));
      } else {
        setDeviceError(reason);
        setDeviceMessage(tr(
          `连接失败：${reason}。请关闭占用所选串口的其他工具后重试。`,
          `Connection failed: ${reason}. Close any other tool using the selected serial port and try again.`,
        ));
      }
    }
  };

  const disconnectDevice = async () => {
    try {
      await serialClientRef.current?.disconnect();
    } catch {
      // The USB cable may already be disconnected.
    } finally {
      serialClientRef.current = null;
      setSerialConnected(false);
      setDeviceState("disconnected");
      resetCapabilityEvidence();
      setDeviceBusy("");
      setDeviceProgress(0);
      setDeviceMessage(tr("设备已断开。", "Device disconnected."));
    }
  };

  const recordDeviceResult = async (
    deviceResult: "probe_success" | "install_success" | "launch_success" | "failed",
    message: string,
    installedPath?: string,
    // Probing and reporting happen in the same tick, so the state setters have
    // not re-rendered yet; the caller passes what it just measured.
    freshEvidence?: { hardwareId: string; probes: CapabilityProbeOutcome[] },
  ) => {
    const sessionId = localStorage.getItem("mpos-session-id");
    if (!sessionId) return;
    try {
      const response = await apiFetch(`${apiUrl}/api/sessions/${sessionId}/devices/result`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          idempotency_key: `device-result-${deviceResult}-${crypto.randomUUID()}`,
          result: deviceResult,
          // Diagnostics only, and only what the device actually reported.
          hardware_id: freshEvidence?.hardwareId ?? detectedHardwareId,
          runtime_capability_results: freshEvidence?.probes ?? capabilityProbes,
          usb_vendor_id: deviceInfoRef.current.usbVendorId,
          usb_product_id: deviceInfoRef.current.usbProductId,
          installed_path: installedPath,
          transport: "webserial",
          message,
          log_excerpt: deviceLogs.slice(-20_000),
        }),
      });
      if (response.ok) {
        setSessionState(await response.json() as SessionState);
        refreshHistory();
      }
    } catch {
      setLogs((items) => [...items, tr(
        "[device] 真机结果暂时无法写入后端审计日志",
        "[device] Could not persist the device result to the backend audit log",
      )]);
    }
  };

  const capabilityAnalysis = sessionState?.capability_analysis ?? null;

  // The postMessage handler is installed once and cannot read sessionState, and
  // applySession only runs on restore — so mirror it here, where every session
  // update lands, instead of on one path that the normal run() flow skips.
  useEffect(() => {
    previewUnsupportedRef.current =
      sessionState?.capability_analysis?.web_preview_unsupported ?? [];
  }, [sessionState]);

  // Runs the contract probes on the authorised device. A probe that cannot be
  // evaluated stays "unknown"; it is never downgraded into "hardware absent".
  const probeDeviceCapabilities = async () => {
    if (!isPlatformActionAllowed(systemStatusConfirmed, publicSystemStatus.maintenance)) {
      setDeviceError(tr("系统升级期间暂不允许设备操作。", "Device actions are unavailable during maintenance."));
      return;
    }
    const client = serialClientRef.current;
    if (!client?.connected || !capabilityAnalysis) return;
    // Shares deviceBusy with installs and app control: probing writes REPL
    // command lines, and an in-flight MPK transfer reads an exact byte count
    // from the same stream, so an interleaved probe silently corrupts it.
    if (deviceBusy) {
      setDeviceError(tr("设备正忙，请等待当前操作完成。", "The device is busy; wait for the current operation to finish."));
      return;
    }
    setDeviceError("");
    setDeviceBusy("capability-probe");
    setCapabilityProbing(true);
    try {
      const execute: DeviceExecute = (source, timeoutMs) => client.execute(source, timeoutMs);
      const hardwareId = await readHardwareId(execute);
      const probes = await probeCapabilities(execute, capabilityAnalysis.capability_contracts);
      setDetectedHardwareId(hardwareId);
      setCapabilityProbes(probes);
      // The completion gate needs this evidence server-side. Leaving it in
      // browser memory made it reach the backend only as an incidental payload
      // of some later install or launch — and never at all if the user probed
      // and stopped there.
      await recordDeviceResult(
        "probe_success",
        tr("能力探测完成。", "Capability probing finished."),
        undefined,
        { hardwareId, probes },
      );
    } catch (error) {
      setDeviceError(tr(
        `能力探测失败：${error instanceof Error ? error.message : String(error)}`,
        `Capability probing failed: ${error instanceof Error ? error.message : String(error)}`,
      ));
    } finally {
      setCapabilityProbing(false);
      setDeviceBusy("");
    }
  };

  const runDeviceAction = async (label: string, action: (client: WebSerialDeviceClient) => Promise<void>) => {
    if (!isPlatformActionAllowed(systemStatusConfirmed, publicSystemStatus.maintenance)) {
      setDeviceError(publicSystemStatus.maintenance
        ? tr("系统升级期间暂不允许设备部署。", "Device deployment is unavailable during maintenance.")
        : tr("系统状态暂不可用，设备操作已禁用。", "System status is unavailable. Device actions are disabled."));
      return;
    }
    const client = serialClientRef.current;
    if (!client?.connected) {
      setDeviceError(tr("请先连接 ESP32。", "Connect the ESP32 first."));
      return;
    }
    setDeviceBusy(label);
    setDeviceError("");
    try {
      await action(client);
    } catch (error) {
      let finalError: unknown = error;
      if (
        ["probe", "install", "run", "restart"].includes(label)
        && WebSerialDeviceClient.isDisconnectError(error)
      ) {
        setDeviceMessage(tr(
          "设备刚刚重启或短暂断开，正在自动重连并重试…",
          "The device restarted or briefly disconnected. Reconnecting and retrying…",
        ));
        const reconnected = await client.reconnect(15_000);
        if (reconnected) {
          try {
            await action(client);
            return;
          } catch (retryError) {
            finalError = retryError;
          }
        }
      }
      const reason = finalError instanceof Error ? finalError.message : String(finalError);
      setDeviceError(reason);
      setDeviceLogs((previous) => `${previous}\n[ERROR] ${reason}\n`.slice(-100_000));
      if (label === "probe") {
        setMposDetected(false);
      }
      if (["probe", "install", "run", "restart"].includes(label)) {
        await recordDeviceResult("failed", reason);
      }
    } finally {
      setDeviceBusy("");
    }
  };

  const fetchShowcaseMpkBytes = (item: ShowcaseApp) => fetchVerifiedShowcaseMpk(
    item.mpkUrl,
    item.sha256,
    window.location.href,
  );

  const fetchShowcaseMpkBase64 = async (item: ShowcaseApp) => (
    encodeShowcaseMpk(await fetchShowcaseMpkBytes(item))
  );

  const previewShowcaseApp = async (item: ShowcaseApp) => {
    if (showcaseAction) return;
    if (!wasmReady || !iframeRef.current?.contentWindow) {
      setToast(tr("MicroPythonOS WASM 尚未就绪，请稍后重试。", "MicroPythonOS WASM is not ready yet. Try again shortly."));
      return;
    }
    setShowcaseAction(`preview:${item.fullname}`);
    setRuntimeStatus(tr(`正在下载并模拟运行 ${item.name}…`, `Downloading and previewing ${item.name}…`));
    setActiveTab("preview");
    try {
      const mpkBase64 = await fetchShowcaseMpkBase64(item);
      showcasePreviewRef.current = item.name;
      if (executionTimer.current !== null) window.clearTimeout(executionTimer.current);
      executionTimer.current = window.setTimeout(() => {
        showcasePreviewRef.current = "";
        setShowcaseAction("");
        setRuntimeStatus(tr("公开 App WASM 模拟运行超时", "Public app WASM preview timed out"));
      }, 60_000);
      iframeRef.current.contentWindow.postMessage(
        buildShowcaseRunMessage(item.fullname, mpkBase64),
        wasmRuntimeOrigin,
      );
    } catch (error) {
      showcasePreviewRef.current = "";
      setShowcaseAction("");
      const message = error instanceof Error
        ? error.message
        : tr("公开 App MPK 下载失败", "Could not download the public app MPK");
      setRuntimeStatus(message);
      setToast(message);
    }
  };

  const deployShowcaseApp = async (item: ShowcaseApp) => {
    if (showcaseAction) return;
    if (!isPlatformActionAllowed(systemStatusConfirmed, publicSystemStatus.maintenance)) {
      const message = systemStatusConfirmed
        ? tr("系统升级期间暂不允许设备部署。", "Device deployment is unavailable during maintenance.")
        : tr("正在确认系统状态，请稍候。", "Checking system status. Please wait.");
      setDeviceError(message);
      setToast(message);
      return;
    }
    if (!serialClientRef.current?.connected) {
      const message = tr("请先在上方连接 ESP32，再点击“下载并部署”。", "Connect the ESP32 above before choosing Download and deploy.");
      setDeviceError(message);
      setDeviceMessage(message);
      setToast(message);
      return;
    }
    setShowcaseAction(`deploy:${item.fullname}`);
    try {
      await runDeviceAction("install", async (client) => {
        setDeviceProgress(0);
        setDeviceMessage(tr(`正在下载并部署 ${item.name}…`, `Downloading and deploying ${item.name}…`));
        const mpkBase64 = await fetchShowcaseMpkBase64(item);
        await client.installMpkBase64(item.fullname, mpkBase64, setDeviceProgress);
        setDeviceProgress(100);
        setDeviceMessage(tr(`${item.name} 已安装到设备。`, `${item.name} was installed on the device.`));
      });
    } finally {
      setShowcaseAction("");
    }
  };

  const downloadShowcaseApp = async (item: ShowcaseApp) => {
    if (showcaseAction) return;
    setShowcaseAction(`download:${item.fullname}`);
    try {
      const bytes = await fetchShowcaseMpkBytes(item);
      const objectUrl = URL.createObjectURL(new Blob([bytes], { type: "application/octet-stream" }));
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = item.mpkUrl.split("/").pop() || `${item.fullname}.mpk`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(objectUrl);
    } catch (error) {
      const message = error instanceof Error
        ? error.message
        : tr("公开 App MPK 下载失败", "Could not download the public app MPK");
      setToast(message);
    } finally {
      setShowcaseAction("");
    }
  };

  const probeDevice = () => runDeviceAction("probe", async (client) => {
    setDeviceMessage(tr("正在检测 MicroPythonOS…", "Probing MicroPythonOS…"));
    await client.execute([
      "import sys",
      "import mpos",
      "print('MicroPython:', sys.version)",
      "print('MicroPythonOS: ready')",
    ].join("\n"));
    const message = tr("MicroPythonOS 已就绪，可以安装和运行 App。", "MicroPythonOS is ready for app install and launch.");
    setMposDetected(true);
    setDeviceMessage(message);
    await recordDeviceResult("probe_success", message);
  });

  const sendDeviceCommand = () => {
    const command = deviceCommand.trim();
    if (!command) return;
    void runDeviceAction("command", async (client) => {
      setDeviceLogs((previous) => `${previous}\n>>> ${command}\n`.slice(-100_000));
      await client.sendLine(command);
      setDeviceCommand("");
    });
  };

  const interruptDevice = () => runDeviceAction("interrupt", async (client) => {
    await client.interrupt();
    setDeviceMessage(tr("已向设备发送 Ctrl+C。", "Sent Ctrl+C to the device."));
  });

  const installGeneratedMpk = () => runDeviceAction("install", async (client) => {
    if (!result) throw new Error(tr("请先生成 App 和 MPK。", "Generate an App and MPK first."));
    const writePermission = sessionState?.permissions.find((item) => item.permission_type === "device_write");
    if (writePermission && writePermission.decision !== "allow_once") {
      setPermissionOpen(true);
      throw new Error(tr("请先允许本次设备写入权限，然后再次点击安装。", "Allow device write for this session, then click install again."));
    }
    setDeviceProgress(0);
    setDeviceMessage(tr("正在高速传输并安装 MPK…", "Fast-streaming and installing the MPK…"));
    await client.installMpkBase64(result.package_name, result.mpk_base64, setDeviceProgress);
    setDeviceProgress(100);
    const message = tr(
      `安装成功：${result.package_name}`,
      `Installed successfully: ${result.package_name}`,
    );
    setDeviceMessage(message);
    await recordDeviceResult("install_success", message, `apps/${result.package_name}`);
  });

  const runInstalledApp = () => runDeviceAction("run", async (client) => {
    const appName = result?.package_name || packageName;
    await client.execute([
      "from mpos import AppManager",
      "AppManager.refresh_apps()",
      `assert AppManager.is_installed_by_name(${JSON.stringify(appName)}), 'App is not installed; click Install generated MPK first'`,
      `started = AppManager.start_app(${JSON.stringify(appName)})`,
      "assert started, 'MicroPythonOS could not start the installed App'",
      "print('Started:', started)",
    ].join("\n"), 45_000);
    const message = tr(`已启动 ${appName}`, `Started ${appName}`);
    setDeviceMessage(message);
    await recordDeviceResult("launch_success", message);
  });

  const stopInstalledApp = () => runDeviceAction("stop", async (client) => {
    await client.execute([
      "from mpos.ui.view import finish_current_activity",
      "finish_current_activity()",
      "print('Foreground activity stopped')",
    ].join("\n"), 30_000);
    setDeviceMessage(tr("当前 App 已停止。", "The current App has stopped."));
  });

  const restartInstalledApp = () => runDeviceAction("restart", async (client) => {
    const appName = result?.package_name || packageName;
    await client.execute([
      "from mpos.ui.view import finish_current_activity",
      "finish_current_activity()",
      "from mpos import AppManager",
      "AppManager.refresh_apps()",
      `assert AppManager.is_installed_by_name(${JSON.stringify(appName)}), 'App is not installed; click Install generated MPK first'`,
      `started = AppManager.start_app(${JSON.stringify(appName)})`,
      "assert started, 'MicroPythonOS could not restart the installed App'",
      "print('Restarted:', started)",
    ].join("\n"), 45_000);
    setDeviceMessage(tr(`已重启 ${appName}`, `Restarted ${appName}`));
  });

  const desktopScreenshot = sessionState?.artifacts.find((item) => item.role === "desktop_screenshot");

  const handleWasmFrameLoad = () => {
    lastRun.current = "";
    setWasmReady(false);
    setRuntimeStatus(tr("正在启动 MicroPythonOS WASM…", "Starting MicroPythonOS WASM…"));
    iframeRef.current?.contentWindow?.postMessage({ source: "mpos-builder", type: "PING" }, wasmRuntimeOrigin);
    window.setTimeout(() => iframeRef.current?.contentWindow?.postMessage({ source: "mpos-builder", type: "PING" }, wasmRuntimeOrigin), 1500);
    if (wasmTimer.current !== null) window.clearTimeout(wasmTimer.current);
    wasmTimer.current = window.setTimeout(() => {
      const detail = tr("MicroPythonOS WASM 启动超时，请刷新页面后重试。", "MicroPythonOS WASM startup timed out. Refresh and retry.");
      setRuntimeStatus(detail);
      setErrorMessage(detail);
      setStatus("timeout");
    }, 120000);
  };

  const downloadMpk = () => {
    if (!result) return;
    void downloadMpkFile(result.mpk_base64, result.mpk_filename, tr, setToast);
  };

  const requestRequirementHelp = async (
    messages: RequirementMessage[],
    finalize = false,
  ) => {
    setRequirementBusy(true);
    setRequirementError("");
    try {
      const response = await apiFetch(`${apiUrl}/api/requirements/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          locale: isZh ? "zh-CN" : "en-US",
          draft_prompt: prompt.trim(),
          messages,
          finalize,
        }),
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(
          typeof payload?.detail === "string"
            ? payload.detail
            : tr("AI 需求助手暂时不可用", "The AI requirement assistant is unavailable"),
        );
      }
      const resultPayload = payload as RequirementChatResult;
      setRequirementResult(resultPayload);
      const lastMessage = messages[messages.length - 1];
      const repeatsLastAssistant =
        lastMessage?.role === "assistant"
        && lastMessage.content.trim() === resultPayload.assistant_message.trim();
      setRequirementMessages(
        finalize && resultPayload.ready || repeatsLastAssistant
          ? messages
          : [
              ...messages,
              { role: "assistant", content: resultPayload.assistant_message },
            ],
      );
    } catch (error) {
      setRequirementError(
        error instanceof Error
          ? error.message
          : tr("需求对话失败，请重试", "Requirement chat failed. Please retry."),
      );
    } finally {
      setRequirementBusy(false);
    }
  };

  const startRequirementChat = () => {
    if (prompt.trim().length < 3) {
      setToast(tr("先写一句你想做什么 App", "First describe the app you want to build"));
      return;
    }
    const initialMessages: RequirementMessage[] = [
      { role: "user", content: prompt.trim() },
    ];
    setRequirementMessages(initialMessages);
    setRequirementResult(null);
    setRequirementInput("");
    setRequirementError("");
    setRequirementOpen(true);
    void requestRequirementHelp(initialMessages);
  };

  const sendRequirementAnswer = () => {
    const answer = requirementInput.trim();
    if (!answer || requirementBusy) return;
    const nextMessages: RequirementMessage[] = [
      ...requirementMessages,
      { role: "user", content: answer },
    ];
    setRequirementMessages(nextMessages);
    setRequirementInput("");
    void requestRequirementHelp(nextMessages);
  };

  const applyRefinedRequirement = () => {
    if (!requirementResult?.refined_prompt) return;
    setPrompt(requirementResult.refined_prompt);
    setRequirementOpen(false);
    setToast(tr("已把 AI 整理的完整需求填入输入框", "The refined requirement was added to the prompt"));
  };

  if (publicSystemStatus.maintenance) {
    return <MaintenanceScreen language={language} systemStatus={publicSystemStatus} />;
  }

  if (authStatus !== "signed_in") {
    return (
      <AuthScreen
        language={language}
        authStatus={authStatus}
        authMode={authMode}
        authUsername={authUsername}
        authPassword={authPassword}
        authError={authError}
        authBusy={authBusy}
        onLanguageToggle={() => setLanguage(isZh ? "en" : "zh")}
        onModeToggle={() => { setAuthMode(authMode === "login" ? "register" : "login"); setAuthError(""); }}
        onUsernameChange={setAuthUsername}
        onPasswordChange={setAuthPassword}
        onSubmit={submitAuth}
      />
    );
  }

  return (
    <div className="page">
      <header>
        <div className="brand"><span>BM</span><div><strong>Blockless-Make-APP</strong><small>{tr("MicroPythonOS AI App 生成与分发平台", "MicroPythonOS AI app creation and distribution")}</small></div></div>
        <div className="header-actions">
          <span className="user-chip">
            {billingAccount?.username}
            {billingAccount?.role === "superadmin" ? ` · ${tr("管理员", "Admin")}` : ""}
          </span>
          <button className="credits-button" onClick={() => setToast(
            billingAccount?.unlimited_credits
              ? tr("管理员账户不受内测点数限制", "Administrator accounts have unlimited beta credits.")
              : tr("当前为免费内测点数，不提供在线充值", "Free beta credits; online payments are not enabled."),
          )}>
            <span>◆</span>{billingAccount?.unlimited_credits ? "∞" : (billingAccount?.credits ?? 50)} {tr("点", "credits")}
          </button>
          <button className="language-button" onClick={() => setLanguage(isZh ? "en" : "zh")} aria-label={tr("切换为英文", "Switch to Chinese")}>
            {isZh ? "English" : "中文"}
          </button>
          <button className="logout-button" onClick={() => void logout()}>{tr("退出", "Sign out")}</button>
          <div className={`run-state ${status}`}><i />{status === "running" ? tr("生成中", "Generating") : status === "waiting_device" ? tr("等待设备安装", "Waiting for device") : status === "blocked" ? tr("等待授权", "Permission required") : status === "cancelled" ? tr("已取消", "Cancelled") : status === "timeout" ? tr("已超时", "Timed out") : status === "failed" ? tr("生成失败", "Failed") : status === "completed" ? tr("已完成", "Completed") : tr("系统就绪", "Ready")}</div>
        </div>
      </header>

      <main>
        <section className="hero">
          <p>{tr("说出想法 → 浏览器预览 → 真机运行 → 发布分享", "Idea → Preview → Device → Share")}</p>
          <h1>{tr("让创客 App 先跑起来，", "Make, preview, deploy, and share ")}<em>{tr("再传播出去。", "MicroPythonOS apps from the browser.")}</em></h1>
        </section>

        <div className="workspace" id="builder">
          <section className="card input-card">
            <label htmlFor="prompt">{tr("你想做什么 App？", "What app do you want to build?")}</label>
            <textarea id="prompt" value={prompt} disabled={status === "running"} onChange={(event) => setPrompt(event.target.value)} />
            <div className="requirement-entry">
              <button type="button" disabled={status === "running" || requirementBusy} onClick={startRequirementChat}>
                <span>✦</span>{tr("和 AI 聊聊，帮我完善需求", "Chat with AI to refine the idea")}
              </button>
              <small>{tr("AI 会一次问一个关键问题，整理好后再生成 App", "AI asks one key question at a time before generation")}</small>
            </div>
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
                <label>AI Provider
                  <select
                    value={aiProvider}
                    disabled={providerStatus !== "ready"}
                    onChange={(event) => {
                      if (isAiProviderId(event.target.value)) setAiProvider(event.target.value);
                    }}
                  >
                    {providerMetadata.map((provider) => (
                      <option
                        value={provider.id}
                        disabled={!provider.configured}
                        title={provider.model || undefined}
                        key={provider.id}
                      >
                        {provider.label}{provider.configured ? provider.model ? ` · ${provider.model}` : "" : tr(" · 未配置", " · Not configured")}
                      </option>
                    ))}
                  </select>
                  <small>{providerStatus === "loading"
                    ? tr("正在读取可用 Provider…", "Loading available providers…")
                    : providerStatus === "error"
                      ? tr("无法读取 Provider 配置，暂时不能生成。", "Provider metadata is unavailable. Generation is temporarily disabled.")
                      : selectedProvider?.configured
                        ? tr("仅发送 Provider ID；密钥始终保留在后端。", "Only the provider ID is sent; credentials remain on the backend.")
                        : tr("此 Provider 尚未配置。", "This provider is not configured.")}</small>
                </label>
              </div>
            </details>

            <div className="targets">
              <label><input type="checkbox" checked={desktopTarget} onChange={(event) => setDesktopTarget(event.target.checked)} /> Desktop smoke test<small>{tr("当前主机不支持时会明确标为跳过", "Marked skipped when unavailable on this host")}</small></label>
              <label><input type="checkbox" checked={webTarget} onChange={(event) => setWebTarget(event.target.checked)} /> Web/WASM preview<small>{tr("在真实 MicroPythonOS WASM 中运行", "Run in real MicroPythonOS WASM")}</small></label>
              <label><input type="checkbox" checked={physicalTarget} onChange={(event) => setPhysicalTarget(event.target.checked)} /> Physical device deploy<small>{tr("未检测到设备时进入系统安装引导", "Shows OS installation guidance if no device is found")}</small></label>
              <label><input type="checkbox" checked={packageTarget} onChange={(event) => setPackageTarget(event.target.checked)} /> Package only<small>{tr("生成可以下载的 _rN.mpk", "Create a downloadable _rN.mpk")}</small></label>
            </div>
            <div className="device-guide">
              <div><strong>{tr("要在真实设备上运行？", "Want to run on a real device?")}</strong><span>{tr("请使用 Chrome、Edge 或 Brave，点击连接后选择 ESP32 对应的串口设备。", "Use Chrome, Edge, or Brave, then choose the serial device for your ESP32.")}</span></div>
              <button onClick={() => void (serialConnected ? disconnectDevice() : scanDevices())}>
                {serialConnected ? tr("断开设备", "Disconnect") : tr("连接 ESP32", "Connect ESP32")}
              </button>
              <a href="https://install.micropythonos.com/" target="_blank" rel="noreferrer">{tr("打开系统安装器", "Open OS installer")}</a>
            </div>
            {deviceMessage && <small className="device-message">{deviceMessage}</small>}
            {(serialConnected || deviceState !== "disconnected") && (
              <section className="device-console">
                <div className="device-console-head">
                  <div>
                    <span className={`device-dot ${deviceState}`} />
                    <strong>{tr("ESP32 设备控制台", "ESP32 device console")}</strong>
                    <small>{deviceState === "connecting"
                      ? tr("正在连接", "Connecting")
                      : deviceState === "connected"
                        ? `${deviceConnectionDetail || tr("USB 串口设备", "USB serial device")} · ${tr("已连接", "connected")}`
                        : tr("连接异常", "Connection error")}</small>
                  </div>
                  <button disabled={!serialConnected || Boolean(deviceBusy)} onClick={() => void probeDevice()}>
                    {deviceBusy === "probe" ? tr("检测中…", "Probing…") : tr("检测系统", "Probe OS")}
                  </button>
                </div>

                {deviceError && <div className="device-error">{deviceError}</div>}
                {deviceBusy === "install" && (
                  <div className="device-progress">
                    <div><span>{tr("上传并安装 MPK", "Upload and install MPK")}</span><b>{deviceProgress}%</b></div>
                    <progress max="100" value={deviceProgress} />
                  </div>
                )}

                <div className="device-actions">
                  <button className="device-primary" disabled={!serialConnected || !result || Boolean(deviceBusy)} onClick={() => void installGeneratedMpk()}>
                    {deviceBusy === "install" ? tr("正在安装…", "Installing…") : tr("安装生成的 MPK", "Install generated MPK")}
                  </button>
                  <button disabled={!serialConnected || Boolean(deviceBusy)} onClick={() => void runInstalledApp()}>{tr("运行 App", "Run App")}</button>
                  <button disabled={!serialConnected || Boolean(deviceBusy)} onClick={() => void stopInstalledApp()}>{tr("停止 App", "Stop App")}</button>
                  <button disabled={!serialConnected || Boolean(deviceBusy)} onClick={() => void restartInstalledApp()}>{tr("重启 App", "Restart App")}</button>
                  <button disabled={!serialConnected || Boolean(deviceBusy)} onClick={() => void interruptDevice()}>{tr("中断命令", "Interrupt")}</button>
                </div>

                <div className="device-terminal-head">
                  <strong>{tr("实时串口日志", "Live serial log")}</strong>
                  <button onClick={() => setDeviceLogs("")}>{tr("清空", "Clear")}</button>
                </div>
                <pre className="device-terminal">{deviceLogs || tr("等待设备输出…", "Waiting for device output…")}</pre>
                <form className="device-command" onSubmit={(event) => { event.preventDefault(); sendDeviceCommand(); }}>
                  <span>&gt;&gt;&gt;</span>
                  <input
                    value={deviceCommand}
                    onChange={(event) => setDeviceCommand(event.target.value)}
                    placeholder={tr("输入一行 MicroPython 命令", "Enter one MicroPython command")}
                    disabled={!serialConnected || Boolean(deviceBusy)}
                  />
                  <button type="submit" disabled={!serialConnected || !deviceCommand.trim() || Boolean(deviceBusy)}>{tr("发送", "Send")}</button>
                </form>
                {capabilityAnalysis && (
                  <DeviceCapabilityPanel
                    analysis={capabilityAnalysis}
                    language={language}
                    micropythonosDetected={mposDetected}
                    hardwareId={detectedHardwareId}
                    probes={capabilityProbes}
                    warnings={sessionState?.warnings ?? []}
                    onProbe={() => void probeDeviceCapabilities()}
                    probing={capabilityProbing}
                  />
                )}
              </section>
            )}

            <div className="actions">
              {status === "running"
                ? <button className="danger-button" onClick={stop}>{tr("停止任务", "Stop")}</button>
                : <button className="main-button" disabled={!providerReady || !systemStatusConfirmed || !prompt.trim() || !(desktopTarget || webTarget || physicalTarget || packageTarget)} onClick={() => void run()}>{continuing ? tr("生成新版本", "Generate revision") : ["completed", "failed", "cancelled", "timeout"].includes(status) ? tr("重新生成 App", "Regenerate App") : tr("开始生成 App", "Generate App")}</button>}
              <span className="real-badge">{tr("真实调用 AI Provider", "Real AI provider")}</span>
            </div>
          </section>

          <ProgressCard
            language={language}
            status={status}
            currentStage={currentStage}
            sessionState={sessionState}
            capabilityAnalysis={capabilityAnalysis}
            capabilityProbes={capabilityProbes}
            providerResult={providerResult}
            errorMessage={errorMessage}
            onContinueEditing={() => { setContinuing(true); setStatus("idle"); setToast(tr("请修改上方需求，然后点击“生成新版本”", "Edit the prompt, then click Generate revision")); }}
            onReviewPermissions={() => setPermissionOpen(true)}
            onKeepWaiting={() => void continueWaiting()}
            onRetry={retry}
          />
        </div>

        {history.length > 0 && <section className="card history-card">
          <div><h2>{tr("历史会话", "Session history")}</h2><span>{tr("刷新页面或关闭浏览器后仍可恢复", "Restore work after refresh or closing the browser")}</span></div>
          <div className="history-list">{history.slice(0, 5).map((item) => <button key={item.session_id} onClick={() => void restoreSession(item.session_id)}><strong>{item.input.display_name}</strong><span>{item.revision_id} · {item.status} · {item.checkpoint_id}</span><small>{item.input.prompt_original}</small></button>)}</div>
        </section>}

        <details className="card showcase-library" id="showcase">
          <summary className="section-heading">
            <div>
              <span>{tr("探索、运行与部署社区作品", "Explore, run, and deploy community apps")}</span>
              <h2>{tr("公开 App 库", "Public App Library")}</h2>
            </div>
            <p>{tr("展开后可模拟运行、下载 MPK，或直接部署到已连接的板子。", "Expand to preview, download an MPK, or deploy directly to a connected board.")}</p>
          </summary>

          <div className="showcase-toolbar">
            <label className="showcase-field showcase-search">
              <span>{tr("搜索公开 App", "Search public apps")}</span>
              <input
                type="search"
                value={showcaseQuery}
                placeholder={tr("名称、描述或包名", "Name, description, or package")}
                disabled={showcaseStatus !== "ready"}
                onChange={(event) => {
                  setShowcaseQuery(event.target.value);
                  setShowAllShowcase(true);
                }}
              />
            </label>
            <label className="showcase-field">
              <span>{tr("类别", "Category")}</span>
              <select
                value={showcaseCategory}
                disabled={showcaseStatus !== "ready"}
                onChange={(event) => {
                  setShowcaseCategory(event.target.value);
                  setShowAllShowcase(true);
                }}
              >
                <option value="all">{tr("全部类别", "All categories")}</option>
                {showcaseCategories.map((category) => (
                  <option value={category} key={category}>{showcaseCategoryText(category)}</option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className={`showcase-toggle${showAllShowcase ? " active" : ""}`}
              aria-expanded={showAllShowcase}
              disabled={showcaseStatus !== "ready"}
              onClick={() => {
                const nextShowAll = !showAllShowcase;
                setShowAllShowcase(nextShowAll);
                if (!nextShowAll) {
                  setShowcaseQuery("");
                  setShowcaseCategory("all");
                }
              }}
            >
              {showAllShowcase ? tr("收起精选", "Show featured") : tr("查看全部", "Show all")}
            </button>
          </div>

          {showcaseStatus === "loading" && (
            <div className="showcase-state"><strong>{tr("正在载入公开 App 库…", "Loading public app library…")}</strong></div>
          )}
          {showcaseStatus === "error" && (
            <div className="showcase-state error">
              <strong>{tr("公开 App 库暂时无法载入", "Public app library unavailable")}</strong>
              <span>{tr("请刷新页面后重试。", "Refresh the page to try again.")}</span>
            </div>
          )}
          {showcaseStatus === "ready" && (
            <>
              <div className="showcase-results">
                <span>
                  {showAllShowcase
                    ? tr(`显示 ${visibleShowcaseApps.length} / ${showcaseApps.length} 个 App`, `Showing ${visibleShowcaseApps.length} of ${showcaseApps.length} apps`)
                    : tr(`精选 ${visibleShowcaseApps.length} 个 App`, `${visibleShowcaseApps.length} featured apps`)}
                </span>
              </div>
              {visibleShowcaseApps.length > 0 ? (
                <div className="showcase-grid">
                  {visibleShowcaseApps.map((item) => (
                    <article className="showcase-app" key={item.fullname}>
                      <figure className="showcase-shot">
                        <img
                          src={item.screenshotUrl}
                          alt={tr(`${item.name} 应用截图`, `${item.name} app screenshot`)}
                          width="320"
                          height="240"
                          loading="lazy"
                          decoding="async"
                        />
                        <span>{showcaseCategoryText(item.category)}</span>
                      </figure>
                      <div className="showcase-body">
                        <div className="showcase-title">
                          <h3>{item.name}</h3>
                          <small>v{item.version}</small>
                        </div>
                        <p title={item.longDescription}>{item.shortDescription}</p>
                        <div className="showcase-footer">
                          <code>{item.fullname}</code>
                          <div className="showcase-actions">
                            <button
                              type="button"
                              disabled={Boolean(showcaseAction)}
                              onClick={() => void previewShowcaseApp(item)}
                            >
                              {showcaseAction === `preview:${item.fullname}` ? tr("模拟中…", "Previewing…") : tr("模拟运行", "Preview")}
                            </button>
                            <button
                              type="button"
                              disabled={Boolean(showcaseAction) || Boolean(deviceBusy)}
                              onClick={() => void deployShowcaseApp(item)}
                            >
                              {showcaseAction === `deploy:${item.fullname}` ? tr("部署中…", "Deploying…") : tr("下载并部署", "Download and deploy")}
                            </button>
                            <button
                              type="button"
                              disabled={Boolean(showcaseAction)}
                              aria-label={tr(`下载 ${item.name} MPK`, `Download ${item.name} MPK`)}
                              onClick={() => void downloadShowcaseApp(item)}
                            >
                              {showcaseAction === `download:${item.fullname}` ? tr("校验中…", "Verifying…") : tr("下载 MPK", "Download MPK")}
                            </button>
                          </div>
                        </div>
                      </div>
                    </article>
                  ))}
                </div>
              ) : (
                <div className="showcase-state">
                  <strong>{tr("没有匹配的 App", "No matching apps")}</strong>
                  <span>{tr("换个关键词或类别试试。", "Try another keyword or category.")}</span>
                </div>
              )}
            </>
          )}
        </details>


        <EcosystemCard isZh={isZh} />

        <section className="card result-card" id="preview">
          <div className="tabs">
            <button className={activeTab === "preview" ? "active" : ""} onClick={() => setActiveTab("preview")}>{tr("App 预览", "App Preview")}</button>
            <button className={activeTab === "logs" ? "active" : ""} onClick={() => setActiveTab("logs")}>{tr("运行日志", "Runtime Logs")}</button>
            <button className={activeTab === "artifacts" ? "active" : ""} onClick={() => setActiveTab("artifacts")}>{tr("生成产物", "Artifacts")}</button>
          </div>
          {activeTab === "preview" && (
            <PreviewPane
              language={language}
              status={status}
              wasmReady={wasmReady}
              runtimeStatus={runtimeStatus}
              desktopScreenshotUrl={desktopScreenshot ? `${apiUrl}/api/artifacts/${desktopScreenshot.id}` : null}
              result={result}
              providerResult={providerResult}
              normalizedZh={result?.prompt_normalized_zh || sessionState?.input.prompt_normalized_zh || prompt}
              normalizedEn={result?.prompt_normalized_en || sessionState?.input.prompt_normalized_en || prompt}
              iframeRef={iframeRef}
              wasmRuntimeUrl={wasmRuntimeUrl}
              onIframeLoad={handleWasmFrameLoad}
            />
          )}
          {activeTab === "logs" && <div className="logs"><button onClick={() => { navigator.clipboard.writeText(logs.join("\n")); setToast(tr("日志已复制", "Logs copied")); }}>{tr("复制日志", "Copy logs")}</button><pre>{logs.length ? logs.join("\n") : tr("// 点击“开始生成 App”后，这里会显示日志。", "// Logs will appear here after you generate an app.")}</pre></div>}
          {activeTab === "artifacts" && (
            <ArtifactsPanel
              language={language}
              result={result}
              artifacts={sessionState?.artifacts ?? []}
              screenshotBusy={screenshotBusy}
              onDownloadArtifact={(artifact) => void downloadArtifact(artifact)}
              onDownloadFile={(file) => download(file.path, file.content)}
              onDownloadMpk={downloadMpk}
              onUploadScreenshot={(file) => void uploadScreenshot(file)}
            />
          )}
        </section>
      </main>

      {requirementOpen && (
        <RequirementModal
          language={language}
          messages={requirementMessages}
          input={requirementInput}
          busy={requirementBusy}
          error={requirementError}
          result={requirementResult}
          onInputChange={setRequirementInput}
          onSend={sendRequirementAnswer}
          onFinish={() => void requestRequirementHelp(requirementMessages, true)}
          onApply={applyRefinedRequirement}
          onClose={() => setRequirementOpen(false)}
        />
      )}

      {permissionOpen && sessionState && (
        <PermissionModal
          language={language}
          permissions={sessionState.permissions}
          permissionBusy={permissionBusy}
          onDecide={(permission, decision) => void decidePermission(permission, decision)}
          onAllowAll={() => void allowAllPermissions()}
          onClose={() => setPermissionOpen(false)}
        />
      )}
      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}
