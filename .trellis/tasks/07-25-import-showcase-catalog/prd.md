# Import 100-app showcase catalog

## Goal

把用户提供的 100 个 MicroPythonOS MPK 与配套截图导入 Blockless-Make-APP，形成可搜索、
可筛选、可下载的前端样例库，同时保持现有 3 个确定性可运行 Demo 不变。

## Background

- 输入文件是一个约 1.08 MB 的导出包，包含 100 个 MPK 和 100 张 320×240 PNG，
  `com.blockless.demo001` 至 `com.blockless.demo100` 一一对应。
- 每个 MPK 都包含 `MANIFEST.JSON`、`assets/main.py` 和 `icon_64x64.png`；结构检查未发现
  路径穿越、加密条目、Manifest ID 不一致或额外文件类型。
- Manifest 提供 fullname、name、category、version、short/long description 和 entrypoint。
- 包内 Python 源码尚未逐个做安全与设备兼容审计，因此本任务只展示和下载，不自动执行。
- 当前前端只有 3 张硬编码项目卡，后端也只接受 3 个固定 demo seed。将 100 个样例伪装
  成这些 seed 会制造前后端元数据漂移，因此样例 catalog 与可运行 demo 必须分层。

## Requirements

1. 完整导入 100 个 MPK 和 100 张截图，不丢失、不重复，package ID 必须与 Manifest 一致。
2. 提供可重复运行的标准库导入脚本，从受信任 ZIP 读取 Manifest、校验路径和配对关系，
   生成确定性的 `catalog.json` 并复制静态资源。
3. 前端新增“100 App 样例库”，从单一 catalog 加载数据，不在 TypeScript 中手写 100 份元数据。
4. 默认展示 12 个精选样例，并支持查看全部、名称/描述搜索和 category 筛选。
5. 每张卡显示截图、名称、简介、类别和版本，不显示额外的生成来源或真机验证标识。
6. 每张卡允许下载对应 MPK；导入、列表加载和卡片点击不得执行其中的 Python 代码。
7. 保留现有 3 个可运行/Remix 的确定性 Demo，用户能清楚区分“可运行 Demo”和“静态样例”。
8. 中文和英文界面均有清晰文案；卡片图片使用 lazy loading，桌面和移动端布局均可用。
9. 样例静态资源进入 Vite production bundle，并随当前 React + FastAPI 单 Docker 镜像发布，
   保证前端页面、后端和样例 catalog 来自同一 commit。
10. 导入脚本不得接受路径穿越、加密 MPK、缺失 Manifest、Manifest ID 不一致或截图缺失。

## Acceptance Criteria

- [ ] `catalog.json` 恰好包含 100 个唯一 package，且每项截图与 MPK URL 均存在。
- [ ] 12 个精选样例优先展示；展开后可浏览全部 100 个，并可按文本和类别筛选。
- [ ] 下载按钮返回与 catalog 条目对应的 `_r1.mpk` 文件。
- [ ] 页面不显示“批量生成样例”或“未经真机验证”标识，也不把静态样例描述为浏览器运行结果。
- [ ] 原有 countdown、calendar、device-dashboard 三个 Demo 入口和行为保持不变。
- [ ] ZIP 或内部 MPK 含非法路径、缺文件或身份不匹配时，导入脚本明确失败且不留下半成品。
- [ ] Vite production build 能包含 catalog、100 张截图和 100 个 MPK。
- [ ] 现有前端生产构建与后端回归不退化。

## Out of Scope

- 自动运行、WASM 预览或真机部署这 100 个未经审计的 App。
- 把 100 个条目写入数据库或 Supabase Storage。
- 为样例增加作者、点赞、举报、评论和公开发布工作流。
- 本任务内重构现有 3 个 demo seed 为后端 catalog API。
- 本任务内修改 GitHub Actions 或 Render 部署触发方式；发布建议单独落地。
