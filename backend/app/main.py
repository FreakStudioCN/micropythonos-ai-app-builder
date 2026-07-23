import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .generator import GenerationError, generate_app
from .models import (
    GenerateRequest,
    GenerateResponse,
    DeviceScanRequest,
    PermissionDecisionRequest,
    PreviewResultRequest,
    RevisionRequest,
    ResumeRequest,
    SessionActionRequest,
    SessionCreateRequest,
)
from .session_service import SessionNotFound, session_service

load_dotenv()

app = FastAPI(title="MicroPythonOS AI App Builder API", version="0.1.0")
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
wasm_web_root = Path(__file__).resolve().parents[2] / "frontend" / "public" / "mpos-web"
app.mount("/mpos-web", StaticFiles(directory=wasm_web_root, html=True), name="mpos-web")


@app.get("/api/health")
def health() -> dict[str, str | bool]:
    configured = bool(os.getenv("DEEPSEEK_API_KEY", "").strip())
    return {"status": "ok", "deepseek_configured": configured}


@app.get("/api/capabilities")
def capabilities() -> dict:
    return session_service.capabilities()


@app.get("/api/sessions")
def list_sessions() -> list[dict]:
    return session_service.list_sessions()


@app.post("/api/sessions", status_code=201)
def create_session(request: SessionCreateRequest) -> dict:
    return session_service.create(request)


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
            if state["status"] in {"completed", "failed", "cancelled", "blocked", "timeout"} and cursor >= len(
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
async def generate_session(session_id: str, request: SessionActionRequest) -> dict:
    try:
        return session_service.start_action(session_id, "generate", request)
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Session 不存在") from exc


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


@app.post("/api/sessions/{session_id}/devices/scan")
def scan_devices(session_id: str, request: DeviceScanRequest) -> dict:
    try:
        return session_service.scan_devices(session_id, request.idempotency_key)
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Session 不存在") from exc


@app.post("/api/generate", response_model=GenerateResponse)
async def generate(request: GenerateRequest) -> GenerateResponse:
    try:
        return await generate_app(request)
    except GenerationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
