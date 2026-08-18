# MicroPythonOS AI App Builder 跨设备能力接入说明

> **状态说明：本文是这套能力接入方案的设计记录，不是待办清单。**
> 其中的字段契约与错误码已经落在 `main` 上——`required_capabilities`、`required_accessories`、
> `runtime_fallbacks`、`physical_validation_required` 见 `backend/app/models.py` 及 session/checkpoint 流程；
> `WEB_PREVIEW_UNSUPPORTED` / `HARDWARE_CAPABILITY_UNAVAILABLE` / `MPOS_CAPABILITY_API_MISSING` /
> `DIRECT_HARDWARE_ACCESS_FORBIDDEN` 与 `mpos-board-capabilities-v1` 均已实现，`target_board` 未被引入。
> 下文第 3、7、9 节保留祈使语气的原文作为设计依据，读到「需要增加」时按「设计要求」理解，
> 具体实现状态以代码为准。

## 1. 核心决策

App 生成流程不得增加板卡选择器。MicroPythonOS 已负责检测板卡，并通过系统 Manager 暴露硬件能力。浏览器用户只需要描述希望实现的功能；生成的 App 将需求表达为 `camera`、`audio.output`、`sensor.imu`、`input.keypad` 等抽象能力，再由运行中的 MicroPythonOS 进行探测。

`mpos-dev-web/reference/board_capabilities.json` 是带版本的机器可读能力快照，用于诊断、测试规划和连接设备后的兼容性提示。它不是板卡白名单，也不得用于生成绑定具体板卡的代码。

能力判断优先级如下：

1. 在已连接或正在运行的设备上调用 MPOS Manager 进行运行时探测。
2. 读取 `DeviceInfo.hardware_id`，仅用于诊断和显示设备信息。
3. 使用 `board_capabilities.json` 提供辅助性静态信息。

未知型号或未来新增的板卡仍然是合法设备。运行时探测成功时，应覆盖缺失或已经过期的静态 JSON 信息。

## 2. 前端修改要求

前端不得增加板卡下拉框、强制板卡问题或按板卡生成 App 的模式。

App 创建页面应当：

- 允许用户直接描述希望制作的 App。
- 展示分析得到的能力标签，例如“需要摄像头”“需要麦克风”“支持按键操作”。这些标签只是说明，不是选择器。
- 说明哪些硬件能力能够在 Web 预览中真实运行、模拟运行或无法运行。
- 当 Web 预览缺少摄像头、麦克风、传感器、SD 卡、GPS、红外或 LoRa 时，展示稳定的占位或降级状态，而不是把它当成生成代码损坏。
- 在连续修改、重试、恢复和 checkpoint 恢复过程中保留对话记录和能力需求。

用户明确授权串口或设备操作后，设备面板应显示：

- 是否检测到 MicroPythonOS。
- `DeviceInfo.hardware_id`，仅作为诊断信息，不作为必选项。
- 每一项所需能力的运行时探测结果。
- 兼容性警告和需要完成的真机验证步骤。

前端不能只根据静态 JSON 宣称某项硬件已经支持。当预览返回 `WEB_PREVIEW_UNSUPPORTED`，或连接设备返回 `HARDWARE_CAPABILITY_UNAVAILABLE` 时，不能自动让模型修改 App 代码。

## 3. 后端 API 和 Session 修改

当前浏览器项目通过 `backend/app/runner_services.py` 加载 `vendor/MicroPython_Skills` 中的 skill。只更新 `~/.claude/skills` 不会改变浏览器后端的生产行为。浏览器仓库必须同步更新 vendored Skills 子模块或目录，并固定兼容的 MicroPythonOS commit。

需要在 `SessionCreateRequest`、`GenerateRequest`、session 状态、checkpoint、重试请求和生成产物中增加：

```json
{
  "required_capabilities": ["camera", "input.keypad"],
  "required_accessories": [],
  "runtime_fallbacks": {
    "camera": "没有摄像头时保留其他功能，并显示清楚的不可用状态。"
  },
  "physical_validation_required": true
}
```

不要增加 `target_board`。`web_preview`、`serial_port_scan`、`firmware_flash` 等字段表示浏览器和后端执行环境的能力，必须与 App 所需硬件能力分开保存。

后端在生成代码前必须读取 `board_capabilities.json` 的 `feature_contracts`：

- `portable_api=true`：允许生成，但必须把推荐 API、运行时 probe、fallback、预览限制、生命周期、权限和真机测试要求传给生成器。
- `portable_api=false`：停止自动实现硬件功能，返回 `MPOS_CAPABILITY_API_MISSING`。
- `contract_status=partial`：允许使用现有可移植 API，但必须把限制写入 warning 和测试计划。
- `.board_private`：只能表示板模块内部存在该硬件或配置，不能作为普通 App API 暴露。

Session 元数据还应保存并返回固定版本：

```json
{
  "skill_commit": "<MicroPython_Skills commit>",
  "mpos_commit": "<MicroPythonOS commit>",
  "board_capabilities_schema": "mpos-board-capabilities-v1"
}
```

这样可以避免恢复旧 session 时，后台静默切换到不同的 API 或板卡能力快照。

## 4. 需求分析和代码生成修改

目前实际控制提示词、确定性模板、代码修复和 API 校验的是 `backend/app/generator.py`，不能只修改 Markdown skill。后端生成器必须实现相同规则：

- 从用户需求中提取 `required_capabilities`。
- 区分板载能力和用户明确提出的 `required_accessories`，不能把板载功能转成驱动搜索任务。
- 摄像头 App 使用 `CameraManager`，并优先复用或继承 `CameraActivity`。
- 运行时使用 `CameraManager.has_camera()` 探测摄像头。
- 没有摄像头时保留其他可用功能和清楚的降级状态。
- 只有能力合同允许时，才能使用 `AudioManager`、`SensorManager`、`LightsManager`、`BatteryManager`、`SDCardManager`、`InputManager` 和 `ConnectivityManager`。
- 普通 App 禁止生成 `mpos.board.*`、板模块导入、GPIO 映射、总线编号、芯片驱动、摄像头方向补丁，以及直接使用 `machine.Pin/I2C/SPI/UART/I2S/ADC` 或 NeoPixel。
- 板载硬件不得触发 UpyPI 驱动搜索。只有用户明确提出外接配件，并确认接线和资源冲突后，才允许搜索驱动。
- 除 pointer 操作外，还必须生成可见的 LVGL focus 导航。
- 摄像头、音频、灯光和其他有状态的硬件必须生成暂停、退出和资源恢复逻辑。

API 校验器必须识别可移植的 MPOS 根导出和能力探测 API。后端必须把 `mpos-gen-app/scripts/check_app_hardware_policy.py` 作为强制门禁，用于拒绝板模块导入和底层硬件构造器。

后端还需要检查：

- 是否写入了板卡 ID、GPIO 表或总线表。
- 每个能力是否有运行时 fallback。
- 交互是否只能通过触摸完成。
- Activity 退出时是否缺少硬件清理。

只有已经确认的外接配件 handoff 才允许使用受限的底层硬件例外。

`mpos_api_summary.json` 和 `lvgl_api_summary.json` 仍然是所有生成任务的强制输入。MicroPythonOS API 变化时，生产环境必须重新生成并固定配套的 summary。

## 5. 预览、测试和部署修改

Web 预览无法模拟所有物理外设。当所需硬件在 Web 中没有模拟实现时，应返回：

```json
{
  "result": "partial",
  "structured_errors": [
    {
      "code": "WEB_PREVIEW_UNSUPPORTED",
      "owner": "external",
      "retryable": false
    }
  ]
}
```

这种结果不能启动代码修复循环。桌面和 Web 预览可以验证布局、焦点操作和 fallback，但不能证明物理硬件已经正常工作。

物理部署流程：

1. 请求扫描和连接串口设备的权限。
2. 确认设备已经安装 MicroPythonOS；如果没有，提示使用 `https://install.micropythonos.com/`。
3. 读取 `DeviceInfo.hardware_id`，并调用每项所需能力的 Manager probe。
4. 将结果与 `board_capabilities.json` 比较，但静态表只用于诊断。
5. 安装 App，执行能力专项测试，并检查退出后的资源恢复情况。

不同硬件的验收示例：

- 摄像头：完成预览和拍照，检查颜色、方向，并确认退出后 Launcher 输入恢复正常。
- 音频：完成播放或录音，并确认停止和关闭后资源被释放。
- RGB 灯：检查 LED 索引范围，退出后清除或恢复灯光状态。
- SD 卡：格式化前必须出现破坏性操作确认。
- 交互 App：在设备支持时同时提供 pointer 和 focus/keypad 操作证据。

只有一张 App 截图不足以证明硬件功能已经通过验证。

## 6. 产物和错误要求

在 `analysis_result.json`、generation result、checkpoint 和最终 artifact manifest 中增加：

- `required_capabilities`
- `required_accessories`
- `runtime_fallbacks`
- `physical_validation_required`
- 连接设备后的 `detected_hardware_id`
- `runtime_capability_results`
- `skill_commit`、`mpos_commit` 和 capability schema 版本

错误归属必须明确：

- 预览环境没有模拟某项物理能力：`WEB_PREVIEW_UNSUPPORTED`，owner 为 `external`。
- MPOS 已有可移植 API，但连接设备没有对应硬件：`HARDWARE_CAPABILITY_UNAVAILABLE`，owner 为 `device`。
- MPOS 还没有提供可移植能力 API：`MPOS_CAPABILITY_API_MISSING`，owner 为 `micropythonos`。
- 生成代码直接访问板模块或底层驱动：`DIRECT_HARDWARE_ACCESS_FORBIDDEN`，owner 为 `skill`，允许重新生成修复。
- 硬件能够打开，但退出后破坏输入或没有恢复资源：属于 MicroPythonOS 或板级支持问题，必须附带真机证据。

## 7. 推荐实施顺序

1. 更新浏览器仓库 vendored 的 `MicroPython_Skills`，并让后端能够读取能力 JSON。
2. 增加 request、session 和 checkpoint 字段，但不增加板卡选择器。
3. 更新需求分析和生成器提示词、模板，使所有硬件功能都采用 capability 模型。
4. 扩展 API 校验，并增加底层硬件访问、输入模式和生命周期策略校验。
5. 实现 Web 预览的 partial 结果处理，避免进入无限修复。
6. 实现设备连接后的运行时能力探测和兼容性显示。
7. 增加单元测试、session 恢复测试、Web 预览测试和真机验收测试。

## 8. 验收标准

- 用户不需要说明或选择板卡，就能提出硬件功能需求。
- 普通生成 App 不包含板卡 ID、GPIO/总线映射、板模块导入或板卡专用驱动。
- 可移植硬件功能包含运行时 probe 和可用的 fallback。
- 不可移植能力返回 `MPOS_CAPABILITY_API_MISSING`，而不是生成虚假代码。
- 交互 App 不假设设备一定具有触摸屏。
- Web 预览限制不会触发无限代码修复。
- 只有在授权和连接设备后，才判断实际设备能力。
- 未收录的新板卡在运行时 probe 成功时不会被拒绝。
- Session 恢复后保留能力需求和准确的 MPOS/Skills 版本。
- 只有完成物理拍照和退出后的输入恢复验证，才能宣称摄像头功能成功。

## 9. 浏览器仓库文件级修改清单

### 后端

- `backend/app/models.py`：给请求和 session 响应模型增加 `required_capabilities`、`required_accessories`、`runtime_fallbacks` 和 `physical_validation_required`，不要增加 `target_board`。
- `backend/app/session_service.py`：在创建、连续修改、重试、checkpoint、恢复、取消和 artifact handoff 中持续保存这些字段。
- `backend/app/runner_services.py`：加载固定版本的 `board_capabilities.json` 和 `docs-hardware-capabilities.md`，并暴露 skill/MPOS commit ID。
- `backend/app/generator.py`：向提示词和模板注入可移植能力合同；遇到不可移植能力时停止；运行 `check_app_hardware_policy.py`；检查 fallback、焦点导航和生命周期清理。
- API 和策略校验：正确区分 `MPOS_CAPABILITY_API_MISSING`、`DIRECT_HARDWARE_ACCESS_FORBIDDEN`、`HARDWARE_CAPABILITY_UNAVAILABLE` 和 `WEB_PREVIEW_UNSUPPORTED`，不能把设备或预览限制送入 App 修复循环。
- 设备服务：用户授权后读取 `DeviceInfo.hardware_id`，执行每项可移植 capability probe，并将证据保存到 `runtime_capability_results`。

### 前端

- 不增加板卡选择器。
- 分开展示所需能力标签和外接配件标签。
- 明确区分“可移植”“等待 OS API”“连接设备后可用”“设备不可用”“已模拟”和“预览不支持”等状态。
- 麦克风、串口/设备写入、SD 格式化、固件烧录和外接硬件接线必须分别请求明确确认。
- 连续对话修改和 session 恢复时保留能力需求与探测结果。
- 对 OS API 缺失、物理硬件缺失或预览限制，不显示自动“AI 修复代码”操作。

### 测试

- 为所有新增字段增加模型序列化和 checkpoint/resume 测试。
- 为每种禁止的 import、硬件构造器以及外接配件例外增加生成策略测试。
- 增加 pointer 和 focus/keypad 两种输入路径测试。
- 验证不支持的硬件在预览中返回 partial，且不会启动重试循环。
- 增加已知板卡、未知板卡、能力可用、能力不可用和静态元数据过期等设备探测测试。
