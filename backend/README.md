# Backend

FastAPI 后端通过 DeepSeek 生成真实的 MicroPythonOS `assets/main.py` 和
`MANIFEST.JSON`，并把它们打包成真实 ZIP 结构的 `.mpk`。

## 配置

```bash
cp .env.example .env
```

编辑 `.env`，填写：

```text
DEEPSEEK_API_KEY=你的真实Key
```

## 启动

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

健康检查：`http://localhost:8000/api/health`

API 文档：`http://localhost:8000/docs`

## Session API

- `GET /api/capabilities`
- `POST /api/sessions`
- `GET /api/sessions/{session_id}`
- `GET /api/sessions/{session_id}/events`
- `POST /api/sessions/{session_id}/actions/analyze`
- `POST /api/sessions/{session_id}/actions/generate`
- `POST /api/sessions/{session_id}/actions/test`
- `POST /api/sessions/{session_id}/actions/package`
- `POST /api/sessions/{session_id}/actions/deploy`
- `POST /api/sessions/{session_id}/actions/publish-check`
- `POST /api/sessions/{session_id}/actions/preview-result`
- `POST /api/sessions/{session_id}/retry`
- `POST /api/sessions/{session_id}/revisions`
- `POST /api/sessions/{session_id}/resume`
- `POST /api/sessions/{session_id}/cancel`
- `POST /api/sessions/{session_id}/devices/scan`
- `POST /api/permissions/{permission_id}/decision`
- `GET /api/sessions/{session_id}/artifacts`
- `GET /api/artifacts/{artifact_id}`

运行状态会持久化到被 Git 忽略的 `backend/sessions/`。API key 仍只放在
`backend/.env`。

生成前会完整读取 `vendor/MicroPython_Skills/mpos-dev/reference/` 下的
`lvgl_api_summary.json` 和 `mpos_api_summary.json`，并把实际计划调用写入
`generation_result.json.api_usage`。每次连续修改创建新的 `rN`，上一成功
版本快照保存在该 session 的 `revisions/rN/`。

`MposSkillAdapter` 会读取并校验 submodule 中各个 `mpos-*-web/SKILL.md`
契约。Skill 是协议文档，不是任意命令入口；所有文件、脚本和设备操作仍由
后端白名单执行器控制。当前设备服务会如实返回能力不可用和系统安装入口，
不会伪造串口扫描或部署成功。

## 测试

```bash
PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -v
```
