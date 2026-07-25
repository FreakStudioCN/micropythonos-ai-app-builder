import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi import Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .auth import (
    COOKIE_MAX_AGE,
    COOKIE_NAME,
    InvalidCredentials,
    ROLE_SUPERADMIN,
    UsernameTaken,
    auth_service,
)
from .billing import InsufficientCredits, billing_service
from .database import database_engine
from .generator import GenerationError, generate_app
from .requirements_chat import RequirementChatError, clarify_requirements
from .models import (
    AuthCredentials,
    DemoErrorInjectionRequest,
    DemoSessionRequest,
    GenerateRequest,
    GenerateResponse,
    DeviceScanRequest,
    DeviceResultRequest,
    PermissionBatchDecisionRequest,
    PermissionDecisionRequest,
    PreviewResultRequest,
    RequirementChatRequest,
    RequirementChatResponse,
    RevisionRequest,
    ResumeRequest,
    ScreenshotUploadRequest,
    SessionActionRequest,
    SessionCreateRequest,
)
from .session_service import SessionNotFound, session_service


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


if _enabled("MPOS_REQUIRE_DURABLE_STORAGE"):
    missing: list[str] = []
    if database_engine.dialect.name == "sqlite":
        missing.append("DATABASE_URL")
    if not session_service.object_storage_enabled:
        missing.append("MPOS_STORAGE_*")
    if missing:
        raise RuntimeError(
            "Durable deployment requires: " + ", ".join(missing)
        )

app = FastAPI(title="Blockless-Make-APP API", version="0.1.0")
local_frontend_origins = [
    "http://localhost:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",
]
configured_frontend_origins = os.getenv(
    "FRONTEND_ORIGINS",
    os.getenv("FRONTEND_ORIGIN", ""),
)
frontend_origins = list(dict.fromkeys(
    local_frontend_origins
    + [origin.strip() for origin in configured_frontend_origins.split(",") if origin.strip()]
))
project_root = Path(__file__).resolve().parents[2]
frontend_dist_root = project_root / "frontend" / "dist"
wasm_web_root = (
    frontend_dist_root / "mpos-web"
    if (frontend_dist_root / "mpos-web").is_dir()
    else project_root / "frontend" / "public" / "mpos-web"
)
app.mount("/mpos-web", StaticFiles(directory=wasm_web_root, html=True), name="mpos-web")


def _current_user_id(request: Request) -> str:
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="请先登录")
    return user_id


def _current_user(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def _is_superadmin(user: dict) -> bool:
    return user.get("role") == ROLE_SUPERADMIN


def _cookie_is_secure(request: Request) -> bool:
    configured = os.getenv("MPOS_COOKIE_SECURE", "auto").strip().lower()
    if configured in {"1", "true", "yes", "on"}:
        return True
    if configured in {"0", "false", "no", "off"}:
        return False
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    return request.url.scheme == "https" or forwarded_proto.split(",", 1)[0] == "https"


def _cookie_same_site() -> str:
    same_site = os.getenv("MPOS_COOKIE_SAMESITE", "lax").strip().lower()
    return same_site if same_site in {"lax", "strict", "none"} else "lax"


def _set_login_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        secure=_cookie_is_secure(request),
        samesite=_cookie_same_site(),
        path="/",
    )


def _account_payload(user: dict) -> dict:
    return {
        **billing_service.account(
            user["id"],
            unlimited=_is_superadmin(user),
        ),
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "user_created_at": user["created_at"],
    }


class AuthenticatedUserMiddleware:
    """Pure ASGI auth middleware so streaming responses remain streaming."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return

        public_paths = {
            "/api/health",
            "/api/capabilities",
            "/api/auth/register",
            "/api/auth/login",
        }
        if scope["method"] == "OPTIONS" or scope["path"] in public_paths:
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        user = auth_service.authenticate(request.cookies.get(COOKIE_NAME))
        if user is None:
            response = JSONResponse(status_code=401, content={"detail": "请先登录"})
            await response(scope, receive, send)
            return

        user_id = user["id"]
        scope.setdefault("state", {})["user"] = user
        scope["state"]["user_id"] = user_id
        try:
            segments = scope["path"].strip("/").split("/")
            if not _is_superadmin(user):
                if len(segments) >= 3 and segments[:2] == ["api", "sessions"]:
                    session_service.require_owner(segments[2], user_id)
                elif len(segments) >= 3 and segments[:2] == ["api", "artifacts"]:
                    session_service.require_artifact_owner(segments[2], user_id)
                elif len(segments) >= 3 and segments[:2] == ["api", "permissions"]:
                    session_service.require_permission_owner(segments[2], user_id)
        except SessionNotFound:
            response = JSONResponse(status_code=404, content={"detail": "资源不存在"})
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


app.add_middleware(AuthenticatedUserMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, Any]:
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    configured = bool(key)
    return {
        "status": "ok",
        "deepseek_configured": configured,
        "deepseek_key_fingerprint": (
            hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
            if configured
            else None
        ),
        "database_backend": database_engine.dialect.name,
        "object_storage_enabled": session_service.object_storage_enabled,
        "durable_storage_required": _enabled("MPOS_REQUIRE_DURABLE_STORAGE"),
    }


@app.get("/api/capabilities")
def capabilities() -> dict:
    return session_service.capabilities()


@app.post("/api/requirements/chat", response_model=RequirementChatResponse)
async def requirement_chat(
    request: RequirementChatRequest,
) -> RequirementChatResponse:
    try:
        return await clarify_requirements(request)
    except RequirementChatError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/auth/register", status_code=201)
def register(
    payload: AuthCredentials,
    request: Request,
    response: Response,
) -> dict:
    try:
        user, token = auth_service.register(payload.username, payload.password)
    except UsernameTaken as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _set_login_cookie(response, request, token)
    return _account_payload(user)


@app.post("/api/auth/login")
def login(
    payload: AuthCredentials,
    request: Request,
    response: Response,
) -> dict:
    try:
        user, token = auth_service.login(payload.username, payload.password)
    except InvalidCredentials as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _set_login_cookie(response, request, token)
    return _account_payload(user)


@app.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict[str, str]:
    auth_service.logout(request.cookies.get(COOKIE_NAME))
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        secure=_cookie_is_secure(request),
        httponly=True,
        samesite=_cookie_same_site(),
    )
    return {"status": "logged_out"}


@app.get("/api/user")
def current_user(request: Request) -> dict:
    return _account_payload(_current_user(request))


@app.get("/api/billing/account")
def billing_account(request: Request) -> dict:
    return _account_payload(_current_user(request))


@app.get("/api/admin/users")
def admin_users(request: Request) -> list[dict]:
    if not _is_superadmin(_current_user(request)):
        raise HTTPException(status_code=403, detail="需要超级管理员权限")
    return [_account_payload(user) for user in auth_service.list_users()]


@app.get("/api/sessions")
def list_sessions(request: Request) -> list[dict]:
    user = _current_user(request)
    owner_user_id = None if _is_superadmin(user) else user["id"]
    return session_service.list_sessions(owner_user_id)


@app.post("/api/demo/sessions", status_code=201)
def create_demo_session(payload: DemoSessionRequest, request: Request) -> dict:
    """Create or restore a deterministic, model-independent demo session."""
    return session_service.create_demo(payload, _current_user_id(request))


@app.post("/api/sessions", status_code=201)
def create_session(payload: SessionCreateRequest, request: Request) -> dict:
    return session_service.create(payload, _current_user_id(request))


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    try:
        return session_service.get(session_id)
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Session 不存在") from exc


@app.get("/api/sessions/{session_id}/events")
async def session_events(
    session_id: str, after: int = Query(default=0, ge=0)
) -> StreamingResponse:
    try:
        session_service.get(session_id)
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Session 不存在") from exc

    async def stream():
        cursor = after
        idle_ticks = 0
        while True:
            events = session_service.events(session_id)
            for event in events[cursor:]:
                cursor += 1
                yield (
                    f"id: {event['seq']}\n"
                    f"event: {event['type']}\n"
                    f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                )
                idle_ticks = 0
            state = session_service.get(session_id)
            if state["status"] in {
                "completed",
                "failed",
                "cancelled",
                "blocked",
                "timeout",
                "waiting_device",
            } and cursor >= len(
                events
            ):
                yield (
                    "event: stream_end\n"
                    f"data: {json.dumps({'session_id': session_id, 'status': state['status']}, ensure_ascii=False)}\n\n"
                )
                break
            idle_ticks += 1
            if idle_ticks >= 20:
                yield ": keep-alive\n\n"
                idle_ticks = 0
            await asyncio.sleep(0.5)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/sessions/{session_id}/actions/generate", status_code=202)
async def generate_session(
    session_id: str,
    payload: SessionActionRequest,
    request: Request,
) -> dict:
    try:
        state = session_service.get(session_id)
        user = _current_user(request)
        billing_service.consume_generation(
            user["id"],
            f"generation:{session_id}:{state['revision_id']}",
            unlimited=_is_superadmin(user),
        )
        return _start_action(session_id, "generate", payload)
    except InsufficientCredits as exc:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "INSUFFICIENT_CREDITS",
                "message": str(exc),
                "balance": exc.balance,
                "required": exc.required,
            },
        ) from exc


@app.post("/api/sessions/{session_id}/actions/run", status_code=202)
async def run_session(
    session_id: str,
    payload: SessionActionRequest,
    request: Request,
) -> dict:
    try:
        state = session_service.get(session_id)
        user = _current_user(request)
        billing_service.consume_generation(
            user["id"],
            f"generation:{session_id}:{state['revision_id']}",
            unlimited=_is_superadmin(user),
        )
        return session_service.start_generation(session_id, payload)
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Session 不存在") from exc
    except InsufficientCredits as exc:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "INSUFFICIENT_CREDITS",
                "message": str(exc),
                "balance": exc.balance,
                "required": exc.required,
            },
        ) from exc


@app.post("/api/sessions/{session_id}/actions/prepare-deps", status_code=202)
async def prepare_deps_session(
    session_id: str, request: SessionActionRequest
) -> dict:
    return _start_action(session_id, "prepare-deps", request)


def _start_action(session_id: str, action: str, request: SessionActionRequest) -> dict:
    try:
        return session_service.start_action(session_id, action, request)
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Session 不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/sessions/{session_id}/actions/analyze", status_code=202)
async def analyze_session(session_id: str, request: SessionActionRequest) -> dict:
    return _start_action(session_id, "analyze", request)


@app.post("/api/sessions/{session_id}/actions/test", status_code=202)
async def test_session(session_id: str, request: SessionActionRequest) -> dict:
    return _start_action(session_id, "test", request)


@app.post("/api/sessions/{session_id}/actions/package", status_code=202)
async def package_session(session_id: str, request: SessionActionRequest) -> dict:
    return _start_action(session_id, "package", request)


@app.post("/api/sessions/{session_id}/actions/deploy", status_code=202)
async def deploy_session(session_id: str, request: SessionActionRequest) -> dict:
    return _start_action(session_id, "deploy", request)


@app.post("/api/sessions/{session_id}/actions/publish-check", status_code=202)
async def publish_check_session(session_id: str, request: SessionActionRequest) -> dict:
    return _start_action(session_id, "publish-check", request)


@app.post("/api/sessions/{session_id}/actions/preview-result")
def record_preview(
    session_id: str, request: PreviewResultRequest
) -> dict:
    try:
        return session_service.preview_result(session_id, request)
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Session 不存在") from exc


@app.post("/api/sessions/{session_id}/retry", status_code=202)
async def retry_session(session_id: str, request: SessionActionRequest) -> dict:
    try:
        return session_service.start_generation(session_id, request)
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Session 不存在") from exc


@app.post("/api/sessions/{session_id}/revisions", status_code=201)
def create_revision(session_id: str, request: RevisionRequest) -> dict:
    try:
        return session_service.create_revision(session_id, request)
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Session 不存在") from exc


@app.post("/api/sessions/{session_id}/resume")
def resume_session(session_id: str, request: ResumeRequest) -> dict:
    try:
        return session_service.resume(session_id, request.idempotency_key)
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Session 不存在") from exc


@app.post("/api/sessions/{session_id}/cancel")
async def cancel_session(session_id: str, request: SessionActionRequest) -> dict:
    try:
        return await session_service.cancel(session_id, request.idempotency_key)
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Session 不存在") from exc


@app.get("/api/sessions/{session_id}/artifacts")
def session_artifacts(session_id: str) -> dict:
    try:
        state = session_service.get(session_id)
        return {
            "schema_version": "mpos-artifact-manifest-v1",
            "session_id": session_id,
            "app_fullname": state["input"]["package_name"],
            "artifacts": state["artifacts"],
        }
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Session 不存在") from exc


@app.get("/api/sessions/{session_id}/summary")
def session_summary(session_id: str) -> dict:
    try:
        return session_service.session_summary(session_id)
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Session 不存在") from exc


@app.get("/api/sessions/{session_id}/activity-log")
def session_activity_log(
    session_id: str,
    view: str = Query(default="engineer", pattern="^(user|engineer)$"),
) -> dict:
    try:
        return session_service.activity_log(
            session_id, view=view, redacted=True
        )
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Session 不存在") from exc


@app.get("/api/sessions/{session_id}/export")
def export_session(
    session_id: str,
    kind: str = Query(default="session", pattern="^(session|demo-artifacts)$"),
) -> FileResponse:
    try:
        path, artifact = session_service.export_bundle(session_id, kind=kind)
        return FileResponse(
            path,
            media_type="application/zip",
            filename=artifact["display_name"],
        )
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Session 不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/sessions/{session_id}/demo-error")
def inject_demo_error(
    session_id: str, request: DemoErrorInjectionRequest
) -> dict:
    try:
        return session_service.inject_demo_error(session_id, request)
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Session 不存在") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/artifacts/{artifact_id}")
def download_artifact(artifact_id: str) -> FileResponse:
    try:
        path, artifact = session_service.artifact(artifact_id)
        return FileResponse(
            path,
            media_type=artifact["mime"],
            filename=artifact["display_name"],
        )
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Artifact 不存在") from exc


@app.post("/api/permissions/{permission_id}/decision")
def decide_permission(
    permission_id: str, request: PermissionDecisionRequest
) -> dict:
    try:
        return session_service.decide_permission(permission_id, request)
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Permission 不存在") from exc


@app.post("/api/sessions/{session_id}/permissions/allow-all")
def allow_all_permissions(
    session_id: str, request: PermissionBatchDecisionRequest
) -> dict:
    try:
        return session_service.allow_all_permissions(session_id, request)
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Session 不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/sessions/{session_id}/devices/scan")
def scan_devices(session_id: str, request: DeviceScanRequest) -> dict:
    try:
        return session_service.scan_devices(session_id, request.idempotency_key)
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Session 不存在") from exc


@app.post("/api/sessions/{session_id}/devices/result")
def record_device_result(session_id: str, request: DeviceResultRequest) -> dict:
    try:
        return session_service.record_device_result(session_id, request)
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Session 不存在") from exc


@app.post("/api/sessions/{session_id}/screenshots", status_code=201)
def upload_screenshot(session_id: str, request: ScreenshotUploadRequest) -> dict:
    try:
        return session_service.upload_screenshot(session_id, request)
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Session 不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/generate", response_model=GenerateResponse)
async def generate(payload: GenerateRequest, request: Request) -> GenerateResponse:
    try:
        user = _current_user(request)
        billing_service.consume_generation(
            user["id"],
            f"legacy-generation:{hashlib.sha256(os.urandom(32)).hexdigest()}",
            unlimited=_is_superadmin(user),
        )
        return await generate_app(payload)
    except InsufficientCredits as exc:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "INSUFFICIENT_CREDITS",
                "message": str(exc),
                "balance": exc.balance,
                "required": exc.required,
            },
        ) from exc
    except GenerationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# In production the Vite build is served by the same FastAPI process. Register
# this mount last so every /api route and /mpos-web keep priority.
if frontend_dist_root.is_dir():
    app.mount("/", StaticFiles(directory=frontend_dist_root, html=True), name="frontend")
