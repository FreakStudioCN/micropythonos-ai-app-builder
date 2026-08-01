# 国内自托管部署计划（定版 v4）

状态：**已定版，待实施**。v1 2026-07-28；v2 吸收 Codex review R1；v3 吸收 R2（CD 触发机制重设计、首次部署改宿主手工、供应链 pin、显式接受风险清单）；v4 吸收 R3（**source_sha 绑定 CI 通过的 commit**、ci.yml action pin、MinIO 轮换契约修正、multipart 权限、备份口径修正、密钥字符集、Compose 版本前提、tag 触发）。
v5 改为**并入现有栈**（见下节）。本文件取代 Render/Supabase 路线（`render.yaml`、`docs/deployment-render-supabase.md` 随实施 PR 废弃）。

## v5 变更：并入现有 compose，不再另起独立栈

v1–v4 假设新开 `/srv/mpos` 独立栈。**v5 改为把 mpos 的四个服务并进生产机上已经在跑的那份 compose.yml**（即已承载插件后端 `mpyhw-api` 的那份），一份文件、一次 `docker compose up`、一次交接。**以下 v4 正文中所有 `/srv/mpos`、"独立栈"、`db`/`minio`/`minio-init` 服务名，一律以本节为准。**

### 命名前缀是硬要求，不是风格

**YAML 重复键不报错，后者静默覆盖。** 对着**真机文件**核过之后，改动分三类 —— 下表按「是不是真碰撞」分开，别把防御性改名和真问题混为一谈：

| 原 | 现 | 性质 |
|---|---|---|
| `db` | `mpos-db` | 🔴 **真碰撞** —— 他确实有 `db` 服务。不改就会顶替掉插件后端的数据库 |
| `edge`（external `caddy_net`） | 改为 `default` | 🔴 **真故障** —— 该网络根本不存在，`config` 直接报错；且声明任何 `networks:` 就会脱离 `default`，Caddy 解析不到（见下节） |
| `name: mpos` | 删除 | 🔴 **真危险** —— 他没有顶层 `name:`，项目名取自目录名。加一个＝改项目名，**他所有在跑的容器（含 Caddy）立刻变孤儿** |
| `internal` | `mpos_internal` | 🟡 防御性 —— 他一个 network 都没声明，当前不碰撞 |
| `pgdata` | `mpos_pgdata` | 🟡 防御性 —— 他的卷叫 `postgres-data`，当前不碰撞 |
| `minio` / `minio-init` | `mpos-minio` / `mpos-minio-init` | 🟡 防御性 —— 他没有这两个服务 |

> 早期版本这张表声称他有 `pgdata` 和 `internal` 并会碰撞。那是**对着仓库模板**推的，不是真机。真机上只有 `db` 一个真碰撞。防御性改名仍然保留（成本为零，且挡住他将来加同名键），但不要再把它们当成"已发生的故障"来引用。

### 宿主实际架构（2026-07-31 拿到真文件后更正，v1–v4 全部搞错了）

**Caddy 是这个 stack 内的一个服务**，`ports: 80/443`，挂载 `./Caddyfile` —— 不是外部反代。**整份文件没有任何 `networks:` 段**，四个服务全在 compose 的隐式 `default` 网络上，Caddy 靠服务名解析。所以：

- **没有 `caddy_net`**，`edge` 引用会直接报错；
- 一旦某服务声明了 `networks:`，它就**脱离 `default`**，Caddy 再也解析不到它。故 `mpos-app` 必须显式列 `default`；`mpos-db`/`mpos-minio`/`mpos-minio-init` 只在 `mpos_internal`，隔壁服务够不到。

实跑的服务是 `db`(postgres:16-alpine)、`mpyhw-api`、**`upypi`**（`ghcr.io/freakstudiocn/upypi:main`，服务 upypi.net 包索引，此前不知道）、`caddy`；卷是 `postgres-data`、`upypi-db`、`upypi-pkgs`、`caddy-config`、`caddy-data`。

验证方式：与**真文件**合并后无键碰撞，`docker compose config` 通过，8 服务 / 7 卷齐全，他四个服务的 image 与网络均未变。

### 镜像源：已改走华为云镜像站，并已实测（2026-07-31 结案）

他 stack 里**每一个 Docker Hub 镜像都走华为云镜像站**（`swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/...`），只有自家 GHCR 直连——那台机拉不动 Docker Hub。所以原方案钉的 Docker Hub digest 会直接失败，三个镜像全部改走该镜像站。

**本机实拉验证通过**，三个都存在，并由此取到 digest（index digest，跨平台通用），已回填成 pin：

| 镜像 | digest |
|---|---|
| `library/postgres:16-alpine` | `sha256:31482568…3e08` |
| `minio/minio:RELEASE.2025-09-07T16-13-09Z` | `sha256:52dfd5c0…15ff` |
| `minio/mc:RELEASE.2025-08-13T08-35-41Z` | `sha256:bdfae21c…2cbe` |

所以 v4 的 digest-pin 硬门**仍然满足**，不需要"首次部署后回填"这个妥协。

**顺带证伪一条 P1 风险**：MinIO healthcheck 依赖的 `curl` **确实存在**（`/usr/bin/curl`，实跑容器确认），`mc` 也在（`/usr/bin/mc`）。此前担心的"curl 缺失 → init 永不启动 → app 永不启动 → `up --wait` 静默超时"**不成立**。

### 保留独立 postgres 容器（推翻"复用现有 pg"）

复用宿主已有的 pg 需要**人工 psql 进生产库执行 `CREATE DATABASE`/`CREATE ROLE`**——postgres 的 init 脚本只在数据目录为空时运行，对已有数据的卷不生效。而独立容器由 `POSTGRES_*` 在空卷上自举，**零宿主往返**。这台机不是我们的，每次"麻烦你上机跑一条命令"都是一轮往返；1G 内存比一轮往返便宜。

### 密钥不再手工填

新增 `deploy/render-compose-block.sh`：生成全部 5 个凭据并写入所有必须一致的位置，末尾断言无残留 `CHANGE-ME`。外部签发的 `DEEPSEEK_API_KEY` 校验 `^sk-[A-Za-z0-9_-]+$`，不符合直接拒绝渲染（含 `$`/`|` 会破坏 compose 插值）。**禁止手工填任何一半**：四组配对值不一致不会在编辑期报错，只会变成起不来的容器或一小时后的 S3 403。

### CD job 的四处相应改动

栈共享之后，原 deploy job 有两处会误伤邻居：

1. **`.env` 整文件覆盖** → 改为**只重写 `MPOS_IMAGE` 那一行**，且**原地写入**（不用临时文件 + `mv`，那会改掉文件的属主与权限——该文件可能属于别的账号）。已实测：邻居行在部署与回滚后均完好，inode 不变。缺 `MPOS_IMAGE` 行则大声失败（首次部署是宿主手工，由它播种）。
2. **`docker compose up -d --wait` 无 service 参数** → 改为 `... --wait mpos-app`，否则每次 mpos 部署都会重启插件后端。**永远不要加 `--remove-orphans`**，它会拆掉邻居服务。
3. 栈目录不再写死 `/srv/mpos` → 读仓库变量 `MPOS_STACK_DIR`，未设置则 job 直接失败。
4. **每个 docker 调用都用 `timeout` 兜住**（2026-08-01 首次真实运行打脸后加的，不是预防性设计）。

### 首次真实部署的事故：挂死的 pull 不是失败的 pull

合并当天 CD 第一次跑就挂了，值得单列，因为它推翻的是一条**我自以为已经防住了**的风险：

```
06:16:28  2fb9654321e5: Download complete     ← 9 层里 8 层，11 秒拉完
06:31:26  ##[error] The operation was canceled ← 最后一层挂了 15 分钟，job 超时
```

为此专门写的三次重试**一次都没触发**。原因不是参数不对，是机制选错了：`if docker pull …; then` 只能观察到**非零退出**，而挂死的 pull 根本不退出，循环就卡在第一轮里。

这和 compose 注释里那条 MinIO healthcheck 缺 `curl` 的风险是**同一种失败形状**——静默挂起而不是大声报错，症状是「卡住」不是「配错」。我在那边认出来了，在这边没认出来。`timeout` 把「永远不返回」变成「exit 124」，重试才接得住它本来就该接的东西。

修完是**行为验证**的，不是看代码看对的：挂死 → 三次有界重试 → exit 1；先挂后成 → 第 2 次成功；纯失败 → exit 1；正常 → 第 1 次过。反过来拿旧写法跑同一个挂死场景，8 秒内零输出、只能从外部杀掉，坐实重试从未触发。

`docker compose up --wait` 同样包了，而且那里不是假想：首次部署时**正是这个 `up` 去镜像站拉 postgres/minio/mc**，能以完全相同的方式挂在某一层上，而 `--wait` 自己没有任何时限。回滚那次 `up` 也包了——回滚挂死会烧光剩余预算，并把栈丢在重启到一半的状态、日志里还没有任何线索。

job 上限相应从 15min 提到 25min，必须大于内层之和（390s + 420s + 420s），否则先炸的是 job 上限，那些具体的 timeout 信息根本打不出来。

修完立刻用真实运行验证了一遍，行为完全符合设计——7 分钟大声失败，取代 15 分钟静默挂死；`removed temporary docker config /tmp/tmp.CxpPmKESGm` 也证实退出处理器确实跑了（上次取消后无法证实这一点）。

### 🔴 但真正的问题不是超时，是 GHCR 从这台机拉不动（未解决）

修复生效之后，暴露出底下压着的东西：**三次有界重试全部挂死**，这不是偶发波动。

| 尝试 | 下完 | 卡住的层 |
|---|---|---|
| 1/3 | 8/9 | `6fd774763a57` |
| 2/3 | 8/9（含 6fd7，且已有 7 层解压完成） | `76dd1e712a48` |
| 3/3 | 8/9 | `6fd774763a57` 又卡 |

三个事实决定了性质：

1. **每次卡的层不一样**——不是某个 blob 损坏，是随机某条连接建立后被黑洞（不返回也不报错）。docker 自身没有停滞检测，所以它会一直等。
2. **每次都能下完 9 层里的 8 层**，第 2 次甚至已经解压完 7 层——离成功非常近，但总差最后一口。
3. **三次尝试之间进度不累积**：`timeout` 发 SIGTERM 后 docker 会回滚未完成的拉取，所以第 3 次又得从头下 8 层。加大重试次数换不到累积收益。

这与 ops owner 说的「github 有时候因为墙会波动」一致，也解释了为什么**他 stack 里每一个 Docker Hub 镜像都走华为云镜像站**——这台机的跨境镜像拉取本来就不可靠。我们的 GHCR 同样是跨境，只是之前没被验证过。

**所以调参数（更长 timeout、更多重试）是治标。已拍板：镜像改推国内 registry。**

### 双 registry：GHCR 仍是规范副本，部署从国内拉

一次 buildx 构建，推到两个 registry。**这是「一次构建推两处」而不是「构建两次」**，因为 buildx 产出的是**同一个 manifest**，而 manifest digest 是内容寻址的——所以 `steps.build.outputs.digest` 在两边都成立，v4 的 digest-pin 硬门原样保留。构建两次则可能得到两个 digest，那会把整套 pin 机制悄悄架空。

分工：**GHCR 是规范副本**（CI、溯源、回滚都读它），**国内 registry 只服务那台生产机的 pull**。

要配三项，缺任何一项 image job 第一步就红：

| 配置 | 类型 | 例 |
|---|---|---|
| `CN_IMAGE` | repo **variable** | `registry.cn-hangzhou.aliyuncs.com/<namespace>/micropythonos-ai-app-builder` |
| `CN_REGISTRY_USERNAME` | repo **secret** | 阿里云 ACR 的用户名 |
| `CN_REGISTRY_PASSWORD` | repo **secret** | ACR 的固定密码/访问凭证 |

registry 主机名从 `CN_IMAGE` 推导（`${CN_IMAGE%%/*}`），不单独配——**一个会自相矛盾的配置项，不如没有**。

未配置时**大声失败，绝不静默只推 GHCR**：那样 CI 会是绿的，几小时后在别人的生产机上才炸。已实测三种取值：unset → exit 1、**空串 → exit 1**（GitHub 未配置的 `vars` 给的是空串不是 unset，所以这里必须用 `:?` 而不是 `?`）、正常值 → 正确解析出 registry 主机名。

deploy job 相应改为登录并从国内 registry 拉，`permissions` 里的 `packages: read` 一并去掉——它不再碰 GHCR，`GITHUB_TOKEN` 什么权限都不需要了。

### 与宿主现有 CD 的两处有意分歧（`mpy-hardware-extension` PR #26 是参考实现）

那份已合并的 CD 只有 21 行，机制是：`docker login` → 按 digest `pull` → **`docker tag <digest> :latest`** → `cd /srv/upypi && docker compose up -d`。两处我们**故意**不一样，不是疏漏：

1. **镜像引用**：他们 compose 里写死 `:latest`，靠部署时重打 tag 让 compose 认到新镜像；我们走 `.env` 里 `MPOS_IMAGE` 的 `@sha256:` digest。我们这套不可变、可回滚可复现，但**同一份 compose 里从此并存两套机制**——交接时必须讲清楚，否则接手的人会以为 mpos 也能靠重打 tag 换版本。
2. **作用域**：他们 `up -d` 不带 service 名（每次发插件后端都会重启全栈，含 Caddy）；我们带 `mpos-app`。我们这套更安全，代价是「别重启邻居」这条纪律在宿主现有实践里本来就不成立，不能假定对方知道。

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
3. ~~**首次生产部署 = 宿主机手工执行**~~ **该前提已随合并失效（2026-08-01）**。原理由是「`workflow_dispatch` 只能触发默认分支上已存在的 workflow，分支阶段机制上不可用」——PR #9 已合并进 main，workflow 现在就在默认分支上，这个限制没有了。首次部署改走 CD（`workflow_dispatch`），宿主端只需准备好文件。**连带消掉一个卡点**：手工路径要求那台机上现有的 `docker login` 能拉到我们这个 private 包；走 CD 则由 deploy job 用 `secrets.GITHUB_TOKEN` + `packages: read` 自己登录，**已实测 `Login Succeeded` 且拉下 9 层中的 8 层**，不需要 ops owner 提供或验证任何 GHCR 凭据。
4. 合并 main 后的常态 CD：`CI 成功(main) → workflow_run → build → deploy`；`workflow_dispatch` 仅允许 main ref 且过 `production` environment 审批。
   ⚠️ **v5 实测：`production` environment 目前根本不存在**（API 404）。workflow 里写了 `environment: production`，GitHub 会在首次用到时自动建一个**没有任何保护规则**的同名环境 —— 也就是说「required reviewers 审批」这道门现在是空的。**切换日前必须手工建好该 environment 并配上 required reviewers**，否则本文多处把它当作缓解措施的地方都不成立。
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

实施时所有 pin 必须落成**具体值**：GitHub Actions 各 action 按 commit SHA 引用、`MPOS_IMAGE` 永远是 `@sha256:` digest。

> **v5：postgres/minio/mc 的 digest 已实测取得并回填**（见上节表格），原先「暂缓 pin」的妥协作废。三个 pin 指向的是华为云镜像站的 digest，不是 Docker Hub 的。

## 仓库改动清单（实施分支 `deploy/domestic`，6 件）

### 1. `deploy/compose.mpos-services.yml`（新；v5 由 `deploy/compose.yml` 改名）

改名是因为它**不再是一份可独立运行的 compose**，而是往宿主现有文件里粘的**片段**：没有顶层 `name:`，`default` 网络也依赖宿主文件。叫 `compose.yml` 会诱导人整份 scp 上去覆盖——那正是最坏的操作。

配套新增 `deploy/render-compose-block.sh`（第 3.5 件），装填密钥一律走它。

全部服务 `restart: unless-stopped`（`mpos-minio-init` 除外，`restart: "no"`）。

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
- **`source_sha` 绑定（CI 门的完整性所在）**：每种触发先解析唯一 `source_sha`——`workflow_run` 路径**必须**取 `github.event.workflow_run.head_sha`（即真正通过 CI 的 commit），push/tag 取 `github.sha`，dispatch 先用 `gh api` 校验该 sha 的 CI conclusion==success 再继续。
- 🔒 **`workflow_run` 三重来源校验（v5 新增，堵一个真实的 fork 提权路径）**：`branches: [main]` 过滤的是**触发方 run 的 head_branch**，不是 base ref。**fork 作者只要把自己的分支也命名为 `main`，其 PR 的 CI run 就能匹配上**，于是 `head_sha` 指向 fork 代码 → image job 构建它 → deploy job 把它推上生产机。这等于绕开上面那条「永不加 `pull_request`」的红线。故在解析 `source_sha` 前硬断言三条：`workflow_run.event == "push"`、`workflow_run.head_repository.full_name == github.repository`、`workflow_run.head_branch == "main"`。**checkout、构建、`sha-<source_sha>` 打标、部署审计输出全部用这一个 sha**——普通 checkout 会拿到 main 当前 HEAD，可能是未过 CI 的更新 commit，等于绕开 CI 门。
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
2. ~~GHCR 拉取权限~~ **已不是门（2026-08-01 实测关闭）**。三个包（`micropythonos-ai-app-builder`、`mpyhw-api`、`upypi`）确实全是 private，匿名拉取 401——但首次部署既然改走 CD（见过渡期策略第 3 条），deploy job 就用 `secrets.GITHUB_TOKEN` + `packages: read` 自己登录，实测通过。**所以不要再去问 ops owner 要 classic token，也不要把包设 public**：这个仓库本身是 private（插件后端那个才是 public），把包设 public 等于首次公开整个代码库。
3. **宿主预检**（ops owner + 我们）：**版本前提**——Docker Engine ≥ 24、Compose plugin ≥ v2.20（计划依赖 `service_completed_successfully` 与 `up --wait`，`docker compose version` 实测确认）→ **先备份现有 compose.yml** → 把 `render-compose-block.sh` 的输出粘进现有 compose.yml 的 `services:`/`networks:`/`volumes:` 三个映射 → 在同目录 `.env` 追加 `MPOS_IMAGE`（填 digest）→ `docker compose config` 通过**且输出里插件后端的 `db` 服务与 `postgres-data` 卷均未改动**（v5 更正：原写 `pgdata`、`mpyhw_internal` —— 真机上这两个名字都不存在，照着找会找不到）→ 无残留 `CHANGE-ME` 扫描。
4. **首次部署 = 触发 `workflow_dispatch`**（v5 更正：原为宿主手工，理由已失效，见过渡期策略第 3 条）。deploy job 自己 `docker compose up -d --wait mpos-app`——**带 service 名**，否则会重启插件后端；且不加 `--remove-orphans`。验收不变：`ps` 全 healthy、日志干净（无 storage 缺项/S3 403/DB auth 错误），并确认 `mpyhw-api` 未被重建。宿主手工那条命令仍然是有效的兜底，但不再是主路径。
5. **先验后端再暴露**：`docker compose exec caddy wget -qO- http://mpos-app:10000/api/health` 通过（Caddy 在栈内，从它自己容器里验才是真链路）→ 编辑与 compose.yml 同目录的 `Caddyfile` 追加 vhost → `docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile`（**别 `up -d` 或重启 caddy 服务**，那会连带影响另外两个站）→ 最后 DNS（提前压 TTL）。
6. 冒烟：health 四项 + 首页 + `/mpos-web/` + 显式 DB 读写（注册）+ 显式 MinIO 往返（session 对象出现且可读回）。注意 `/api/health` 只报配置不做实时依赖探活——监控不得只信它。
7. superadmin：`docker compose exec mpos-app python scripts/provision_superadmin.py --target production --username <user> --promote-existing` → 权限矩阵抽查（superadmin 200 / 普通 403）。
8. 真实生成：澄清 → 生成（50→40）→ MPK 下载 → WASM 预览。
9. **持久化/备份/回滚演练（验收硬门）**：
   - 容器重建后数据仍在、session 从 MinIO 恢复。**命令必须点名 mpos 三个服务**：`docker compose rm -sf mpos-app mpos-db mpos-minio` → `docker compose up -d --wait mpos-app`。⛔ **绝不能用 `docker compose down`** —— 它是项目级的，会把 `mpyhw-api`、`upypi`、`caddy` 和插件后端的 `db` 一起停掉，两个线上站直接断服。（v5 更正：原文就是 `down && up -d`，是独立栈时代的残留。）
   - `pg_dump`（DB：账号/登录/计费的权威源）+ **MinIO 用 `mc mirror` 导出**（session 状态与 artifact 的权威源——`session_state.json` 等就住在对象存储里，**不做在线卷目录拷贝**）→ **在一次性栈恢复并登录成功**；备份落**离机/独立盘**目的地，定保留期；两者非同刻一致，**明确接受的偏斜口径**：账号/点数以 DB 备份为准，session 以 MinIO 备份为准，互相引用缺失时（如 session 在而 artifact 缺、或反之）应干净报错，接受的数据丢失窗口 = 备份间隔；恢复演练至少覆盖「DB 旧于 MinIO」「MinIO 旧于 DB」两个方向各一次登录+开 session 验证；
   - digest 回滚演练：改 `.env` → `docker compose up -d --wait mpos-app`（**同样带 service 名**）→ 登录/计费/生成正常 → 滚回。当前 schema 仅增量（`create_all` + `auth.py` 手写补列），**未来任何非增量变更前必须先补 Alembic**，届时回滚演练要含 DB 恢复；
   - 宿主端口审计：DB/MinIO/app 无任何公网绑定。
10. 观察期 → **切换日前置门：注册闸门拍板**（见风险 1）→ 合并 main → 完整自动链路（CI→workflow_run→build→deploy）再验一遍 → 老站下线（erkou111）。

## 需要 ops owner（ben0i0d）确认/提供

- **同意把四个服务并进现有 compose.yml**（而不是另起独立栈），以及随之而来的爆炸半径合并。
- **现有 compose.yml 与 Caddyfile 的当前内容**——仓库里那份是模板不是实跑文件，我们无法读取生产机。粘贴前需据实核对是否已有同名 service/network/volume。
- ~~现有栈目录的绝对路径~~ **已知：`/srv/upypi`**。来源是插件后端已合并的 CD（`mpy-hardware-extension` PR #26，`cd /srv/upypi && docker compose up -d`），与他 compose 里 caddy 挂 `./Caddyfile` 一致。workflow 已写成默认值，路径变了再设 `vars.MPOS_STACK_DIR` 覆盖。（`caddy_net` 一项作废：无此网络，见 v5。）
- **🆕 内存预算**（原先只问了磁盘）。他明确说过「服务器配置不是很够」——这正是当初否掉 CD 框架、选自托管 runner 的理由。本方案在已有 4 个服务的机器上**新增两个常驻进程**（postgres + MinIO），并声明 `mem_limit` 合计 4G（app 2g / db 1g / minio 1g）。`mem_limit` 是上限不是预留，但实际占用是真的。**上机前必须确认剩余内存扛得住**，否则第一次 `up` 就可能触发 OOM——而 OOM 杀的**不一定是我们的容器**。
- ~~实测三个镜像能不能从华为云镜像站拉~~ **已自测通过（2026-07-31，本地 Docker）**，不再需要他代劳。三个都拉下来了，digest 已回填进 compose（见 v5）。原先"路径是按命名规律推的、未验证"的顾虑作废——之所以早先证不了，是因为**匿名探 registry 对存在的镜像也一律 401**，那是探针的限制，不是路径的问题。教训：能自己拉就别拿去当"需对方确认"。
- ~~实测 MinIO 镜像里有没有 `curl``~~ **已自测：`/usr/bin/curl` 和 `/usr/bin/mc` 都在**（同上）。这条原本是 P1，因为缺 `curl` 的失败**不可见**：healthcheck 永远不过 → init 不跑 → app 不起 → `up --wait` 超时，症状是"卡住"而不是"健康检查配错"。现已证伪，healthcheck 保持 `curl` 不动；`mc ready local` 作为该镜像日后去掉 curl 时的备选记在 compose 注释里。
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
