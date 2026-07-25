# Technical Design

## Boundaries

- 新增独立 auth service，负责用户表、密码哈希和登录会话表。
- SessionService 继续负责文件型生成会话，只新增/保留 `owner_user_id` 所有权字段。
- BillingService 继续使用现有持久账本，以数据库用户 UUID 为计费键。
- FastAPI 统一中间件解析登录 Cookie，并在进入资源路由前执行所有权检查。

## Storage

使用 SQLAlchemy Core 兼容两种运行方式：

- 本地默认：`backend/sessions/auth.db` SQLite。
- 云端：`DATABASE_URL=postgresql://...`，由 psycopg 连接 Supabase PostgreSQL。

表：

- `app_users`: UUID、显示用户名、规范化唯一用户名、密码哈希、创建时间。
- `app_login_sessions`: 会话令牌 SHA-256、用户 UUID、创建/最后访问/过期时间。

## Authentication Flow

1. 注册校验用户名和密码，使用 `hashlib.scrypt` 与随机盐生成密码哈希。
2. 登录使用常量时间比较验证哈希。
3. 注册或登录成功后生成高熵随机 token；只把 token 哈希写入数据库。
4. 原 token 进入 HttpOnly Cookie，默认 30 天过期。
5. 中间件查询 token 哈希并把可信用户 UUID 放入 `request.state`。
6. 退出删除数据库会话并清除 Cookie。

## Authorization and Billing

- 创建 session 时写入 `owner_user_id`。
- `/api/sessions/{id}` 的所有子路由统一检查 owner。
- artifact/permission 先通过索引找到 session，再检查 owner。
- billing API 忽略任何客户端 user_id，只使用 `request.state.user_id`。
- 完整 run 和独立 generate stage 以 `session_id + revision_id` 作为扣费幂等键。

## Compatibility and Rollout

- 旧的无 owner/匿名 owner session 默认不可见，不自动归属给新账号。
- 本地无 `DATABASE_URL` 时自动建 SQLite schema。
- 云端启动时自动创建两张最小 auth 表；后续 schema 扩大时再引入迁移工具。
- 回滚时可撤销 auth 路由和中间件；原生成会话文件不删除。

## Deployment

现有 Docker 在 Render 同源提供 Vite 产物和 FastAPI API。Cookie 不跨站，无需新增
代理，当前 `Dockerfile` 和 `render.yaml` 可直接扩展。数据库使用 Supabase
PostgreSQL。

Render Free 文件系统在休眠、重启和重新部署时会丢失。因此：

- `app_users`、`app_login_sessions`、billing account 和 ledger 存 PostgreSQL。
- session 工作目录在任务运行时使用本地临时目录，同时同步到 Supabase Storage
  私有 bucket；服务启动或本地缓存缺失时从对象存储恢复。
- Storage 使用 Supabase 的 S3 兼容端点，凭据只保存在 Render 环境变量。

无论选择哪种拓扑，`DATABASE_URL`、`DEEPSEEK_API_KEY` 和其他秘密只配置在后端
平台；浏览器构建不得包含数据库密码或模型 Key。

## Security Limits

- 第一版按产品决定开放注册。用户名不是“每个自然人”的强证明，多账号可以绕过
  每账号 50 点限制；内测成本异常时再增加邀请码或其他注册门槛。
- 第一版没有密码找回；忘记密码需人工处理或后续实现重置流程。
- PostgreSQL 和 Supabase Storage 必须同时配置，否则 Render Free 重启后会丢失
  billing/session/artifact 数据。
