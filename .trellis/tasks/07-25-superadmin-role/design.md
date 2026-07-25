# Technical Design

## Role Model and Migration

`app_users` 新增：

```text
role VARCHAR(32) NOT NULL DEFAULT 'user'
```

应用层只接受 `user` 与 `superadmin`。公开注册显式写 `user`，不能根据特定用户名自动
提权。

`metadata.create_all()` 不会修改已有表，因此 AuthService 初始化时执行幂等迁移：

- SQLite：先 inspect columns，缺 role 时执行一次 `ALTER TABLE ... ADD COLUMN`。
- PostgreSQL：执行 `ADD COLUMN IF NOT EXISTS`。
- 新表声明同时包含 server default，旧行迁移后自然成为 `user`。

## Trusted Actor

认证中间件继续用 Cookie token hash 查库，把完整可信 user（id、username、role）写入
request state。role 不接受任何客户端输入。

所有权服务中的严格判断保持不变。入口中间件仅对已认证 `superadmin` 跳过 owner 拒绝：

```text
cookie -> database user/role
  user       -> existing owner check
  superadmin -> existing resource route, owner check bypassed
```

管理员可调用现有读写 action，但不能修改 owner 或冒充其他用户。管理员创建 session 时
仍使用自己的 UUID。

## Admin APIs

新增 `GET /api/admin/users`：

- 仅 role=superadmin。
- 返回 id、username、role、created_at、credits/unlimited。
- 不返回 password_hash、token_hash 或数据库内部连接信息。

现有 `GET /api/sessions` 对管理员传 owner filter `None`，普通用户仍传自身 UUID。

## Unlimited Billing

BillingService 接口增加显式 `unlimited` 参数：

- 普通用户保持原 50/10 行为。
- 管理员 consume 返回成功但不减 credits、不写 generation ledger。
- account payload 保留兼容的 credits 数字，同时增加 `unlimited_credits: true`。
- 前端在 unlimited 时跳过余额不足前置判断并显示 `∞`。

## Secure Provisioning

新增 `scripts/provision_superadmin.py`：

- `--target local` 使用本地 SQLite。
- `--target production` 只从进程环境读取 `DATABASE_URL`。
- `--username` 可见但密码不作为 CLI 参数。
- 密码通过 `getpass` 或专用 secret 环境变量读取，并且从不打印。
- 不存在时用 AuthService 的 scrypt 路径创建；存在时要求显式 `--promote-existing`。
- 重置密码必须额外指定 `--reset-password`，并撤销该用户的所有登录 token。
- 输出仅包含目标类型、用户名和成功状态。

生产执行位置优先 Render Shell/Job，让 `DATABASE_URL` 由平台环境注入。当前本地环境没有
该变量，因此生产 provisioning 在连接配置完成前必须明确停止。

## Compatibility and Security

- 旧 Cookie 无需重新登录；每次请求查库后即可看到新 role。
- 普通用户错误语义维持 401/404。
- 管理员专用 endpoint 对普通登录用户返回 403。
- role 不进入 session state 作为授权来源，避免角色撤销后旧 checkpoint 继续提权。
- 初始密码不进入仓库；首次登录后应立即轮换。

## Rollback

将账号 role 改回 `user` 即可即时撤销权限。数据库 role 列可保留，不做破坏性 drop。
代码回滚后该列不会影响旧逻辑。
