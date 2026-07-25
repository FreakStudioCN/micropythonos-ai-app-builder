# Generation Retry Debug Research

## Evidence

- `backend/app/generator.py:946-1004` 最多尝试五次；每次校验失败都会构造 correction，
  下一轮确实收到上一轮错误。
- `backend/app/generator.py:904-911` 当前把完整失败 candidate 放在修复建议之后。
- `backend/app/generator.py:267-281` 在 revision/repair 场景把 previous code 追加到
  correction 之后，使旧代码成为 Prompt 尾部。
- `backend/app/generator.py:388-476` 能拒绝 `while True` 和 `onCreate()` 中不能证明
  有限的循环，但错误没有源码行号。
- `backend/app/session_service.py:2088-2121` 在生成耗尽后只保存最终聚合错误和活动日志。
- `backend/app/session_service.py:991-1028` 的外层重试只携带最终错误/日志尾，失败归档
  只复制已有结果文件，不包含内部五轮 candidate。
- 本地唯一 session 是 12:07 的 deterministic demo 成功样例；截图为 16:04，故无法从
  当前本地目录还原截图对应的五轮原始模型输出。

## Conclusion

连续失败不是“完全没有反馈”，而是纠错 Prompt 尾部仍由违规参考代码占据，且低温
重试容易复现同一结构。最窄行为修复是重排 Prompt 并提高错误定位精度；最窄可观测性
修复是以私有 session artifact 保存每轮候选与结构化校验结果。

## Rejected Approach

不使用 AST 自动把 `while` 改成 `lv.timer_create`。任意循环的状态、退出条件和 UI 更新
语义无法机械转换，自动改写可能产生更隐蔽的功能错误。
