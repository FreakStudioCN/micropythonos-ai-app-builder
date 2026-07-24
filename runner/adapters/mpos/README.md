# MPOS Skill adapter

后端的 `MposSkillAdapter` 对 `vendor/MicroPython_Skills/mpos-*-web/SKILL.md`
做名称、版本和 SHA256 校验。阶段执行器随后生成 Skill 合同规定的结构化
产物。Skill 文档不能注入 shell；所有副作用必须经过权限服务和白名单执行器。

阶段顺序：

1. analyze
2. prepare-deps
3. generate
4. test
5. package
6. deploy
7. publish-check
