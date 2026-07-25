# Technical Design

## Root Cause

校验器和五轮重试都在工作，但纠错信息的排列削弱了反馈：失败 candidate 位于
correction 尾部，revision/repair 的 previous code 又位于整个用户 Prompt 尾部。
低温模型因此容易继续复现刚看到的阻塞循环。与此同时，内部 attempt 未持久化，
失败后无法证明每轮实际收到和生成了什么。

## Prompt Contract

每轮用户 Prompt 按以下顺序组装：

```text
base request
previous code reference (optional, delimited)
failed candidate reference (optional, delimited)
final correction block
```

final correction block 必须位于最后，并包含：

- 当前 attempt 序号。
- 校验错误类型、准确行号和简短原因。
- 明确禁止保留同类阻塞循环。
- 对 UI 周期更新明确要求使用 `lv.timer_create`。
- 要求返回完整、可解析且已修复的 `app_code`。

参考代码是待修对象，不是可模仿的正确示例。使用稳定 delimiter 隔离，避免它与最终
指令混在一起。

## Attempt Diagnostic Flow

```text
DeepSeek response
  -> parse candidate
  -> validate candidate
  -> emit private GenerationAttempt record
  -> SessionService writes session-local artifacts
  -> existing session sync uploads them to private object storage
```

Generator 通过内部 callback/collector 发出 attempt 记录，不直接写 session 路径。
SessionService 负责目录、归档和对象存储边界。

建议目录：

```text
generation-attempts/run-NNN/attempt-NNN/
  candidate.py       # 仅在成功解析出 app_code 时存在
  validation.json    # attempt、status、error code/message/line
  model_meta.json    # 可用的 model/request/token 元数据，不含请求凭据
```

不要保存完整请求 Prompt、HTTP header 或 API key。模型原始响应默认不落盘；候选代码
已足以还原当前阻塞循环问题，并可控制隐私与体积。

## Retry and Archive Semantics

- 单次 generate 调用使用新的 `run-NNN`，避免 retry 覆盖旧 attempt。
- 内部五轮 attempt 顺序写入同一 run。
- 成功后保留之前失败的 attempt，便于解释模型如何收敛。
- 五轮耗尽后，SessionService 继续写现有 structured error 和 checkpoint。
- 外层“从失败检查点重试”归档时，一并移动/复制当前 run 到对应
  `failed-attempts/attempt-NNN/`。

## Compatibility and Security

- 对外 `generation_result.json` 和 SSE error contract 保持兼容。
- 调试目录属于现有 owner-scoped session，不新增公开下载入口。
- 对象存储沿用私有 bucket 和现有 owner 校验。
- 不改变计费幂等键，也不因内部 attempt 再次扣点。

## Rollback

Prompt 顺序和 attempt 记录器可独立回退。若诊断持久化出现问题，生成器仍应返回原有
成功结果或 `APP_GENERATION_FAILED`，不能让调试写入覆盖生成主错误。
