# Architecture

系统将一次 App 生成视为可恢复的长任务，而不是同步 HTTP 请求。

## Main components

- Frontend：收集自然语言需求，展示权限请求、进度、预览和产物。
- Backend：保存会话状态，调度任务并向前端传输结构化事件。
- Runner：执行需求分析、API 校验、代码生成、测试、打包、部署与发布检查。
- MicroPythonOS：提供运行时、桌面模拟、Web port 和硬件部署能力。
- MicroPython Skills：提供协议化的生成、测试、打包、部署与发布工作流。

第一轮正式内测使用数据库用户名/密码账号。密码使用加盐 scrypt 哈希，登录 token
只把 SHA-256 哈希保存到数据库，原 token 保存在 HttpOnly Cookie。所有 session、
artifact、permission 和 billing 请求都在统一中间件中执行用户 UUID 所有权检查，
支持跨浏览器登录恢复；第一版不提供密码找回。

## Browser skill pipeline

1. `mpos-plan-app-web`
2. `mpos-analyze-app-web`
3. `mpos-prepare-deps-web`（需要 App 本地依赖时）
4. `mpos-gen-app-web`
5. `mpos-test-app-web`
6. `mpos-package-app-web`
7. `mpos-deploy-app-web`
8. `mpos-publish-app-web`

classic `mpos-*` skill 保持不变；浏览器只依赖 `mpos-ai-app/v1` 的
result JSON、checkpoint、activity log、artifact manifest 和结构化错误。
`mpos-debug-app` 不进入主流程；失败信息应回传给 AI 进行迭代修复。

## Implemented MVP flow

```text
React Workbench
  -> FastAPI session API
  -> per-permission approval host
  -> MposSkillAdapter validates mpos-*-web contracts
  -> /actions/run orchestrates the complete controlled pipeline
  -> /actions/{stage} can execute analyze/prepare-deps/generate/test/package/deploy/publish-check independently
  -> persisted session_state.json + activity_log.jsonl
  -> analysis_result + dependency_handoff
  -> DeepSeek generation, bilingual normalization and product/static gates
  -> MicroPythonOS WASM self_test
  -> browser WebSerial result audit + deploy_result
  -> validated PNG/JPEG/WebP screenshot upload
  -> artifact/session/publish bundle + manual uPyStore guidance
```

会话工作目录位于 `backend/sessions/<session_id>/`，已由 `.gitignore`
排除。前端只使用 artifact ID 下载文件，不能请求任意主机路径。
云端运行时工作目录是临时缓存，并增量同步到 Supabase Storage 私有 bucket；账号、
登录会话、点数账户和账本位于 Supabase PostgreSQL。服务启动时从对象存储恢复
session/artifact，Render 重启不会把持久数据留在临时磁盘里。

连续修改沿用同一个 `session_id`，递增 `revision_id`。开始新 revision
前先把上一成功版本的 project 和 artifacts 保存到 `revisions/rN/`，
并把上一版 `assets/main.py` 作为模型修改输入，同时生成 unified diff，
因此失败修改不会覆盖最后一个可用版本。

## Runtime boundaries

- Skill 文件定义阶段契约，不能向后端注入 shell。
- `ScriptDispatcher` 只执行服务器预定义的白名单操作。
- Desktop smoke 仅在 Linux SDL binary、controller 和 runner 同时存在时执行；
  否则以 `TOOLCHAIN_MISSING`/skipped 记录，不伪造测试通过。
- `DeviceService` 单独维护设备能力与锁；没有串口/`mpremote` 时返回 blocked，
  不把 Web preview 说成真机成功。
- 每条活动事件同时包含 `seq`、`ts`、`session_id`、`stage` 和 `phase`。
- Checkpoint history 记录输入 hash、Skill 名称和版本、两个 submodule commit、
  API summary 版本、输出文件、warning/error 与下一阶段。
- Artifact 只通过 artifact ID 或 manifest 相对路径访问。
