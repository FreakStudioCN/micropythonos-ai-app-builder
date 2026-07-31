# 国内自托管部署计划（定版 v4）

状态：**已定版，待实施**。v1 2026-07-28；v2 吸收 Codex review R1；v3 吸收 R2（CD 触发机制重设计、首次部署改宿主手工、供应链 pin、显式接受风险清单）；v4 吸收 R3（**source_sha 绑定 CI 通过的 commit**、ci.yml action pin、MinIO 轮换契约修正、multipart 权限、备份口径修正、密钥字符集、Compose 版本前提、tag 触发）。
v5 改为**并入现有栈**（见下节）。本文件取代 Render/Supabase 路线（`render.yaml`、`docs/deployment-render-supabase.md` 随实施 PR 废弃）。

## v5 变更：并入现有 compose，不再另起独立栈

v1–v4 假设新开 `/srv/mpos` 独立栈。**v5 改为把 mpos 的四个服务并进生产机上已经在跑的那份 compose.yml**（即已承载插件后端 `mpyhw-api` 的那份），一份文件、一次 `docker compose up`、一次交接。**以下 v4 正文中所有 `/srv/mpos`、"独立栈"、`db`/`minio`/`minio-init` 服务名，一律以本节为准。**

### 命名前缀是硬要求，不是风格

宿主那份 compose 已经定义了 `db`、`pgdata`、`internal`。原方案的块里有同名键——**YAML 重复键不报错，后者静默覆盖**。已实测：直接粘贴原块，合并结果只剩 5 个服务，`db` 变成 mpos 的 postgres，`volumes.pgdata` 被重指到 `mpos_pgdata`，**插件后端会带着错误的库和错误的数据卷起来**。故全部改名：

| 原 | 现 | 为什么 |
|---|---|---|
| `db` | `mpos-db` | 与插件后端的 `db` 服务碰撞 |
| `minio` / `minio-init` | `mpos-minio` / `mpos-minio-init` | 防御性，宿主文件将来可能有 |
| `internal` | `mpos_internal` | 与 `internal: name: mpyhw_internal` 碰撞 |
| `pgdata` | `mpos_pgdata` | 与 `pgdata: name: mpyhw_pgdata` 碰撞 |
| `edge`（external `caddy_net`） | 改为 `default` | **该网络不存在**，见下 |
| `name: mpos` | 删除 | 宿主文件无顶层 `name:`，项目名取自目录名 |

### 宿主实际架构（2026-07-31 拿到真文件后更正，v1–v4 全部搞错了）

**Caddy 是这个 stack 内的一个服务**，`ports: 80/443`，挂载 `./Caddyfile` —— 不是外部反代。**整份文件没有任何 `networks:` 段**，四个服务全在 compose 的隐式 `default` 网络上，Caddy 靠服务名解析。所以：

- **没有 `caddy_net`**，`edge` 引用会直接报错；
- 一旦某服务声明了 `networks:`，它就**脱离 `default`**，Caddy 再也解析不到它。故 `mpos-app` 必须显式列 `default`；`mpos-db`/`mpos-minio`/`mpos-minio-init` 只在 `mpos_internal`，隔壁服务够不到。

实跑的服务是 `db`(postgres:16-alpine)、`mpyhw-api`、**`upypi`**（`ghcr.io/freakstudiocn/upypi:main`，服务 upypi.net 包索引，此前不知道）、`caddy`；卷是 `postgres-data`、`upypi-db`、`upypi-pkgs`、`caddy-config`、`caddy-data`。

验证方式：与**真文件**合并后无键碰撞，`docker compose config` 通过，8 服务 / 7 卷齐全，他四个服务的 image 与网络均未变。

### ⚠️ 未决：镜像源可能拉不动

他 stack 里**每一个 Docker Hub 镜像都走了华为云镜像站**（`swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/{postgres,caddy}`），只有自家 GHCR 镜像直连。强烈提示那台机拉不动 Docker Hub。

本方案钉的 `postgres:16.14@sha256:…` 与 `minio/minio@sha256:…` **都是 Docker Hub digest，大概率拉不下来**。`ddn-k8s/docker.io/minio/minio` 是否存在**未经验证**，不要当成事实。落地前必须由 ops owner 实测 `docker pull`。附带后果：走镜像站则 digest 与 Docker Hub 不同，v4 的"digest pin"供应链要求需要重新取值。

### 保留独立 postgres 容器（推翻"复用现有 pg"）

复用宿主已有的 pg 需要**人工 psql 进生产库执行 `CREATE DATABASE`/`CREATE ROLE`**——postgres 的 init 脚本只在数据目录为空时运行，对已有数据的卷不生效。而独立容器由 `POSTGRES_*` 在空卷上自举，**零宿主往返**。这台机不是我们的，每次"麻烦你上机跑一条命令"都是一轮往返；1G 内存比一轮往返便宜。

### 密钥不再手工填

新增 `deploy/render-compose-block.sh`：生成全部 5 个凭据并写入所有必须一致的位置，末尾断言无残留 `CHANGE-ME`。外部签发的 `DEEPSEEK_API_KEY` 校验 `^sk-[A-Za-z0-9_-]+$`，不符合直接拒绝渲染（含 `$`/`|` 会破坏 compose 插值）。**禁止手工填任何一半**：四组配对值不一致不会在编辑期报错，只会变成起不来的容器或一小时后的 S3 403。

### CD job 的三处相应改动

栈共享之后，原 deploy job 有两处会误伤邻居：

1. **`.env` 整文件覆盖** → 改为**只重写 `MPOS_IMAGE` 那一行**，且**原地写入**（不用临时文件 + `mv`，那会改掉文件的属主与权限——该文件可能属于别的账号）。已实测：邻居行在部署与回滚后均完好，inode 不变。缺 `MPOS_IMAGE` 行则大声失败（首次部署是宿主手工，由它播种）。
2. **`docker compose up -d --wait` 无 service 参数** → 改为 `... --wait mpos-app`，否则每次 mpos 部署都会重启插件后端。**永远不要加 `--remove-orphans`**，它会拆掉邻居服务。
3. 栈目录不再写死 `/srv/mpos` → 读仓库变量 `MPOS_STACK_DIR`，未设置则 job 直接失败。

### 接受的新代价

一份 compose 意味着**爆炸半径合并**：该文件语法错误会同时挡住两个服务的部署，宿主上手滑的 `up -d`（不带 service 名）会重启插件后端。以 service 级 scope + 上面的注释缓解，不做机制隔离。

## 已拍板的决策

| 决策点 | 结论 |
|---|---|
| 部署位置 | 与插件后端（mpyhw-api）**同一台生产机**，复用现有 Caddy，新开 upypi.net 子域名（暂记 `mpos.upypi.net`，待 ops owner 定名） |
| 对象存储 | **MinIO 容器**（`object_storage.py` boto3 SigV4 + path-style，兼容已核实） |
| 数据库 | compose 内 **postgres 容器** + named volume |
| CI/CD | GitHub runner 构建镜像 → GHCR → **upypi 自托管 runner** 部署；**deploy 必须门在 CI 成功之后**（workflow_run 模式） |
| 前端 | 仅小幅美化（样式/配色/文案/默认 publisher），部署跑通后单独 PR；逻辑/接口/`mpos-web` 不动 |

## 过渡期策略（新老并行）

老站（blockless-make-app.onrender.com，协助方 Render 账号下）持续运行，不受本计划影响。

1. **main 冻结**：过渡期内不合并任何改动；切换日一次性合并 `deploy/domestic`。
2. **新工作全在分支 `deploy/domestic`**。分支 push 只构建镜像（含容器冒烟），**不部署**。
3. **首次生产部署 = 宿主机手工执行**（`workflow_dispatch` 只能触发默认分支上已存在的 workflow，分支阶段机制上不可用；手工路径见「首次上线序列」，这是主路径不是兜底）。
4. 合并 main 后的常态 CD：`CI 成功(main) → workflow_run → build → deploy`；`workflow_dispatch` 仅允许 main ref 且过 `production` environment 审批。
5. 过渡期给老站发修复：erkou111 在其 Render 面板手动 deploy；本仓库不持有其密钥。
6. 待 erkou111 确认：关闭老服务的 Render 原生 Auto-Deploy。

## 已完成的交接清理（2026-07-28）

- 仓库为 **transfer 非 fork**，FreakStudioCN 持有 admin。
- 已删 secret `RENDER_API_KEY`、已删 `deploy-main.yml`（`2d584d5`）、已移除 erkou111 协作者权限。
- 待办（人工）：通知 erkou111 轮换其 Render API key。

## 目标架构

```text
push deploy/domestic ──────────────→ image job（构建+容器冒烟+推 GHCR，输出 digest）
main: CI 成功 → workflow_run ──────→ image job → deploy job（upypi runner）
workflow_dispatch（仅 main + 审批）─→ 同上

生产机 <MPOS_STACK_DIR>/compose.yml（已存在，承载插件后端；本方案向其追加四个服务）:
  mpyhw-api        （已在跑，不动）
  db               （已在跑，插件后端的 postgres，不动）
  mpos-app         ghcr.io/freakstudiocn/micropythonos-ai-app-builder@<digest>（:10000 仅内网）
  mpos-db          postgres:16.<pin>（mpos_pgdata 卷）
  mpos-minio       minio/minio:<pin>（mpos_miniodata 卷，仅内网）
  mpos-minio-init  一次性 mc 容器（建桶 + app 专用 key，幂等）
入口: 栈内 caddy 服务（default 网络）→ mpos.upypi.net → mpos-app:10000
```

实施时所有 pin 必须落成**具体值**：postgres/minio 解析到 digest、GitHub Actions 各 action 按 commit SHA 引用、`MPOS_IMAGE` 永远是 `@sha256:` digest。

## 仓库改动清单（实施分支 `deploy/domestic`，6 件）

### 1. `deploy/compose.yml`（新）

全部服务 `restart: unless-stopped`（`minio-init` 除外，`restart: "no"`）。

- **`mpos-app`**
  - `image: ${MPOS_IMAGE}`（digest pin，来自现有栈目录的 `.env`）。
  - `expose: 10000`；networks `default`（栈内 caddy 靠它解析）+ `mpos_internal`；不 publish 宿主端口。（v5 更正：原写 `edge`（external caddy 网）+ `internal`，两者都不存在。）
  - healthcheck：`python -c "urllib.request.urlopen('http://127.0.0.1:10000/api/health')"`。
  - `depends_on`: `db: service_healthy`、`minio-init: service_completed_successfully`（`object_storage.py` 导入期 `_ensure_bucket()`、`session_service` 构造期 `restore_all()`，顺序靠编排保证，不改代码兜底）。
  - 加固：非 root 用户运行（见第 6 件）、`cap_drop: [ALL]`、`security_opt: [no-new-privileges:true]`；**限额给数**：`mem_limit: 2g`、`cpus: 2`、`pids_limit: 512`；日志 `json-file` + `max-size: 10m, max-file: "3"`（所有服务同）。不挂 docker socket，无 privileged。
- **`db`**：`POSTGRES_USER=mpos`、`POSTGRES_DB=mpos`、`POSTGRES_PASSWORD`（与 `DATABASE_URL` 完全一致，特殊字符 percent-encode）；`pg_isready` healthcheck；`mem_limit: 1g`；仅 `internal`。
- **`minio`**：`MINIO_ROOT_USER/PASSWORD`（仅 bootstrap）；healthcheck `/minio/health/live`；`mem_limit: 1g`；仅 `internal`，API/Console 均不暴露。
- **`minio-init`**（`depends_on: minio: service_healthy`）：
  - `mc alias set` root 凭据；
  - `mc mb --ignore-existing local/mpos-sessions`；
  - 创建/更新 app 专用 access key，**收敛语义而非跳过语义**（MinIO 不可读回已存 secret，比对机制为**实测认证**）：先用目标 app 凭据 `mc alias set` 试认证——成功 → 凭据已收敛，保留；失败且用户存在 → **删除重建（rotate）**为目标凭据；用户不存在 → 创建；每次运行都重新附 policy；
  - 附**最小权限 policy（实现必须内联具体 JSON）**：该桶的 `s3:ListBucket`、`s3:GetObject`、`s3:PutObject` + **multipart 所需动作**（`s3:AbortMultipartUpload`、`s3:ListMultipartUploadParts`、`s3:ListBucketMultipartUploads`——boto3 `upload_file()` 超过 ~8MiB 走 multipart，截图上限 10MiB 必触发）；**不给 delete-object、不给 CreateBucket**（建桶由 init 唯一负责，代码 404 兜底路径不可达即为预期）；
  - **末步自验**：用 app 凭据 `mc alias set` + list 该桶，失败则 init 以非零退出（`depends_on` 保证 app 不会带着坏凭据启动）；
  - 全程幂等可重跑；**轮换只换 secret，access key ID 固定不变**（ID 即 MinIO 用户名，改 ID = 换人不是轮换）；如确需换 ID，必须显式加「撤销旧用户」步骤，禁止留旧凭据。app key 轮换 = 改 compose 中 secret → 重跑 init（实测认证失败→删除重建+自验）→ `up -d` 重启 app。

`mpos-app` env 内联（`<CHANGE-ME>` 装填规则见后）：

```text
DATABASE_URL=postgresql://mpos:<CHANGE-ME>@db:5432/mpos

MPOS_STORAGE_ENDPOINT=http://minio:9000
MPOS_STORAGE_REGION=us-east-1
MPOS_STORAGE_ACCESS_KEY_ID=<CHANGE-ME，minio-init 创建的 app 专用 key>
MPOS_STORAGE_SECRET_ACCESS_KEY=<CHANGE-ME>
MPOS_STORAGE_BUCKET=mpos-sessions

MPOS_REQUIRE_DURABLE_STORAGE=true
MPOS_COOKIE_SECURE=true
MPOS_COOKIE_SAMESITE=lax
MPOS_DEMO_ERROR_INJECTION=false
MPOS_ALLOW_DEV_ORIGINS=false

DEEPSEEK_API_KEY=<CHANGE-ME>            # 硬依赖：需求澄清+legacy 生成路径只读这组
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash        # 以账号实际可用为准
```

同源不设 `FRONTEND_ORIGINS`/`VITE_*`；Storage 五变量 all-or-nothing 为预期，不绕。

**密钥装填与宿主处理**：**自生成密钥**（DB 密码、MinIO root/app key、JWT 类）一律 `openssl rand -hex N`（纯 hex：无 `$` 等 Compose 插值敏感字符，DB 密码天然免 percent-encode）；**外部签发凭据**（`DEEPSEEK_API_KEY`）豁免此规则，装填前校验不含 `$`（DeepSeek key 为 `sk-`+字母数字，天然安全；如未来某外部凭据含 `$`，用 `$$` 转义并在 `docker compose config` 输出中核对渲染值）；加密通道传输；宿主那份**共享** compose.yml 属主部署账号/root、`0600`；不进通用日志采集；备份中按密文对待。绝不入库。**v5：装填一律用 `deploy/render-compose-block.sh`，不手工填**。

### 2. `deploy/Caddyfile.snippet`（新）

```text
mpos.upypi.net {
    reverse_proxy mpos-app:10000
}
```

### 3. `deploy/env.example`（新，模板）→ 追加到宿主**现有栈目录**的 `.env`

仅一行非密 pin：`MPOS_IMAGE=ghcr.io/freakstudiocn/micropythonos-ai-app-builder@sha256:<digest>`。

**deploy job 的事务契约**：先按 digest pull 成功 → 记下旧值、写入新值 → `docker compose up -d --wait`；若失败，**恢复旧值并 `up -d --wait` 回到旧栈**，job 以失败退出。`.env` 与实际运行容器的 digest 由部署后 `docker inspect` 强制一致校验。回滚 = 改回上一已验证 digest + `up -d`。

### 4. `.github/workflows/publish-image.yml`（新）

- 触发：
  - `push: branches [deploy/domestic]` → 仅 image job（构建验证）；
  - `push: tags ['v*']` → 仅 image job（出 semver 镜像，不部署）；
  - `workflow_run: workflows [CI], types [completed], branches [main]` → CI **成功**才 build+deploy（deploy 从此门在测试之后）；
  - `workflow_dispatch` → 仅当 `github.ref == 'refs/heads/main'`。
  - **🔒 永不添加 `pull_request`/`pull_request_target`**；自托管 job 不 checkout、不跑仓库脚本。
- **`source_sha` 绑定（CI 门的完整性所在）**：每种触发先解析唯一 `source_sha`——`workflow_run` 路径**必须**取 `github.event.workflow_run.head_sha`（即真正通过 CI 的 commit），push/tag 取 `github.sha`，dispatch 先用 `gh api` 校验该 sha 的 CI conclusion==success 再继续。**checkout、构建、`sha-<source_sha>` 打标、部署审计输出全部用这一个 sha**——普通 checkout 会拿到 main 当前 HEAD，可能是未过 CI 的更新 commit，等于绕开 CI 门。
- **`image` job**（ubuntu-latest，`timeout-minutes: 30`，权限 `contents: read, packages: write, actions: read`——`actions: read` 供 dispatch 路径查 CI conclusion）：checkout `source_sha`（`submodules: false`）→ 仅 `git submodule update --init vendor/MicroPython_Skills` → buildx 构建根 `./Dockerfile` → **容器冒烟**：以默认 env 起容器（本地 sqlite/文件模式），非 root 身份确认 + `curl /api/health` 200 → push GHCR（`latest`@main、`sha-<source_sha>`、tag 触发时 semver）→ **将 `source_sha` 与 `digest` 一并声明为 job outputs**（deploy job 经 `needs.image.outputs.*` 消费，两条链都闭合）。所有 action 按 commit SHA pin。
- **`deploy` job**（`runs-on: [self-hosted, Linux, X64, upypi]`，`needs: image`，`timeout-minutes: 15`，权限 `contents: none, packages: read`，`environment: production`（含 required reviewers 审批），条件：workflow_run-成功路径或 main 的 dispatch）：接收 `source_sha` 与 digest 为显式输入 → 临时 `DOCKER_CONFIG` 登录 GHCR（结束即删；package public 后可免）→ 按第 3 件的事务契约执行 → digest 一致校验 → **审计输出：`deployed source_sha=<sha> digest=<digest>` 写入 job summary**（部署账本，与 `.env` 交叉对证）。
- `concurrency`: 固定组 `production-deploy`，`cancel-in-progress: false`。
- Runner 治理（实施时一并做）：runner 限定仅本仓库；`.github/workflows/**` 设 CODEOWNERS + branch protection。

### 5. 清理 Render/Supabase 残留

- 删 `render.yaml`；`docs/deployment-render-supabase.md` 删除或标注废弃。
- 改写 `backend/.env.example`：存储段改为通用 S3/MinIO 表述；去掉 Render 字样；`MPOS_API_TOKEN` 标注未实现；**补 `MPOS_ALLOW_DEV_ORIGINS` 文档**。
- `ci.yml`：逻辑不动，但其 `actions/checkout@v6`、`setup-python@v6`、`setup-node@v6` **一并 pin 到 commit SHA**——deploy 信任 CI 的结论，CI 里的可变 action 就是生产供应链的一部分，不能只 pin 新 workflow。

### 6. 小幅后端/镜像改动（同一实施 PR，各带验证）

- **Dockerfile**：`COPY scripts/provision_superadmin.py …`；新增非 root 用户，`MPOS_SESSION_ROOT` 等可写目录 chown。验证不靠「CI 绿」空话——**image job 的容器冒烟在非 root 下起服务**即为门。
- **`backend/app/main.py` CORS**：新增 `MPOS_ALLOW_DEV_ORIGINS`（默认 `true` 不破坏本地开发；`false` 时不注入 4 个 localhost 源）。唯一后端逻辑改动，带单测。

## 首次上线序列（顺序即门禁）

1. 实施 PR 于 `deploy/domestic` → CI 绿 → 分支 push 的 image job 出镜像与 digest（含非 root 冒烟）。不合 main。
2. GHCR package 设 public（或服务器一次性 login）。
3. **宿主预检**（ops owner + 我们）：**版本前提**——Docker Engine ≥ 24、Compose plugin ≥ v2.20（计划依赖 `service_completed_successfully` 与 `up --wait`，`docker compose version` 实测确认）→ **先备份现有 compose.yml** → 把 `render-compose-block.sh` 的输出粘进现有 compose.yml 的 `services:`/`networks:`/`volumes:` 三个映射 → 在同目录 `.env` 追加 `MPOS_IMAGE`（填 digest）→ `docker compose config` 通过**且输出里插件后端的 `db`、`pgdata`、`mpyhw_internal` 均未改动** → 无残留 `CHANGE-ME` 扫描。
4. **首次部署 = 宿主手工** `docker compose up -d --wait mpos-app`（**带 service 名**，否则会重启插件后端；且不加 `--remove-orphans`）：`ps` 全 healthy、日志干净（无 storage 缺项/S3 403/DB auth 错误），并确认 `mpyhw-api` 未被重建。
5. **先验后端再暴露**：`docker compose exec caddy wget -qO- http://mpos-app:10000/api/health` 通过（Caddy 在栈内，从它自己容器里验才是真链路）→ 编辑与 compose.yml 同目录的 `Caddyfile` 追加 vhost → `docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile`（**别 `up -d` 或重启 caddy 服务**，那会连带影响另外两个站）→ 最后 DNS（提前压 TTL）。
6. 冒烟：health 四项 + 首页 + `/mpos-web/` + 显式 DB 读写（注册）+ 显式 MinIO 往返（session 对象出现且可读回）。注意 `/api/health` 只报配置不做实时依赖探活——监控不得只信它。
7. superadmin：`docker compose exec mpos-app python scripts/provision_superadmin.py --target production --username <user> --promote-existing` → 权限矩阵抽查（superadmin 200 / 普通 403）。
8. 真实生成：澄清 → 生成（50→40）→ MPK 下载 → WASM 预览。
9. **持久化/备份/回滚演练（验收硬门）**：
   - `down && up -d` 数据仍在、session 从 MinIO 恢复；
   - `pg_dump`（DB：账号/登录/计费的权威源）+ **MinIO 用 `mc mirror` 导出**（session 状态与 artifact 的权威源——`session_state.json` 等就住在对象存储里，**不做在线卷目录拷贝**）→ **在一次性栈恢复并登录成功**；备份落**离机/独立盘**目的地，定保留期；两者非同刻一致，**明确接受的偏斜口径**：账号/点数以 DB 备份为准，session 以 MinIO 备份为准，互相引用缺失时（如 session 在而 artifact 缺、或反之）应干净报错，接受的数据丢失窗口 = 备份间隔；恢复演练至少覆盖「DB 旧于 MinIO」「MinIO 旧于 DB」两个方向各一次登录+开 session 验证；
   - digest 回滚演练：改 `.env` → `up -d` → 登录/计费/生成正常 → 滚回。当前 schema 仅增量（`create_all` + `auth.py` 手写补列），**未来任何非增量变更前必须先补 Alembic**，届时回滚演练要含 DB 恢复；
   - 宿主端口审计：DB/MinIO/app 无任何公网绑定。
10. 观察期 → **切换日前置门：注册闸门拍板**（见风险 1）→ 合并 main → 完整自动链路（CI→workflow_run→build→deploy）再验一遍 → 老站下线（erkou111）。

## 需要 ops owner（ben0i0d）确认/提供

- **同意把四个服务并进现有 compose.yml**（而不是另起独立栈），以及随之而来的爆炸半径合并。
- **现有 compose.yml 与 Caddyfile 的当前内容**——仓库里那份是模板不是实跑文件，我们无法读取生产机。粘贴前需据实核对是否已有同名 service/network/volume。
- 现有栈目录的绝对路径（填进仓库变量 `MPOS_STACK_DIR`）。（`caddy_net` 一项作废：无此网络，见 v5。）
- **实测 `docker pull` 这两个镜像**：`.../ddn-k8s/docker.io/minio/minio:RELEASE.2025-09-07T16-13-09Z` 与 `.../minio/mc:RELEASE.2025-08-13T08-35-41Z`。路径是按他 postgres/caddy 的命名规律推的，**未验证**——匿名探 registry 一律 401（连已知存在的 `library/postgres` 也 401），外部证不了。拉不动就要他给可用路径或换自建镜像。（postgres 已改用他正在跑的那个 `library/postgres:16-alpine`，无此风险。）
- **实测 MinIO 镜像里有没有 `curl`**：`docker run --rm --entrypoint sh <minio镜像> -c 'command -v curl mc'`。没有的话 healthcheck 永远不过 → init 不跑 → app 不起 → `up --wait` 超时，**症状是"卡住"不是"健康检查配错"**。届时改用 `mc ready local`。
- 新服务上机同意；磁盘预算（镜像+Postgres+MinIO 随生成量涨）。
- 子域名定名 + DNS（切换前压 TTL）。
- runner 限定到本仓库（label 只是路由不是边界）。
- 备份纳管：`pg_dump` + `mc mirror`（不做在线卷拷贝），**离机目的地 + 保留期 + 恢复演练**；备份按密文对待。

## 显式接受的风险（架构选型自带，与现有 mpyhw-api 同类，不再当 blocker 追）

1. **自托管 runner 对共享宿主机是 root 级通道**：允许的 workflow 一旦被攻破即可控整机（含隔壁 mpyhw-api 栈）。缓解：runner 仅限本仓库、deploy job 不 checkout 不跑仓库脚本、无 PR 触发、workflows 目录 CODEOWNERS+保护、dispatch 限 main+审批。接受理由：与插件后端同一既有模式，且部署面已收到最小。
2. **compose 内联 env 密钥对任何 docker-capable 用户可见**（`docker inspect` 可读，0600 挡不住）：宿主上有 docker 权限≈有密钥，本来如此；同机 mpyhw-api 同模式。缓解：宿主账号最小化 + 轮换流程。
3. **共享 `default` 网络存在横向网络路径**（v5 更正措辞：不是 `caddy_net`）。`mpos-app` 与 `mpyhw-api`、`upypi`、`caddy`、插件后端的 `db` 同在 `default` 上，被攻破即可达这些网络面——**含插件后端那个未设密码隔离的 `db`**。与现状一致（他们四个本来就互通），不阻塞。已做的收敛：`mpos-db`/`mpos-minio` 只在 `mpos_internal`，反向不可达。

## 已知残留风险（须跟踪）

1. **公开注册无闸门 = 无上限烧 DeepSeek 点数**（每号 50 点、无限流；共享宿主还有资源耗尽面）。**升级为切换日前必须拍板**：邀请码/限流二选一或明确豁免；无论如何**先在 DeepSeek 控制台设消费上限**（立即可做，不改代码）。
2. `DEEPSEEK_API_KEY` 硬依赖为代码事实；只用 PRIMARY/AIGoCode 需改 `requirements_chat.py`（本次不动）。
3. 默认 publisher `erkou111` → 前端美化 PR。
4. `requirements.txt` 存在版本范围、Dockerfile 基础镜像为可变 tag：同 commit 重建产物可漂移。本次仅 pin 部署面（镜像 digest/action SHA/db+minio digest）；应用依赖 lock 列为后续基建。
5. `/api/health` 暴露 DeepSeek key 的 8 位指纹与后端形态：低危信息面，后续可裁剪。
6. 启动 `restore_all()` 全量恢复随 bucket 增长变慢：量大后做清理策略。

## 前端美化 PR（部署跑通后，单独小 PR）

允许：样式、配色、文案、logo、默认 publisher 改 FreakStudio。
禁止：交互逻辑、接口契约、`frontend/public/mpos-web/`、生成/计费/认证代码。
