# Development and deployment notes

[English README](../README.md) | [中文 README](../README.zh-CN.md)

## Local baseline

The container uses Python 3.12 and Node.js 22. Use the same baseline for a predictable local setup. Initialize both submodules; install `backend/requirements.txt`, `runner/requirements.txt`, and the frontend lockfile dependencies.

Backend configuration is read from ignored `backend/.env` using the checked-in `.env.example`. Configure a supported provider using your own key. The example API credentials are placeholders. Provider selection/routing is implemented in backend source and may change; inspect the current template rather than copying old service credentials.

Run the backend at `127.0.0.1:8000` and the frontend at `localhost:5174`, as described in the README. Vite uses `strictPort`. If needed, use ignored `frontend/.env.local` with `VITE_API_BASE` to select the backend.

## Workflow contract

The browser protocol is `mpos-ai-app/v1`. Important routes in `backend/app/main.py` include:

| Route | Purpose |
| --- | --- |
| `POST /api/sessions` | Create a resumable app session |
| `POST /api/sessions/{id}/actions/run` | Start the controlled complete workflow |
| `POST /api/sessions/{id}/actions/{stage}` | Execute an implemented stage such as analyze, generate, test, package, deploy or publish-check |
| `GET /api/sessions/{id}/events` | Stream stage events through SSE |
| `GET /api/sessions/{id}/summary` | Read the sanitized session summary |
| `GET /api/sessions/{id}/activity-log` | Read the supported activity-log view |
| `POST /api/sessions/{id}/screenshots` | Upload supported publication screenshots |
| `GET /api/sessions/{id}/export?kind=session` | Export a sanitized session bundle |

Not every possible `{stage}` string is accepted. See [the architecture](architecture.md), [runner adapter](../runner/adapters/mpos/README.md), and [schemas](../runner/schemas) for exact stage contracts and result fields. Skill documents are contracts, not shell programs submitted by a browser or model.

Generated apps belong in session/app directories. Do not modify the upstream OS, LVGL bindings or build system to conceal an app error. Preserve the last successful revision and the failed-attempt evidence when iterating.

## Preview and hardware

Browser preview runs the bundled MicroPythonOS WebAssembly target. It cannot prove peripheral behavior. Desktop smoke requires the Linux SDL runtime/controller toolchain; missing tools must produce an explicit skipped/blocked result. A package file is not a successful device run.

WebSerial requires a supported browser and a secure context: use `localhost` locally or HTTPS remotely. Device actions depend on the actual OS firmware, board profile and connected serial device. `waiting_device` is a handoff state, not necessarily a failed generation.

## Persistence and hosted deployment

Local development can fall back to SQLite and filesystem sessions. A hosted service needs durable PostgreSQL and private S3-compatible object storage to retain users, session records and app artifacts across restarts. The checked-in `Dockerfile` builds the frontend and serves the backend in one container.

Review the current `backend/.env.example` for `DATABASE_URL`, the `MPOS_STORAGE_*` configuration group, cookie settings and model providers. `MPOS_REQUIRE_DURABLE_STORAGE=true` makes missing durable services a startup error. Keep storage credentials server-side, preserve account/session ownership checks, and disable development-only options in hosted environments. The unused `MPOS_API_TOKEN` example is not an authentication boundary.

Hosting providers, quotas and payment configuration are deployment-specific. This guide does not assert that a remote instance is currently available or production-ready.

## Focused validation

For frontend changes, use `npm test` and `npm run build` in `frontend/`. For backend/runner changes, select the relevant tests in `backend/tests/` and `runner/tests/` with an isolated environment. Do not run tests against private user sessions or production storage.

For documentation/license-only changes, validate relative links and images, JSON metadata, license consistency, preserved third-party notices, and `git diff --check`. Running model calls, full app generation or device operations is unnecessary for that scope.

## 中文说明

建议与容器保持 Python 3.12 / Node.js 22 基线，先初始化子模块，再安装后端、Runner 和前端依赖。密钥仅写入被忽略的后端 `.env`；浏览器只设置公开 API 地址。

预览、桌面检查、MPK 打包和实机部署分别记录结果。没有工具时明确跳过，没有设备时保持等待，不能伪造成功。托管环境需要持久数据库与私有对象存储；开发用 SQLite/文件系统回退不能代替生产持久化。文档和许可证修改仅需必要静态验证，不必调用模型或操作硬件。
