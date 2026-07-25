# Implementation Plan

1. 新增数据库 auth service、用户/登录会话 schema、scrypt 密码哈希和 token 哈希。
2. 新增 register/login/logout/current-user API，替换匿名 Cookie 中间件。
3. 创建 session 时绑定数据库 user UUID，并统一保护 session/artifact/permission。
4. billing 迁移到同一个 PostgreSQL/SQLite 数据库，只从后端登录上下文获取 user
   UUID，保持 50/10 点数规则和幂等扣费。
5. 前端增加最小登录/注册 gate 和退出入口，所有 API 请求携带 Cookie。
6. 新增 Supabase S3-compatible session/artifact 同步层；本地保持文件系统 fallback，
   云端重启后可恢复 session 和 artifact。
7. 配置 SQLite 默认路径、PostgreSQL/S3 依赖、Render/Supabase 环境变量和部署文档。
8. 增加注册登录、密码哈希、Cookie、越权、计费和对象存储恢复测试。
9. 配置并真实部署前端、后端、Supabase PostgreSQL/Storage，写入平台环境变量。
10. 对公开 HTTPS 地址执行注册、登录、刷新恢复、健康检查和资源隔离烟雾测试。
11. 运行：
   - `PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -v`
   - `npm run build --prefix frontend`

## Rollback Points

- auth service 和路由可以整体回退，不修改或删除已有用户生成文件。
- 若 PostgreSQL 连接失败，启动应明确失败；不得静默切换到临时 SQLite。
- 若前端鉴权 gate 失败，后端仍通过 401/404 保持数据边界。

## Stop Condition

达到 PRD 验收标准即停止，不增加邀请码以外的运营后台、密码找回、支付或角色系统。
