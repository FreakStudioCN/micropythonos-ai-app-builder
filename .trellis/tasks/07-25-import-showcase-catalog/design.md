# Technical Design

## Catalog Boundary

现有 3 个 deterministic demo 继续走 `POST /api/demo/sessions`，支持运行、部署和 Remix。
新导入的 100 个 App 使用静态 catalog，只负责展示与下载：

```text
trusted ZIP
  -> import script validates outer ZIP and inner MPK
  -> frontend/public/showcase/catalog.json
  -> frontend/public/showcase/screenshots/*.png
  -> frontend/public/showcase/mpks/*.mpk
  -> Vite copies public assets into dist
  -> FastAPI serves the same dist from the production image
```

这避免扩展 `DemoSessionRequest.seed` 为 100 个硬编码值，也不会执行未审计源码。

## Import Contract

`scripts/import_showcase_bundle.py` 使用 Python 标准库完成导入：

- 拒绝绝对路径、`..`、符号链接、加密条目和未知顶层目录。
- 外层只接受 `mpks/*.mpk` 与 `screenshots/*.png`。
- 每个 MPK 只读取 `<fullname>/MANIFEST.JSON`、`assets/main.py` 和图标进行结构校验。
- 校验外层文件名、Manifest fullname、内部根目录和截图 basename 一致。
- 先写临时目录，100 对全部验证成功后再替换目标目录，避免半成品。
- catalog 按 fullname 排序并使用稳定 JSON 格式，保证重复导入结果确定。

Catalog 条目：

```json
{
  "fullname": "com.blockless.demo001",
  "name": "CountdownTimer",
  "category": "utilities",
  "version": "1.0.0",
  "shortDescription": "...",
  "longDescription": "...",
  "screenshotUrl": "/showcase/screenshots/com.blockless.demo001.png",
  "mpkUrl": "/showcase/mpks/com.blockless.demo001_r1.mpk",
  "featured": true
}
```

精选 ID 固定为：001、015、018、030、033、051、053、061、062、067、088、096。

## Frontend Experience

- 现有“仿真项目库”保留，标题和按钮继续表示真实可运行 Demo。
- 其后新增“100 App 样例库”区块，以图片区分于现有渐变封面卡片。
- 首屏只渲染精选 12 个；“查看全部”后渲染搜索/类别过滤结果。
- 下载采用普通 `<a download>` 指向同源 MPK，不把文件内容载入 JS。
- 页面不展示“批量生成样例”或“未经真机验证”等来源/验证标识。
- 图片设置固定 aspect ratio、`loading=lazy` 和替代文本，避免布局跳动。
- 无匹配结果和 catalog 加载失败时给出明确状态，不影响页面其他功能。

## CI/CD and Version Association

当前 Dockerfile 在一个镜像内先构建 React，再复制 FastAPI/runner 与 `frontend/dist`。
样例位于 Vite public 目录后，也会进入同一个镜像，因此前端、后端和 catalog 天然绑定
同一 commit SHA。

长期发布分支应为 `main`。当前日期分支只用于本轮收尾，合并后删除；后续任务使用短命
`feat/*` 或 `fix/*` 分支。GitHub CI gate Render 部署属于独立后续任务。

## Security

- 不从 MPK 执行、import 或 eval Python。
- 本次输入来源记录在任务 research 中；不把 Windows 绝对路径写入产品 catalog。

## Rollback

删除新增展示区块、导入脚本和 `frontend/public/showcase` 即可回退。现有 3 个 Demo 与后端
API 不发生 schema 变更。
