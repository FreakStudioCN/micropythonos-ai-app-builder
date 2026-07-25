# Superadmin Role and Environment Research

## Existing Code

- `backend/app/auth.py:38-46` 的 `app_users` 无 role。
- `AuthService.__init__` 只调用 `metadata.create_all()`，不能给已有表补列。
- `backend/app/main.py:136-179` 的认证中间件是集中 owner authorization 边界。
- `backend/app/session_service.py:256-274` 的 owner 校验应保持严格。
- `SessionService.list_sessions(user_id)` 已支持 `None` 返回全部 session。
- `backend/app/billing.py:80-116` 的 account/consume 当前只按 user_id 工作。
- 前端 BillingAccount 尚无 role/unlimited 字段。

## Environment

- 本地 SQLite `backend/sessions/app.db` 可只读连接。
- 本地指定账号不存在。
- 本地 `app_users` 尚无 role/admin 类列。
- `backend/.env`、示例配置和当前进程均没有生产 `DATABASE_URL`。
- 因此当前无法连接生产 Supabase，也无法确认生产账号或 schema。

## Decisions

- 使用 role 字段，不按用户名硬编码管理员。
- 使用明确的 unlimited billing contract，不使用巨额点数 sentinel。
- provisioning 密码通过隐藏输入/secret environment 传入，不进入 CLI 参数。
- 普通 owner check 不改弱；只在可信 request actor 边界做管理员 bypass。
- 生产 provisioning 必须等待 `DATABASE_URL` 由平台安全注入。
