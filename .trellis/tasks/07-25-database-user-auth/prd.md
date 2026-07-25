# Database user authentication for closed beta

## Goal

让正式内测用户通过公开网址使用用户名和密码注册、登录并恢复自己的项目；用户
身份、密码哈希和登录会话持久化到后端数据库，所有资源与点数都绑定数据库用户，
完成前端、后端和数据库的真实云端部署。

## Background

- 当前产品提供 50 个免费内测点数，每个新 App revision 消耗 10 点。
- 当前版本不提供收费、在线充值或自动订阅，前端不展示购买入口。
- 仅依赖浏览器匿名 ID 会在清除 Cookie 后丢失身份，也无法跨设备恢复项目。
- 正式内测要求不同用户不能读取、修改或下载彼此的 session、permission 和 artifact。

## Requirements

1. 用户可用唯一用户名和密码注册、登录和退出。
2. 用户记录保存在后端数据库；本地开发可使用 SQLite，云端通过 `DATABASE_URL`
   使用 PostgreSQL。
3. 密码不得明文保存或返回，只保存随机盐的强密码哈希。
4. 登录成功后由后端签发随机会话令牌，数据库仅保存令牌哈希，浏览器通过
   HttpOnly Cookie 携带令牌。
5. session、artifact、permission、billing 全部绑定数据库用户 ID；跨用户访问
   返回 404，未登录访问受保护 API 返回 401。
6. 新用户初始获得 50 点；每个新 revision 消耗 10 点；失败后的同 revision 重试
   不重复扣费。
7. 清除 Cookie 后可以重新登录恢复原用户的数据和剩余点数，不能重新领取额度。
8. 保留当前产品文案：不收费、不充值、不自动订阅，前端继续展示点数。
9. 第一版开放注册，不要求邀请码；额度限制定义为“每个账号 50 点”，暂不保证同一
   自然人只能注册一个账号。
10. 前端、FastAPI 后端和 PostgreSQL 必须部署到实际云平台，不能只交付本地配置。
11. 生产环境必须配置 DeepSeek Key、数据库连接、Cookie、CORS 和健康检查，秘密
    只能保存在平台环境变量中。
12. 部署完成后提供用户访问网址，并真实验证注册、登录、刷新恢复和后端健康检查。
13. 部署拓扑确定为：Render 同源提供 Vite 前端和 FastAPI 后端，Supabase 提供
    PostgreSQL；不拆分到 Vercel/Cloudflare。
14. 因 Render Free 文件系统不持久，用户、登录会话和 billing 必须保存到 Supabase
    PostgreSQL；session 状态、截图和生成产物必须保存到 Supabase Storage，不能把
    `/data` 当作免费环境的持久层。

## Acceptance Criteria

- [x] 注册后数据库存在用户记录，密码字段不是原密码。
- [x] 正确密码可登录，错误密码返回 401，重复用户名返回 409。
- [x] 登录 Cookie 为 HttpOnly，HTTPS 部署可启用 Secure，退出后令牌失效。
- [x] 两个用户的 session 列表互不重叠，跨用户访问 session、artifact、permission
      均返回 404。
- [x] 客户端 query/header 不能选择其他用户的 billing 身份。
- [x] 新用户显示 50 点，同一 revision 只扣一次 10 点，最多生成 5 个新 revision。
- [x] SQLite 鉴权测试、后端全量测试和前端生产构建通过。
- [x] 云端部署文档说明 PostgreSQL `DATABASE_URL` 和 Cookie 配置。
- [ ] 前端、后端和数据库已真实部署，用户可通过 HTTPS 网址完成注册和登录。
- [ ] 云端健康检查成功，前端可访问受保护 API，密码和 DeepSeek Key 未进入构建产物。
- [ ] Render 重启后用户、点数、session 列表和生成产物仍能恢复。

## Out of Scope

- 邮箱、手机号、OAuth 和实名认证。
- 找回密码、修改密码、用户头像和个人资料。
- 管理员后台、角色/组织/团队权限。
- 支付、充值、订阅和自动加点。
- 邀请码、设备指纹和防多账号注册。
- Vercel/Cloudflare 前端拆分部署。
