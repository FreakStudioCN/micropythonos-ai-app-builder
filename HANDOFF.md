# HANDOFF — micropythonos-ai-app-builder 接手与国内自托管部署

最后更新：2026-07-28。上一段工作在 blockless 工作区的 Claude Code 会话里完成。

## Goal（目标）

FreakStudioCN 从 erkou111 手里接手本项目（AI 生成 MicroPythonOS 小应用的网站，现生产 https://blockless-make-app.onrender.com/ 在协助方 Render 账号下），改为**部署在国内**：与插件后端 mpyhw-api **同一台生产机**、复用现有 Caddy、新开 upypi.net 子域名，compose 栈 = app + Postgres + MinIO，CI/CD = GHCR + upypi 自托管 runner。前端只允许小幅美化（部署跑通后单独 PR），逻辑/接口/`mpos-web` 不动。

**权威计划文件：`docs/deployment-domestic-plan.md`（v4）** — 经 Codex 三轮独立 review + 两次复核拿到 GO，一切实施以它为准。

## Current Progress（当前进度）

**已完成：**

1. **交接清理**（已在 GitHub 上生效）：确认仓库是 **transfer 非 fork**（`erkou111/...` 只是重定向，FreakStudioCN 持有 admin）；已删仓库 secret `RENDER_API_KEY`、删 `deploy-main.yml`（main 上 commit `2d584d5`）、移除 erkou111 协作者权限。
2. **计划**：`docs/deployment-domestic-plan.md` v4 定版（三轮 Codex review 迭代出的版本，含 source_sha 绑定、事务部署、MinIO 最小权限收敛、显式接受风险清单、上线序列门禁）。
3. **实施**：分支 **`deploy/domestic`**（4 个 commit：`4d770a5`、`895ff24`、`edac7e1`、`9972079`），draft **PR #9**（⚠️ 注明切换日前不许合并）。改动：
   - `deploy/compose.yml`（digest-pin 的 app/postgres:16.14/minio + minio-init 收敛脚本 + 加固/限额/日志轮转）、`deploy/Caddyfile.snippet`、`deploy/env.example`（MPOS_IMAGE digest pin 模板）
   - `.github/workflows/publish-image.yml`（build + 非 root 容器冒烟 + GHCR；deploy job 门在 CI 成功后经 `workflow_run` 且绑定 `head_sha`；事务化 `.env` 更新，任何失败/校验不过都回滚旧栈；无 PR 触发——红线）
   - `ci.yml` action SHA pin、`.github/CODEOWNERS`、Dockerfile（非 root 用户 + COPY provision_superadmin.py）、`backend/app/cors.py`（`MPOS_ALLOW_DEV_ORIGINS` 门）+ `runtime_flags.py`（行数闸门要求的拆分）+ `tests/test_cors_config.py`、`backend/.env.example` 去 Render/Supabase 化、删 `render.yaml` 与旧部署文档
4. **验证**：本地 CORS 测试 3/3、runner 5/5；GitHub 上 CI + publish-image 在 `895ff24`、`edac7e1` 均双绿（最后一个 commit `9972079` 的 CI 结果需确认，是纯 workflow 脚本微调）；GHCR 已有构建产物镜像。Codex 对实现 diff 的 review：0 blocker，5 条 findings 全部修复并复核，最终 **GO**。

**未完成（这就是接下来的活）：** 生产机侧的一切（见 Next Steps）。

## What Worked（有效的做法）

- **Codex 全程独立把关**：计划三轮 + 实现两轮，每轮真实抓到问题（`workflow_dispatch` 不能触发非默认分支 workflow、workflow_run 不 checkout head_sha 会绕开 CI 门、冒烟镜像≠推送镜像、set -e 下赋值行失败跳过回滚等）。调用方式必须走 `codex:rescue` skill → `codex:codex-rescue` 子代理转发（主 agent 手搓 companion 是 bug）；**取结果的坑**：companion 的 `result` 子命令按目录 scope 查注册表经常 miss，直接读 job 状态文件的 `rendered` 字段（路径在 `status <task-id>` 输出的 `Log:` 行，`.log` 换 `.json`）。
- **push 配方**（Windows GCM 会无声挂死）：`GIT_TERMINAL_PROMPT=0 git -c credential.helper= -c "credential.helper=!gh auth git-credential" push`，空 helper 必须排第一。
- 行数闸门（>400 行文件不许再涨）拦 `main.py` 两次：按要求抽内聚模块（`cors.py`、`runtime_flags.py`）即可通过，别删注释凑行数。
- 镜像/action pin 值来源：`gh api repos/<action>/commits/<tag>` 取 SHA；Docker Hub tags API 取 digest。

## What Didn't Work（别再踩）

- **本机 Windows 跑 backend 测试有 13 个既有 error**（`test_billing`/`test_access_control` 的 temp 目录 teardown，`NotADirectoryError`）——**基线（未改代码）就这样**，与本次改动无关，CI ubuntu 上全绿。别去修它，也别被它吓到。
- `git stash -u` + `pop` 会把 `git rm` 已暂存的删除还原成未暂存——第一个 commit 因此漏了两个删除文件，补了第二个 commit。stash 后提交前必须重查 `git status`。
- 我最初把 FreakStudioCN 仓库当成 fork 并写了一整套「为什么要 fork」的理由——错的，是 transfer。有疑问先 `gh api repos/... --jq .fork,.parent`。
- 最初计划让首次部署走 `workflow_dispatch`——GitHub 机制上不可能（workflow 必须存在于默认分支才能 dispatch）。首次部署 = 宿主机手工 `docker compose up -d --wait`，这是主路径不是妥协。

## Next Steps（按顺序，计划文件「首次上线序列」是权威版）

1. **确认 `9972079` 的 CI/publish-image 双绿**（`gh run list --branch deploy/domestic`）。
2. **人工待办（用户/ops 侧，代码做不了）**：
   - 通知 erkou111：轮换他的 Render API key；在 Render 面板关掉老服务的原生 Auto-Deploy。
   - 找 ops owner（ben0i0d）要：新栈上机同意、子域名定名+DNS、`caddy_net` 真实网络名、`/srv/mpos` 目录、备份纳管（`pg_dump` + `mc mirror`，离机目的地）。
   - GHCR package 设 public（或服务器 `docker login`）；GitHub 建 `production` environment + required reviewers；分支保护开 Code Owners 审查。
   - **DeepSeek 控制台设消费上限**（注册无闸门，每号 50 点是真钱；切换日前必须拍板邀请码/限流）。
3. **宿主预检 + 首次手工部署 + 验收**：全按 `docs/deployment-domestic-plan.md` 的「首次上线序列」1-9 步走（版本门、CHANGE-ME 扫描、先验后端再暴露、superadmin `compose exec` 提升、真实生成 50→40、down/up 持久化、备份恢复演练、digest 回滚演练）。
4. **切换日**：验收全过 + 注册闸门已拍板 → PR #9 转 ready 并合并 main → 自动链路（CI→workflow_run→build→deploy）完整验一遍 → 老站下线。
5. **前端美化 PR**（部署跑通后）：样式/配色/文案/默认 publisher `erkou111`→FreakStudio；禁改逻辑、接口、`mpos-web`、生成/计费/认证。

## 红线（任何后续工作不得违反）

- `publish-image.yml` 及任何触碰 upypi runner 的 workflow **永不加 `pull_request`/`pull_request_target` 触发**；自托管 job 不 checkout 仓库、不跑仓库脚本。
- 过渡期 **main 冻结**（= 老站代码快照），一切新改动进 `deploy/domestic`。
- 填了密钥的 `compose.yml` 绝不入库；仓库里只有 CHANGE-ME 模板。
- `DEEPSEEK_API_KEY` 是需求澄清接口的硬依赖（不只是 health 显示），生产必配。
