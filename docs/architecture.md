# Architecture

系统将一次 App 生成视为可恢复的长任务，而不是同步 HTTP 请求。

## Main components

- Frontend：收集自然语言需求，展示权限请求、进度、预览和产物。
- Backend：保存会话状态，调度任务并向前端传输结构化事件。
- Runner：执行需求分析、API 校验、代码生成、测试、打包、部署与发布检查。
- MicroPythonOS：提供运行时、桌面模拟、Web port 和硬件部署能力。
- MicroPython Skills：提供协议化的生成、测试、打包、部署与发布工作流。

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
  -> single in-flight controlled runner
  -> persisted session_state.json + activity_log.jsonl
  -> DeepSeek generation and static gates
  -> MicroPythonOS WASM self_test
  -> artifact download + manual uPyStore guidance
```

会话工作目录位于 `backend/sessions/<session_id>/`，已由 `.gitignore`
排除。前端只使用 artifact ID 下载文件，不能请求任意主机路径。

连续修改沿用同一个 `session_id`，递增 `revision_id`。开始新 revision
前先把上一成功版本的 project 和 artifacts 保存到 `revisions/rN/`，
因此失败修改不会覆盖最后一个可用版本。

## Runtime boundaries

- Skill 文件定义阶段契约，不能向后端注入 shell。
- `ScriptDispatcher` 只执行服务器预定义的白名单操作。
- `DeviceService` 单独维护设备能力与锁；没有串口/`mpremote` 时返回 blocked，
  不把 Web preview 说成真机成功。
- 每条活动事件同时包含 `seq`、`ts`、`session_id`、`stage` 和 `phase`。
- Checkpoint history 记录输入 hash、Skill 名称和版本、两个 submodule commit、
  API summary 版本、输出文件、warning/error 与下一阶段。
- Artifact 只通过 artifact ID 或 manifest 相对路径访问。
