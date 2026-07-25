# Implementation Plan

1. 在 `backend/app/generator.py` 明确分离参考代码与最终 correction block，确保
   previous code 和失败 candidate 都位于强制修复规则之前。
2. 为阻塞式 `while` 校验结果补充 AST 行号，并保持有限循环通过、无限循环拒绝。
3. 定义内部 generation attempt 记录和可选 sink/collector；在 JSON 解析失败、缺字段、
   代码校验失败与成功路径上各记录一次，不泄露请求凭据或完整 Prompt。
4. 在 `backend/app/session_service.py` 为每次 generate 分配新的 run 目录，原子写入
   candidate 与结构化 validation/model metadata，并复用现有 session 对象存储同步。
5. 扩展失败归档逻辑，使当前 generation run 随 session 状态一起保留；重试创建新 run。
6. 在 `backend/tests/test_generator_quality.py` 增加 Prompt 尾部顺序、阻塞循环行号和
   多轮 correction 收敛回归测试。
7. 在 session service 测试中覆盖五轮耗尽、先失败后成功、失败归档和秘密字段缺失。
8. 按用户明确许可后执行窄验证，再执行后端全量测试；未获许可前不运行测试。

## Validation Commands

```bash
PYTHONPATH=backend backend/.venv/bin/python -m unittest backend.tests.test_generator_quality -v
PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -v
```

## Risk and Rollback Points

- Prompt 重排可能影响 revision 生成质量；测试必须同时覆盖有/无 previous code。
- 诊断写入失败不能遮蔽原始 generation 结果，写入路径需采用 best-effort 或明确的次级日志。
- generation attempt 不得加入面向普通用户的公开 artifact manifest。
- 若对象存储同步明显放大延迟，可保留本地逐轮写入，在阶段结束时批量同步。

## Stop Condition

满足 PRD 验收标准后停止，不增加重试次数、不做自动 AST 修复，也不顺带实现持久队列。
