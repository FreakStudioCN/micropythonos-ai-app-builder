# Implementation Plan

1. 在 `backend/app/auth.py` 增加 role 常量、schema 列、SQLite/PostgreSQL 幂等迁移，
   并让注册、登录、token 解析和 public user payload 携带可信 role。
2. 增加 AuthService 的安全 user listing、role promotion 和显式密码重置/session 撤销能力，
   仅供内部 admin endpoint 或 provisioning 调用。
3. 在 `backend/app/main.py` 集中实现 superadmin owner bypass、全 session 列表与
   `GET /api/admin/users`，保持普通用户 401/403/404 行为。
4. 在 `backend/app/billing.py` 增加显式 unlimited 模式，并更新所有 account/consume
   调用点，避免管理员扣点或写 consumption ledger。
5. 在 `frontend/src/App.tsx` 扩展账户类型、余额拦截与管理员/无限点数显示。
6. 新增 `scripts/provision_superadmin.py`，支持隐藏密码输入、local/production target、
   显式提权与重置密码，不接受明文密码参数。
7. 更新 access-control/billing 测试，并增加 migration/provisioning 回归覆盖。
8. 获得验证许可后运行窄测试、后端全量测试与前端 build。
9. 用 provisioning 脚本在本地创建指定账号并验证 role；不在输出中显示密码。
10. 生产 `DATABASE_URL` 安全注入后，在生产环境执行同一脚本并验证登录；连接未配置时
    明确报告阻塞，不伪造完成状态。

## Validation Commands

```bash
PYTHONPATH=backend backend/.venv/bin/python -m unittest \
  backend.tests.test_access_control backend.tests.test_billing -v
PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -v
npm run build --prefix frontend
```

## Risk and Rollback Points

- role migration 必须兼容已有 SQLite 和 Supabase PostgreSQL，不能依赖 `create_all()`。
- 中间件 bypass 只依据数据库 role，不能因用户名、header 或 query 放行。
- 管理员跨用户写 action 当前复用既有 activity log；完整独立 admin audit ledger 作为后续
  安全增强，不在本任务静默伪造。
- 生产数据库连接/DDL 权限不足时停止，不回退到本地 SQLite。

## Stop Condition

本地与生产账号均真实创建、角色/点数/跨用户权限验收通过后停止；不扩展完整管理员后台。
