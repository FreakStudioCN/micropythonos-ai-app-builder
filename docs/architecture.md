# Architecture

系统将一次 App 生成视为可恢复的长任务，而不是同步 HTTP 请求。

## Main components

- Frontend：收集自然语言需求，展示权限请求、进度、预览和产物。
- Backend：保存会话状态，调度任务并向前端传输结构化事件。
- Runner：执行需求分析、API 校验、代码生成、测试、打包、部署与发布检查。
- MicroPythonOS：提供运行时、桌面模拟、Web port 和硬件部署能力。
- MicroPython Skills：提供协议化的生成、测试、打包、部署与发布工作流。

## Planned skill pipeline

1. `mpos-dev`
2. `mpos-gen-app`
3. `mpos-test-app`
4. `mpos-package-app`
5. `mpos-deploy-app`
6. `mpos-publish-app`

`mpos-debug-app` 不进入主流程；失败信息应回传给 AI 进行迭代修复。
