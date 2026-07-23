# Frontend

MicroPythonOS AI App Builder 的浏览器模拟前端。

## 本地启动

```bash
npm install
npm run dev
```

浏览器打开终端显示的地址（通常是 `http://localhost:5173`）。

当前页面连接本机 FastAPI，经过逐项权限确认后真实调用 DeepSeek。页面提供：

- SSE 活动时间线与刷新恢复；
- 每个 `permission_id` 只能回答一次的权限卡片；
- warning、blocked、failed、cancelled、timeout 独立状态；
- MicroPythonOS WASM 可选预览；
- 带相对路径、角色、大小、SHA-256、MIME 和阶段的 Artifact Browser；
- 明确可见的系统安装、设备扫描和 uPyStore 发布检查入口。

Web preview 只是兼容性预览，不等于真实硬件验证。当前后端未启用串口能力时，
设备面板会显示真实的不可用状态和 MicroPythonOS 安装器链接。
