# Blockless-Make-APP Fix Plan

审计基线：2026-07-25，对照 `../FRONTEND_BACKEND_ONBOARDING.md`，代码提交
`8ae5225a3bc00907ff70baae20430d96f0f00f8b`。

## 已验证基线

- [x] 父仓库与顶层 Git submodule 已拉取并锁定到父仓库记录的 commit。
- [x] 后端依赖已安装到 `backend/.venv`，前端依赖已安装到 `frontend/node_modules`。
- [x] 后端 38 个单元测试通过。
- [x] 前端 TypeScript 与 Vite 生产构建通过。
- [x] 本地生产构建已由 FastAPI/Uvicorn 在 `0.0.0.0:8000` 提供服务。
- [x] 首页、健康检查和确定性 Demo 会话创建均已做真实 HTTP 烟雾验证。
- [ ] 配置真实 `DEEPSEEK_API_KEY` 后执行一次非 Demo 的端到端生成。

## P0：上线前必须补齐

### 1. 数据库账号与会话隔离（正式内测版已完成）

现状：已实现用户名/密码注册登录、scrypt 密码哈希、数据库登录 session，以及
`mpos_session` HttpOnly Cookie。session、artifact、permission 和 billing 已按
数据库用户 UUID 隔离，跨用户访问统一返回 404。浏览器不能通过 header 或 query
参数选择计费身份。每个新账号获得 50 点，每个 revision 消耗 10 点。

边界：第一版开放注册，没有邮箱验证、密码找回、邀请码或付费。多账号仍能绕过每
账号 50 点限制；成本异常时再加邀请码或邮箱验证。Render 部署使用 Supabase
PostgreSQL 保存账号/点数，Supabase Storage 保存 session/artifact。

### 2. 真实 Runner/Skill 执行层与结果 Schema

现状：部分实现。`MposSkillAdapter` 只读取 `SKILL.md` frontmatter 和 hash；除生成
阶段外，analysis/prepare/test/package/deploy/publish 大多由
`SessionService` 硬编码结果。`runner/schemas/` 目前只有协议 envelope，没有各阶段
result JSON schema，也没有通用 ProtocolDispatcher/executor 边界。

验收：为七个阶段补 schema 校验；阶段通过统一 runner 接口执行；每个阶段失败都
产生结构化 result、phase_complete、checkpoint 和 artifact manifest；禁止把任意
模型文本或 shell 当执行入口。

### 3. 持久任务、服务端超时与重启恢复

现状：部分实现。checkpoint 和 session 文件已落盘，但运行任务只存在进程内
`asyncio.Task`；服务重启会丢失 in-flight task。请求模型声明的 `timeout_seconds`
没有被 SessionService 使用，能力接口却返回 `timeout=true`。

验收：持久任务队列/worker；启动时恢复或明确终止遗留任务；用服务端 deadline
包住完整阶段；超时写 `SCRIPT_TIMEOUT`/`timeout`，保留现场并可 retry/resume；补
重启恢复和超时测试。

### 4. 完整 App 元信息与语言归一化

现状：部分实现。后端模型已有双语名称、描述和 release notes 字段，但前端表单只
收集包名、显示名、publisher、version。`prompt_language` 直接跟随 UI 语言，不能
识别英文/混合输入；翻译不是独立可降级步骤，失败时不会按设计返回
`TRANSLATION_WARNING` 后继续。

验收：补 category、图标、双语 short/long description、release notes 表单和
自动补全；语言检测与 UI locale 解耦；保留 original/normalized 双视图；翻译失败
可降级且不阻断生成。

### 5. 前端完整状态、对话和 revision 恢复体验

现状：部分实现。已有 SSE、历史 session、错误复制、retry、日志、revision snapshot
和 diff artifact；但时间线没有单独展示 prepare-deps/deploy，缺少用户/AI/工具对话
视图、工程师 activity log 视图、revision 列表/diff 阅读器和回退上一成功 revision
接口。

验收：七阶段状态完整可见；对话与执行日志分层；可列出、比较、下载并恢复历史
revision；恢复不会覆盖最后成功版本；刷新后 UI 状态一致。

## P1：核心产品补齐

### 6. Desktop smoke 与后端设备执行

现状：部分实现。WASM 和浏览器 WebSerial 已实现；当前部署环境没有 Desktop SDL
binary，也未安装/启用后端 `mpremote`，后端串口扫描固定返回 unsupported。
`DeviceService` 声明了锁但没有实际用于跨 session 串行化设备操作。

验收：构建并登记 desktop binary；接入受控 mpremote 扫描/复制/安装；按设备标识
实现锁和取消释放；正确区分 probe、device-copy、mpk-install、launch 结果。

### 7. 真正的仿真项目发布库

现状：未实现。目前是 3 个硬编码、确定性 Demo 的 Run/Remix 入口，不是用户发布
项目库。没有作者/标签/媒体/版本页面、项目发布 API、热门统计、举报、审核、下架或
活动专区。

验收：项目实体与版本、公开范围、发布/下架工作流；Run/Deploy/Remix/uPyStore
入口；审核举报；素材授权和 artifact provenance manifest。

### 8. 点数充值与管理员加点

现状：未实现且当前产品明确关闭。已有 50 初始点数、每次生成扣 10 点和持久流水，
但没有充值面板、收款确认、管理员加点 API、充值记录、退款/异常说明；后端没有
购买、充值或订阅激活接口。

验收：如果继续采用设计文档 26.3，先实现受认证的人工加点和完整审计；生产环境
不得由客户端声明付款成功。若决定不做，应更新设计文档，明确从产品范围移除。

### 9. 发布与宣传演示增强

现状：部分实现。已有截图校验、发布检查、材料 ZIP、session/demo bundle、三种 demo
seed 和错误注入；缺少 4-9 App showcase、用户测评素材包、多设备 demo state、素材
真实/模拟/概念来源清单和 App 图标生成。

验收：导出包带 provenance manifest；showcase 可稳定复现；多设备记录版本与部署
结果；概念演示有不可移除的标识。

## P2：明确后续能力

- [ ] uPyStore 同名 App/release 查询；正式 API 存在后再做 OAuth/token 与自动上传。
- [ ] 多用户团队协作、评论和任务分配。
- [ ] 仿真项目热度、搜索、推荐和活动专区运营能力。
- [ ] 前端组件测试、真实浏览器 E2E、WebSerial mock 与部署回归测试。

## 下一轮入口

下一步优先处理 **P0-3 持久任务、服务端超时与重启恢复**。数据库用户隔离和静态
session/artifact 重启恢复已满足正式内测底线，但运行中的 `asyncio.Task` 在进程
重启时仍会丢失。
