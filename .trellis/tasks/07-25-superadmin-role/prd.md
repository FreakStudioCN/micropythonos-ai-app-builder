# Add local and production superadmin

## Goal

为正式内测增加真实的数据库 `superadmin` 角色，并将指定账号分别安全创建到本地 SQLite
和生产 Supabase PostgreSQL。超级管理员不受点数限制，可管理全部用户生成资源；普通
用户现有隔离和计费规则不得弱化。

## Background

- 当前 `app_users` 没有 role 字段，所有登录用户权限相同。
- session、artifact 和 permission 当前严格按 `owner_user_id` 隔离。
- 当前每个新账号 50 点、每个 revision 扣 10 点。
- 本地数据库 `backend/sessions/app.db` 可连接，指定账号尚不存在，schema 尚无角色列。
- 当前环境没有生产 Supabase `DATABASE_URL`，因此可以先完成兼容实现和本地建号，但
  生产建号必须等连接 URL 通过安全环境变量注入后执行。
- 用户提供的初始密码已经出现在对话中，不得写入 PRD、代码、脚本参数、日志或提交。

## Requirements

1. `app_users.role` 仅允许 `user` 和 `superadmin`；所有公开注册一律写入 `user`。
2. 新安装和已有 SQLite/PostgreSQL 数据库都能幂等获得非空 role 列，旧用户默认为 `user`。
3. 登录 Cookie 每次解析都从数据库读取 role；客户端 header、query 和 payload 不能声明角色。
4. 普通用户继续只能访问自己的 session、artifact 和 permission，跨用户访问返回 404。
5. `superadmin` 可列出所有用户和 session，并通过现有 session/artifact/permission/action
   接口查看或管理跨用户资源；管理员创建的新 session 仍归管理员自己。
6. 管理员用户列表不得返回密码哈希、登录 token、数据库连接信息或其他秘密。
7. `superadmin` 生成 revision 时不扣点、不写消费 ledger；账户响应提供明确的
   `unlimited_credits`，不能用巨额数字伪装无限。
8. 前端账户模型识别 role/unlimited，并以管理员标识和 `∞` 展示；普通用户体验不变。
9. 提供仅供运维调用的 provisioning 脚本：密码从隐藏输入或 secret 环境读取，不接受
   明文命令行参数，不打印密码或哈希。
10. provisioning 对本地 SQLite 和由 `DATABASE_URL` 指定的 Supabase PostgreSQL 使用
    同一角色/密码哈希逻辑；两边用户 UUID 无需相同。
11. 指定账号不存在时创建并设为 `superadmin`；已存在时必须显式选择提权及是否重置密码，
    重置后撤销该用户已有登录 session。
12. 仓库和部署配置不得包含生产数据库 URL、初始密码或派生密码哈希。

## Acceptance Criteria

- [ ] 旧版 SQLite schema 启动后自动补 role，已有用户保持 `user`。
- [ ] PostgreSQL migration 可重复执行，不因列已存在失败。
- [ ] 公开注册无法创建 `superadmin`，伪造 role/header/query 无效。
- [ ] 普通用户跨用户访问仍为 404；管理员可列出全部 session 并访问他人 artifact。
- [ ] `GET /api/admin/users` 仅管理员可用，普通用户返回 403，响应不含秘密字段。
- [ ] 管理员连续生成不会减少余额或产生 generation consumption ledger。
- [ ] `/api/user` 与 billing 响应包含 role/unlimited，前端显示管理员和无限点数状态。
- [ ] provisioning 在临时 SQLite 上覆盖首次创建、幂等提权、显式密码重置和 session 撤销。
- [ ] 指定账号成功写入本地数据库并能登录为 `superadmin`。
- [ ] 注入生产 `DATABASE_URL` 后，指定账号成功写入 Supabase 并能登录为 `superadmin`。
- [ ] 后端鉴权/计费测试和前端 production build 通过。

## Out of Scope

- 在公开 API 中创建、提权或删除管理员。
- 删除用户、修改其他用户密码或变更资源 owner。
- 完整图形化管理员后台。
- 绕过数据库自身的 Supabase 网络、RLS 或 DDL/DML 权限。
- 把生产 Supabase 凭据提交到仓库。
