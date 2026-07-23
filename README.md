# MicroPythonOS AI App Builder

用自然语言生成、预览、测试、打包并部署 MicroPythonOS App 的浏览器工作台。

用户描述 App 需求后，系统将通过长任务工作流完成：

1. 需求分析与 App 规格生成
2. MicroPythonOS / LVGL API 校验
3. MicroPython + LVGL 源码生成
4. 桌面烟雾测试与可选 Web 预览
5. `MANIFEST.JSON` 校验及 `.mpk` 打包
6. 可选 ESP32/ESP32-S3 真机部署
7. 发布前检查与上传指导

## Repository layout

```text
.
├── frontend/                 # 浏览器 UI
├── backend/                  # 会话、权限、任务与产物 API
├── runner/                   # AI/agent/skill 长任务执行器
├── docs/                     # 架构、协议和开发文档
└── vendor/
    ├── MicroPythonOS/        # 官方 OS，Git submodule
    └── MicroPython_Skills/   # FreakStudioCN，Git submodule
```

## Development rules

- App 代码只能写入任务生成目录或 MicroPythonOS 的 App 目录。
- 不通过修改 OS 框架、`lvgl_micropython`、构建脚本或系统库迁就生成的 App。
- 生成代码前必须完整读取 API 资料，并对所有 `lv.*` 调用交叉校验。
- Web preview 是可选能力；硬件安装和部署路径必须保留。
- API key、模型 token、串口信息、个人绝对路径和用户会话产物不得提交。

## Clone

```bash
git clone --recurse-submodules https://github.com/erkou111/micropythonos-ai-app-builder.git
cd micropythonos-ai-app-builder
git submodule update --init --recursive
```

## Dependency status

- `vendor/MicroPythonOS`：已接入官方仓库。
- `vendor/MicroPython_Skills`：已接入
  `https://github.com/FreakStudioCN/MicroPython_Skills.git`，固定到父仓库记录的 commit。

## Browser protocol

前后端按 `mpos-ai-app/v1` 工作：

- `POST /api/sessions` 创建可恢复会话。
- `GET /api/sessions/:id/events` 通过 SSE 返回阶段事件。
- 生成、重试、取消和 Web preview 结果都写入 checkpoint。
- 产物由 `artifact_manifest.json` 驱动，不向前端暴露服务器绝对路径。
- `.mpk` 使用 `<fullname>_rN.mpk`，发布仅提供 uPyStore 手工上传检查与引导。
