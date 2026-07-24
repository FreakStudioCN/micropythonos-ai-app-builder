# Runner

Runner 是浏览器工作流的受控执行层。`mpos-*-web` Skill 是协议合同，
不是可以直接执行的 shell 脚本；Runner 读取合同和 JSON schema，由后端
执行明确允许的文件、脚本、预览、打包和设备操作。

目录：

- `schemas/`：协议、阶段结果和设备结果的 JSON Schema。
- `adapters/mpos/`：Skill 合同到后端阶段执行器的映射说明。
- `scripts/`：只允许服务器维护的白名单脚本；前端和模型不能提交任意命令。

每个阶段必须写 result JSON、`phase_complete.*.json`、checkpoint，并更新
`artifact_manifest.json` 和 `activity_log.jsonl`。
