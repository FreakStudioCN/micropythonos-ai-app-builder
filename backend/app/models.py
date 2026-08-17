from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

PROTOCOL_VERSION = "mpos-ai-app/v1"
AIProviderId = Literal["auto", "deepseek_primary", "deepseek_secondary", "aigocode"]


class CapabilityRequirements(BaseModel):
    """Abstract hardware requirements carried by a session end to end.

    Deliberately has no ``target_board``: the browser never asks the user to
    pick a board, and the running MicroPythonOS is the only authority on what
    hardware actually exists.
    """

    required_capabilities: list[str] = Field(default_factory=list, max_length=32)
    required_accessories: list[str] = Field(default_factory=list, max_length=32)
    # Bounded like its sibling lists: one fallback sentence per capability.
    runtime_fallbacks: dict[str, str] = Field(default_factory=dict, max_length=32)
    physical_validation_required: bool = False


class CapabilityProbeResult(BaseModel):
    """One runtime probe outcome from a connected device.

    ``available=None`` means the probe could not be evaluated (for example the
    contract's probe expression takes a parameter). That is explicitly not the
    same as ``False``: reporting "not measured" as "hardware absent" would
    fabricate a device verdict.
    """

    capability: str = Field(min_length=1, max_length=64)
    available: bool | None = None
    probe: str = Field(default="", max_length=200)
    detail: str = Field(default="", max_length=2000)


class SystemStatusResponse(BaseModel):
    status: Literal["ready", "maintenance"]
    maintenance_mode: bool
    message: str
    retry_after_seconds: int = Field(ge=1, le=86400)


class GenerateRequest(CapabilityRequirements):
    prompt: str = Field(min_length=3, max_length=4000)
    package_name: str = "com.example.myapp"
    display_name: str = "我的 App"
    publisher: str = "erkou111"
    version: str = "0.1.0"
    revision: int = Field(default=1, ge=1, le=9999)
    previous_code: str | None = Field(default=None, max_length=100_000)
    runtime_error: str | None = Field(default=None, max_length=8000)
    ai_provider: AIProviderId = "auto"

    @field_validator("package_name")
    @classmethod
    def validate_package_name(cls, value: str) -> str:
        parts = value.split(".")
        if len(parts) < 3 or any(not part.replace("_", "").isalnum() for part in parts):
            raise ValueError("包名必须类似 com.example.myapp")
        return value.lower()


class GeneratedFile(BaseModel):
    path: str
    content: str


class GenerateResponse(BaseModel):
    package_name: str
    summary: str
    manifest: dict[str, Any]
    files: list[GeneratedFile]
    mpk_base64: str
    model: str
    provider: str = ""
    failover_used: bool = False
    attempted_providers: list[str] = Field(default_factory=list)
    provider_attempts: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = []
    acceptance_tests: list[str] = []
    mpk_filename: str
    revision: int = 1
    prompt_normalized_zh: str = ""
    prompt_normalized_en: str = ""
    store_metadata: dict[str, Any] = Field(default_factory=dict)
    required_capabilities: list[str] = Field(default_factory=list)
    required_accessories: list[str] = Field(default_factory=list)
    runtime_fallbacks: dict[str, str] = Field(default_factory=dict)
    physical_validation_required: bool = False
    capability_contracts: list[dict[str, Any]] = Field(default_factory=list)
    capability_warnings: list[str] = Field(default_factory=list)
    skill_commit: str = ""
    mpos_commit: str = ""
    board_capabilities_schema: str = ""


class RequirementMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class RequirementChatRequest(BaseModel):
    locale: Literal["zh-CN", "en-US"] = "zh-CN"
    draft_prompt: str = Field(min_length=3, max_length=4000)
    messages: list[RequirementMessage] = Field(min_length=1, max_length=24)
    finalize: bool = False


class RequirementChatResponse(BaseModel):
    assistant_message: str
    ready: bool = False
    refined_prompt: str = ""
    missing_fields: list[str] = Field(default_factory=list)
    brief: dict[str, Any] = Field(default_factory=dict)
    model: str


class Capabilities(BaseModel):
    file_operation: bool = True
    script_run: bool = True
    approval_request: bool = True
    permission_request: bool = True
    checkpoint_resume: bool = True
    cancellation: bool = True
    retry: bool = True
    timeout: bool = True
    desktop_preview: bool = False
    web_preview: bool = True
    physical_device: bool = False
    browser_webserial: bool = False
    serial_port_scan: bool = False
    mpremote: bool = False
    firmware_flash: bool = False
    network_read: bool = True
    network_upload: bool = False
    upystore_publish: bool = False


class SessionCreateRequest(CapabilityRequirements):
    protocol_version: str = PROTOCOL_VERSION
    idempotency_key: str = Field(min_length=8, max_length=200)
    prompt: str = Field(min_length=3, max_length=4000)
    prompt_language: Literal["zh-CN", "en-US", "mixed", "unknown"] = "unknown"
    ui_locale: Literal["zh-CN", "en-US"] = "zh-CN"
    package_name: str = "com.example.myapp"
    display_name: str = "我的 App"
    display_name_zh: str = ""
    display_name_en: str = ""
    short_description_zh: str = ""
    short_description_en: str = ""
    long_description_zh: str = ""
    long_description_en: str = ""
    release_notes_zh: str = ""
    release_notes_en: str = ""
    category: str = "generated"
    publisher: str = "erkou111"
    version: str = "0.1.0"
    ai_provider: AIProviderId = "auto"
    targets: list[
        Literal["desktop-preview", "web-preview", "physical-device", "package-only"]
    ] = ["web-preview", "package-only"]
    # Runner/browser environment capabilities. Kept strictly separate from the
    # App's required_capabilities: one describes where code can run, the other
    # describes what hardware the App needs.
    capabilities: Capabilities = Field(default_factory=Capabilities)

    @field_validator("protocol_version")
    @classmethod
    def validate_protocol(cls, value: str) -> str:
        if value != PROTOCOL_VERSION:
            raise ValueError(f"protocol_version 必须是 {PROTOCOL_VERSION}")
        return value

    @field_validator("package_name")
    @classmethod
    def validate_session_package_name(cls, value: str) -> str:
        return GenerateRequest.validate_package_name(value)


class SessionActionRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=200)
    previous_code: str | None = Field(default=None, max_length=100_000)
    runtime_error: str | None = Field(default=None, max_length=8000)
    timeout_seconds: int = Field(default=180, ge=10, le=600)
    ai_provider: AIProviderId | None = None


class AuthCredentials(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)


class DemoSessionRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=200)
    seed: Literal["countdown", "calendar", "device-dashboard"] = "countdown"
    ui_locale: Literal["zh-CN", "en-US"] = "zh-CN"


class DemoErrorInjectionRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=200)
    code: Literal[
        "LVGL_API_MISSING",
        "SCRIPT_TIMEOUT",
        "DEVICE_NOT_CONNECTED",
        "WEB_PREVIEW_BUILD_FAILED",
    ] = "LVGL_API_MISSING"


class RevisionRequest(CapabilityRequirements):
    idempotency_key: str = Field(min_length=8, max_length=200)
    prompt: str = Field(min_length=3, max_length=4000)
    prompt_language: Literal["zh-CN", "en-US", "mixed", "unknown"] = "unknown"
    ai_provider: AIProviderId | None = None


class PreviewResultRequest(SessionActionRequest):
    # "partial" reports a preview that ran but could not exercise a physical
    # capability. It is not a failure and must never start a code-repair loop.
    result: Literal["success", "failed", "timeout", "partial"]
    message: str = Field(default="", max_length=8000)
    unsupported_capabilities: list[str] = Field(default_factory=list, max_length=32)


class PermissionDecisionRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=200)
    decision: Literal["allow_once", "deny"]


class PermissionBatchDecisionRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=200)


class ResumeRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=200)


class DeviceScanRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=200)


class DeviceResultRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=200)
    result: Literal["probe_success", "install_success", "launch_success", "failed"]
    error_code: str | None = Field(default=None, max_length=100)
    hardware_available: bool | None = None
    micropythonos_installed: bool | None = None
    # Diagnostics only, read from DeviceInfo after the user authorises the
    # device. Never a selector, never a precondition, and never defaulted to a
    # specific board: an unlisted board that probes successfully is still valid.
    hardware_id: str = Field(default="", max_length=200)
    runtime_capability_results: list[CapabilityProbeResult] = Field(
        default_factory=list, max_length=32
    )
    usb_vendor_id: int | None = Field(default=None, ge=0, le=0xFFFF)
    usb_product_id: int | None = Field(default=None, ge=0, le=0xFFFF)
    installed_path: str | None = Field(default=None, max_length=500)
    transport: Literal["webserial", "mpremote", "device-copy", "mpk-install"] = "webserial"
    message: str = Field(default="", max_length=8000)
    log_excerpt: str = Field(default="", max_length=20_000)


class ScreenshotUploadRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=200)
    filename: str = Field(min_length=1, max_length=200)
    media_type: Literal["image/png", "image/jpeg", "image/webp"]
    data_base64: str = Field(min_length=16, max_length=14_000_000)
    source: Literal["desktop", "web", "device", "manual"] = "manual"
