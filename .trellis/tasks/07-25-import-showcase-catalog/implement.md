# Implementation Plan

1. 新增 `scripts/import_showcase_bundle.py`，实现 ZIP/MPK 安全校验、Manifest 解析、精选标记、
   临时目录原子导入和确定性 catalog 生成。
2. 用该脚本导入用户提供的 ZIP，生成 `frontend/public/showcase/catalog.json`、100 张截图和
   100 个 MPK；不手工编辑生成文件。
3. 在 `frontend/src/App.tsx` 增加 catalog 类型、加载状态、搜索、类别过滤、精选/全部切换，
   保留现有 `simulationProjects` 与 `openSimulationProject` 行为。
4. 在现有仿真项目区块之后渲染静态样例库；卡片不显示生成来源或真机验证标识，只提供 MPK 下载。
5. 在 `frontend/src/styles.css` 增加有明确视觉层级的截图卡片、筛选控件、空/错状态和响应式布局，
   延续现有页面视觉语言。
6. 按用户明确许可后运行导入结构检查与前端 production build；未获许可前不运行验证。

## Validation Commands

```bash
python3 scripts/import_showcase_bundle.py --check \
  frontend/public/showcase/catalog.json frontend/public/showcase
npm run build --prefix frontend
```

## Risk and Rollback Points

- 100 个静态文件会增大仓库与镜像约 1.1 MB，可接受，但不应把解包源码重复提交。
- 所有 URL 使用 Vite public 根路径，避免开发与 FastAPI 同源生产路径不一致。
- 导入脚本必须先完成所有校验再替换目标目录，失败时保留已有 catalog。
- UI 只引用 catalog，不复制 Manifest 元数据，避免后续更新漂移。

## Stop Condition

100 个样例可稳定展示、筛选和下载后停止；不增加运行、发布社区或数据库能力。
