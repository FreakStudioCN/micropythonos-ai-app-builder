from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

PROTOCOL_VERSION = "mpos-ai-app/v1"


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=4000)
    package_name: str = "com.example.myapp"
    display_name: str = "我的 App"
    publisher: str = "erkou111"
    version: str = "0.1.0"
    revision: int = Field(default=1, ge=1, le=9999)
    previous_code: str | None = Field(default=None, max_length=100_000)
    runtime_error: str | None = Field(default=None, max_length=8000)

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
    warnings: list[str] = []
    acceptance_tests: list[str] = []
    mpk_filename: str
    revision: int = 1


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
    serial_port_scan: bool = False
    mpremote: bool = False
    firmware_flash: bool = False
    network_read: bool = True
    network_upload: bool = False
    upystore_publish: bool = False


class SessionCreateRequest(BaseModel):
    protocol_version: str = PROTOCOL_VERSION
    idempotency_key: str = Field(min_length=8, max_length=200)
    prompt: str = Field(min_length=3, max_length=4000)
    prompt_language: Literal["zh-CN", "en-US", "mixed", "unknown"] = "unknown"
    ui_locale: Literal["zh-CN", "en-US"] = "zh-CN"
    package_name: str = "com.example.myapp"
    display_name: str = "我的 App"
    publisher: str = "erkou111"
    version: str = "0.1.0"
    targets: list[
        Literal["desktop-preview", "web-preview", "physical-device", "package-only"]
    ] = ["web-preview", "package-only"]
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


class RevisionRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=200)
    prompt: str = Field(min_length=3, max_length=4000)
    prompt_language: Literal["zh-CN", "en-US", "mixed", "unknown"] = "unknown"


class PreviewResultRequest(SessionActionRequest):
    result: Literal["success", "failed", "timeout"]
    message: str = Field(default="", max_length=8000)


class PermissionDecisionRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=200)
    decision: Literal["allow_once", "deny"]


class ResumeRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=200)


class DeviceScanRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=200)
