# Blockless-Make-APP

让创客 App 先跑起来，再传播出去。

Blockless-Make-APP 是面向创客入门、科创教育和 STEM 课堂的 AI App
生成与分发平台。用户在浏览器里描述想法，系统生成 MicroPythonOS App，
支持预览、修改、打包、真机部署和 uPyStore 发布准备。

> Make, preview, deploy, and share MicroPythonOS apps from the browser.

用户描述 App 需求后，系统通过长任务工作流完成：

1. 需求分析与 App 规格生成
2. MicroPythonOS / LVGL API 校验
3. MicroPython + LVGL 源码生成
4. 桌面烟雾测试与可选 Web 预览
5. `MANIFEST.JSON` 校验及 `.mpk` 打包
6. 可选 ESP32/ESP32-S3 真机部署
7. 发布前检查与上传指导

当前版本不提供收费、在线充值或自动订阅。每个新账号获得 50 个免费内测点数，
每个新 App revision 消耗 10 点；点数仅用于控制内测资源。前端不展示购买入口。
用户使用用户名和密码注册、登录；密码只以加盐 scrypt 哈希保存，登录 token 只以
SHA-256 哈希保存在后端数据库。会话、权限、生成产物和点数按数据库用户 UUID
隔离，浏览器不能自行指定计费身份。第一版开放注册，没有密码找回和支付功能。

## 当前产品能力

- 自然语言生成、WASM 预览、版本继续修改和真实 `.mpk` 打包。
- WebSerial 连接 ESP32/ESP32-S3，安装并运行生成的 App。
- uPyStore 发布校验、截图、材料 ZIP 和手工上传引导。
- 硬件生态页：15 款真实适配板卡、Desktop 和 Web 目标。
- Tuya / 涂鸦智能相关板卡只标为规划适配/概念演示。

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

## 本地启动与固定端口

后端固定使用 `8000`，前端开发服务器固定使用 `5174`。Vite 已开启
`strictPort`，端口被占用时会直接报错，不会悄悄切换到其他端口。

```bash
# 终端 1：后端
backend/.venv/bin/python -m uvicorn app.main:app \
  --app-dir backend --host 0.0.0.0 --port 8000

# 终端 2：前端
cd frontend
npm install
npm run dev
```

浏览器打开 `http://localhost:5174/`，后端健康检查是
`http://localhost:8000/api/health`。前端默认连接该后端；需要更换地址时，
复制 `frontend/.env.example` 为 `frontend/.env.local` 后修改
`VITE_API_BASE_URL`。

WebSerial 只在安全上下文中可用。本机开发请使用 `localhost`；从其他电脑
通过局域网 IP 访问时，应配置 HTTPS，否则浏览器可能不提供串口连接功能。

后端密钥只写入未纳入 Git 的 `backend/.env`。如果密钥曾经出现在压缩包、
聊天或提交历史中，必须立即在服务商控制台撤销并创建新密钥，仅从环境变量
加载新密钥。

## 云端内测部署

推荐的零月租起步拓扑是 Render Free 单容器同源托管前端与 FastAPI，Supabase Free
提供 PostgreSQL 和私有 Storage。Render 本地文件系统不是持久存储，正式内测必须
同时配置数据库和对象存储；缺少任一项时，部署配置会让服务直接启动失败，避免
静默丢失账号、点数或生成产物。

完整步骤见 [`docs/deployment-render-supabase.md`](docs/deployment-render-supabase.md)。

## Browser protocol

前后端按 `mpos-ai-app/v1` 工作：

- `POST /api/sessions` 创建可恢复会话。
- `POST /api/sessions/:id/actions/run` 执行完整的一句话生成流水线；
  `actions/analyze`、`prepare-deps`、`generate`、`test`、`package`、
  `deploy`、`publish-check` 可单独执行和重跑。
- `GET /api/sessions/:id/events` 通过 SSE 返回阶段事件。
- 生成、重试、取消和 Web preview 结果都写入 checkpoint。
- analyze 后会写 `dependency_handoff.json`；每个阶段写
  `phase_complete.<phase>.json`，Runner 不把 Skill 文档当 shell 执行。
- 产物由 `artifact_manifest.json` 驱动，不向前端暴露服务器绝对路径。
- `.mpk` 使用 `<fullname>_rN.mpk`，发布仅提供 uPyStore 手工上传检查与引导。
- 连续修改会把上一成功 revision 的源码交给模型，并生成可下载的
  `rN_changes.patch`。
- 浏览器 WebSerial 的探测、安装和启动结果会回写 `deploy_result.json`。
- 仅选择真机部署时，流水线进入 `waiting_device`，这表示生成和打包已完成、
  正在等待浏览器连接设备，不会被前端误报为生成失败。
- `POST /api/sessions/:id/screenshots` 校验并保存 PNG/JPEG/WebP 发布截图，
  同时更新 `publish_result.json` 的截图门禁。
- `GET /api/sessions/:id/summary` 返回适合交付和发布页展示的脱敏摘要；
  `GET /api/sessions/:id/activity-log` 分别支持用户视图和工程师视图。
- `GET /api/sessions/:id/export?kind=session` 导出脱敏 session bundle；
  `kind=demo-artifacts` 导出宣传素材包。
- `POST /api/demo/sessions` 可创建或恢复 `countdown`、`calendar`、
  `device-dashboard` 三个确定性 Demo，不依赖模型随机输出。
- `POST /api/sessions/:id/demo-error` 仅在
  `MPOS_DEMO_ERROR_INJECTION=true` 时启用，用于录制“失败 → 回传 → 重试”；
  默认关闭，不能用于生产环境。
- 从失败、超时或取消状态重试前，后端会把旧状态、activity log 和 result
  JSON 保存到 `failed-attempts/attempt-NNN/`，不会覆盖失败现场。
