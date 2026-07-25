# Archive and Delivery Research

## Input Archive

- 100 个 `com.blockless.demoNNN_r1.mpk`，编号 001-100。
- 100 个同 ID PNG，全部为 320×240 RGB。
- 外层仅有 `mpks/` 与 `screenshots/`，总未压缩体积约 1.08 MB。
- 每个 MPK 是 ZIP，固定包含 `<fullname>/MANIFEST.JSON`、`assets/main.py` 和
  `icon_64x64.png`。
- Manifest 与包名一致，未发现路径穿越、加密条目或额外类型。
- 类别分布：utilities 23、education 10、games 20、health 2、weather 5、
  productivity 20、graphics 20。
- Python 源码未逐包安全审计，因此只能静态展示和下载。

## Existing Product Entry

- `frontend/src/App.tsx:118-149` 硬编码现有 3 个 simulation project。
- `frontend/src/App.tsx:1411-1441` 渲染仿真项目库。
- `backend/app/models.py:118-121` 将 demo seed 限定为 3 个 Literal。
- `backend/app/session_service.py:52-83` 维护后端重复的 demo 元数据，并现场合成固定项目。
- 为 100 个样例扩展 seed 会放大双份元数据漂移，故采用独立静态 catalog。

## Branch and Delivery Facts

- GitHub 默认分支为 `main`；当前工作分支是短期 `dc/local-deploy-20260725`。
- `.github/workflows/ci.yml` 当前只运行后端测试和前端 build，不构建/发布镜像。
- `render.yaml` 使用单个 Docker web service 和 commit 自动部署，但未在文件中显式写 branch。
- Dockerfile 同时构建 React 与 FastAPI，production 中由 FastAPI 提供 `frontend/dist`。
- 因此静态 catalog 随镜像即可与前后端绑定同一 commit；上线前仍需在 Render Dashboard
  确认监听分支为 `main`。

## Recommended Follow-up

PR 阶段执行 backend tests、frontend build、Docker build 和窄 smoke；合并 `main` 后只在
CI 全绿时触发 Render deploy hook。网站 Docker 镜像与用户生成 MPK 是两类制品，不应
混用同一 artifact/release 语义。
