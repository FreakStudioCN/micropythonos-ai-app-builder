# Controlled scripts

这里只能放仓库维护者审核过的固定脚本。请求只能传操作名称和受限目标，
不能传 shell 字符串。当前 Python 语法检查由后端 `ScriptDispatcher`
执行；后续 desktop smoke、MPK 校验和 mpremote 适配也应遵守同一规则。
