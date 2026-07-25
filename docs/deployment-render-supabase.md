# Render + Supabase 免费内测部署

这套部署把 React 前端和 FastAPI 后端放在同一个 Render Web Service，通过同一域名
访问；Supabase PostgreSQL 保存账号、登录会话和点数，Supabase Storage 保存生成
session 与 artifact。

## 需要准备

- GitHub：能向本仓库推送分支，并允许 Render 读取仓库。
- Supabase：一个新加坡区域项目。
- Render：使用 GitHub 登录并授权本仓库。
- DeepSeek：现有 `DEEPSEEK_API_KEY`。

不需要提供自定义业务接口，也不要把密码、数据库 URL 或 Access Key 发到聊天或
提交到 Git。所有秘密直接填进 Render 的 Environment 页面。

## 1. 创建 Supabase 项目

1. 在 Supabase 创建项目，区域选择 Singapore，并保存数据库密码。
2. 打开 **Connect**，选择适合长期运行服务器的 **Session pooler**，复制完整连接
   URI。该值稍后填写为 Render 的 `DATABASE_URL`。
3. 打开 **Storage**，创建 private bucket：`mpos-sessions`。
4. 在项目的 Storage/S3 设置中启用 S3 compatibility，并创建一组 server-side S3
   access key。记录 endpoint、region、access key ID 和 secret access key。

S3 secret 只会显示一次。若丢失，删除旧 key 并生成新 key，不要把它放到前端环境
变量。server-side S3 key 可绕过 Storage RLS，只能用于 Render 后端。

## 2. 推送部署分支

本任务分支是 `dc/local-deploy-20260725`。确认测试通过并提交后推送到 GitHub：

```bash
git push -u origin dc/local-deploy-20260725
```

部署正式内测前建议把该分支合并到仓库默认分支，使 Render 的自动部署来源稳定。

## 3. 由 Blueprint 创建 Render 服务

1. 在 Render 选择 **New > Blueprint**。
2. 连接 GitHub 仓库，Render 会读取仓库根目录的 `render.yaml`。
3. 创建服务时填写所有标记为 secret/manual 的变量：

| Render 环境变量 | 填写内容 |
| --- | --- |
| `DEEPSEEK_API_KEY` | DeepSeek 后端 Key |
| `DATABASE_URL` | Supabase Session pooler 完整 URI |
| `MPOS_STORAGE_ENDPOINT` | Supabase S3 endpoint |
| `MPOS_STORAGE_REGION` | Supabase S3 region |
| `MPOS_STORAGE_ACCESS_KEY_ID` | Supabase server S3 access key ID |
| `MPOS_STORAGE_SECRET_ACCESS_KEY` | Supabase server S3 secret access key |

`MPOS_STORAGE_BUCKET=mpos-sessions`、安全 Cookie 和持久化强制检查已经写在
`render.yaml`。不要改成 public bucket。

## 4. 查看网址和验收

部署状态变为 **Live** 后，进入 Render 的 `blockless-make-app` 服务。页面顶部显示的
`https://...onrender.com` 就是正式内测网址；前端和 `/api` 共用这个域名。

先访问：

```text
https://你的服务域名/api/health
```

应看到：

```json
{
  "status": "ok",
  "deepseek_configured": true,
  "database_backend": "postgresql",
  "object_storage_enabled": true,
  "durable_storage_required": true
}
```

然后执行人工烟雾测试：

1. 注册账号 A，确认显示 50 点和 5 次剩余生成。
2. 创建一个 App，确认扣到 40 点。
3. 退出后重新登录 A，确认点数和历史 session 仍存在。
4. 注册账号 B，确认看不到 A 的 session/artifact。
5. 在 Render 手动 redeploy 后再次登录 A，确认数据仍存在。

## 免费方案限制

- Render Free 会在空闲后休眠，第一次访问可能需要等待冷启动，不适合承诺稳定 SLA。
- Render Free 本地文件系统是临时的，所以本项目强制使用 Supabase 数据库和 Storage。
- Supabase Free 有数据库和 Storage 配额；本方案适合小规模正式内测，不适合无限开放。
- DeepSeek API 调用仍会产生模型费用，50 点规则只是本项目的资源闸门，不是支付系统。

出现真实用户量或需要稳定响应后，第一项升级应是 Render 付费实例；当前应用代码和
Supabase 数据不需要因此迁移。
