# Debug repeated DeepSeek blocked-loop generation

## Goal

让真实 DeepSeek 生成在遇到阻塞式 `while` 时能够根据校验反馈收敛，而不是连续五次
重复同类错误；当所有尝试仍失败时，保留足够且私有的逐轮现场用于定位问题。

## Background

- 本任务是 `07-25-database-user-auth` 的子任务。父任务负责正式内测的用户隔离、
  点数、Render + Supabase 持久化和真实 DeepSeek 端到端验收。
- 最近一次生成器修复已经允许可证明有限的 `while`，同时继续拒绝 `while True` 和
  `onCreate()` 中可能阻塞 UI 的循环。
- 2026-07-25 16:04 的真实界面截图显示：需求分析成功，但 DeepSeek 连续五次生成
  都因阻塞式 `while` 被拒绝，后续 test/package/publish 阶段无法开始。
- 当前五轮内部重试会把上一轮校验错误传给模型，但失败代码位于纠错 Prompt 尾部；
  revision/repair 场景还会在 correction 后追加旧代码，容易再次锚定违规实现。
- 当前只保存最终汇总错误。五轮候选代码、逐轮校验结果和模型元数据在内存中被覆盖，
  因此截图对应的历史 session 无法还原。

## Requirements

1. 纠错 Prompt 的最后有效指令必须是本轮校验错误和强制修复规则；失败 candidate 与
   previous code 只能作为有明确边界的参考代码出现在其前面。
2. 阻塞式 `while` 校验错误必须包含准确源码行号，并明确推荐非阻塞
   `lv.timer_create` 调度。
3. 每次内部生成 attempt 都要形成结构化诊断记录，至少包含 attempt 序号、校验状态、
   结构化错误、可用的候选代码和不含凭据的模型元数据。
4. 逐轮诊断由 SessionService 写入当前用户的私有 session 目录，并跟随现有对象存储
   同步；Generator 不直接依赖文件系统或云存储。
5. 失败归档必须保留该次运行的 generation attempt 目录；用户重试时不能覆盖上一轮
   失败现场。
6. 不保存 API key、认证 header、Cookie、完整请求 Prompt 或其他后端秘密。
7. 不通过 AST 自动把任意 `while` 改写成 timer；模型必须重新生成语义正确的代码。
8. 不放宽对 `while True` 和 UI 生命周期阻塞循环的安全校验。

## Acceptance Criteria

- [ ] 同时提供 previous code、失败 candidate 和 correction 时，最终 Prompt 中的强制
      修复规则位于所有参考代码之后。
- [ ] `while True` 或 `onCreate()` 阻塞循环仍被拒绝，错误包含对应行号和
      `lv.timer_create` 建议；有限循环继续通过。
- [ ] 模拟“前几轮失败、后续成功”时，每轮均产生顺序稳定的私有诊断记录。
- [ ] 模拟五轮全部失败时，最终错误仍为 `APP_GENERATION_FAILED`，并可从 session
      目录读取五轮结构化校验现场。
- [ ] 从失败检查点重试后，旧 attempt 进入独立 `failed-attempts/attempt-NNN/`，新一轮
      使用新的 attempt 目录，不覆盖旧文件。
- [ ] 私有调试产物不包含 API key、认证 header、Cookie 或完整请求 Prompt。
- [ ] 新增的生成器与 session 持久化回归测试通过；原后端测试不回退。

## Out of Scope

- 增加第六次以上模型重试或提高模型温度。
- 自动改写模型生成的 Python AST。
- 向普通用户展示原始 Prompt、模型原始响应或内部调试文件。
- 解决持久任务队列、服务进程重启恢复和全阶段 timeout；这些仍属于后续 P0-3。
