import asyncio
import ast
import base64
import io
import json
import os
import re
import struct
import threading
import time
import zipfile
import zlib
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import httpx

from .models import GenerateRequest, GenerateResponse, GeneratedFile
from .runner_services import hardware_capability_registry


SYSTEM_PROMPT = """
你是 MicroPythonOS App 代码生成器。请根据用户需求生成一个最小但完整、可运行的 App。
只输出 JSON 对象，不要 Markdown，不要代码围栏，不要解释文字。

运行环境不是 CPython，而是 MicroPythonOS：
- UI 使用 `import lvgl as lv`
- Activity 使用 `from mpos import Activity`
- 入口类必须继承 Activity，并实现 `onCreate(self)`
- onCreate 中创建 `screen = lv.obj()`，最后调用 `self.setContentView(screen)`
- 不使用 tkinter、PyQt、HTML、React、文件系统绝对路径或 CPython 第三方包
- 严禁使用 eval、exec、compile、open、subprocess、os.system
- 计算器必须自己实现安全的数字与运算符处理，不能用 eval 或 exec 计算表达式
- 优先使用简单、稳定的 LVGL API：lv.obj, lv.label, lv.button, lv.textarea, lv.flex_flow
- 当前是新版 LVGL：按钮必须写 `lv.button(parent)`，严禁旧名称 `lv.btn`
- 不使用 `lv.scr_act()`；新建 screen 后通过 Activity.setContentView 显示
- 完全不设置字体，不引用任何 `lv.font_*` 常量，让控件自然继承系统字体
- 不使用 lv.theme、自建 style 对象、复杂 style selector、grid template 或当前 Web 构建未验证的 LVGL API
- 必须使用控件自己的、已经验证的 set_style_* 方法完成现代化视觉设计，style selector 参数统一传 0
- 事件回调必须使用 `widget.add_event_cb(callback, lv.EVENT.CLICKED, None)`
- 严禁使用 `lv.event_code`、`lv.EVENT_CLICKED` 等其他事件名称；当前 MicroPythonOS 只支持 `lv.EVENT.CLICKED` 这种写法
- 不使用 `label.set_text_align()`；需要相对父容器摆放文字时使用
  `label.align(lv.ALIGN.CENTER, x, y)`，只能传 align、x、y 三个参数
- 需要相对另一个控件摆放时使用
  `label.align_to(other, lv.ALIGN.OUT_BOTTOM_MID, x, y)`；严禁写
  `label.align(other, lv.ALIGN.CENTER, x, y)`，该旧式写法会在当前绑定中参数超量
- Web 模拟器的默认字体不含中文字形，所有可见控件文字必须使用简短英文或 ASCII，避免显示方框乱码
- 实际显示区域是 320x240，不是手机屏幕；优先使用百分比尺寸和 flex 布局，确保所有内容都在屏幕内
- 游戏必须真的提供可点击的开始/跳跃/重置等交互；日历使用 label、button、obj 组成简单网格，不使用未验证的 calendar 高级 API
- 游戏不能依赖电脑键盘、外接手柄或设备硬件键；所有操作必须在 App 画面内提供可点击按钮
- 射击游戏必须在底部提供清晰的 `LEFT`、`RIGHT`、`FIRE` 三个屏幕按钮：LEFT/RIGHT 点击后改变玩家位置，FIRE 点击后创建或激活子弹
- 射击游戏必须保存玩家和子弹的数字坐标，用 `set_pos()` 更新对象；使用 `lv.timer_create()` 更新子弹、敌人、碰撞和分数
- 不调用任何控件的 `get_pos()`、`get_coords()`、`get_x()`、`get_y()`；位置必须保存在 `self.player_x`、`self.bullet_y` 等 Python 数字状态中
- 创建控件时优先把引用保存到 `self.day_buttons` 等 Python 列表，需要数量时优先使用 `len(self.day_buttons)`；当前绑定允许 `parent.get_child(index)` 和 `parent.get_child_count()`，但禁止旧名称 `get_child_cnt()` 和不稳定的 `get_child_by_type()`
- `onCreate()` 必须快速返回，严禁 `while True`、界面主循环以及
  `sleep()`、`sleep_ms()` 等阻塞等待
- 计算器解析表达式等纯计算逻辑允许使用有明确退出条件的有限 `while`；
  循环体必须推进索引或缩小集合。动画、倒计时和游戏更新仍必须使用 lv.timer_create
- 动画和游戏更新必须使用 `self.update_timer = lv.timer_create(self.update_frame, 33, None)`；回调接收 timer 参数，不能自己写主循环
- `lv.timer_create(...)` 返回的定时器必须保存为 Python 引用；停止周期定时器时先判断引用不为 None，再调用 `self.update_timer.delete()` 并把引用设回 None
- 严禁 `lv.timer_del(timer)`、`timer._del()` 和 `set_repeat_count(0)`；一次性定时器使用 `set_repeat_count(1)`，且不要再手工删除
- MicroPythonOS 的精简 `random` 模块没有 `shuffle()`；需要洗牌时自己使用 `random.getrandbits()` 实现 Fisher-Yates，严禁调用 `random.shuffle()`
- LVGL 控件清除状态使用 `widget.remove_state(state)`，严禁旧式 `widget.clear_state(state)`
- 每个 App 都必须实现 `self_test(self)`，程序化调用真实功能方法并比较操作前后的状态，返回至少两个布尔结果组成的 dict
- `self_test()` 不能直接返回写死的 True；必须包含方法调用和前后值比较，并在结束前恢复被修改的状态，保证用户看到的是初始界面
- 所有代码必须放在一个入口 Python 文件中

只使用下面这些稳定的 UI 能力，不要猜 API：
- 创建：lv.obj(parent)、lv.label(parent)、lv.button(parent)、lv.textarea(parent)
- 布局：set_size、set_width、set_height、set_pos、set_x、set_y、
  align(align, x, y)、align_to(other, align, x, y)、center、
  set_flex_flow(flow)、set_flex_align(main, cross, track)
- set_flex_flow 只能传一个 flow 参数，正确示例：`container.set_flex_flow(lv.FLEX_FLOW.ROW_WRAP)`；严禁额外传 selector 或 0
- 交互：add_event_cb(callback, lv.EVENT.CLICKED, None)、lv.timer_create(callback, milliseconds, None)、timer_handle.delete()
- 文字：label.set_text、label.get_text
- 颜色：lv.color_hex(0xRRGGBB)
- 安全样式：set_style_bg_color、set_style_bg_opa、set_style_text_color、set_style_radius、
  set_style_border_width、set_style_border_color、set_style_pad_all、set_style_pad_hor、
  set_style_pad_ver、set_style_shadow_width、set_style_shadow_color、set_style_shadow_opa

视觉质量是验收条件，不是可选项。界面风格必须根据 App 主题变化：
- 不要机械复用固定的深蓝色模板；除非用户明确要求深色科技风，否则不要默认使用
  0x0F172A、0x1E293B、0x6366F1 这组旧配色
- 只使用 4 到 6 种协调颜色，确保背景、卡片、主色、强调色、正文和次要文字之间有足够对比度
- screen 必须设置背景色；内容区域要有清晰的标题、状态/分数和主操作区
- 卡片或控制区使用 10 到 16px 圆角、合理内边距和无边框或细边框
- 主按钮使用主色背景和高对比文字；次按钮降低视觉权重
- 320x240 内避免堆满控件，四周至少留 8px 安全边距，按钮高度至少 34px
- 游戏要有明确的场景背景、玩家/敌人/子弹配色、顶部 HUD 和底部操作区
- 禁止只用 LVGL 默认白底、默认灰按钮交付成品

JSON 格式必须严格为：
{
  "summary": "一句话说明生成了什么",
  "prompt_normalized_zh": "规范化后的中文技术需求",
  "prompt_normalized_en": "Normalized English technical requirement",
  "requirement_coverage": [
    {
      "requirement": "用户明确提出的一项功能或交互，不得写泛泛描述",
      "implementation": "实现该需求的状态、方法和可见控件",
      "verification": "用户如何在界面上验证它确实可用"
    }
  ],
  "store_metadata": {
    "display_name_zh": "中文显示名",
    "display_name_en": "English display name",
    "short_description_zh": "中文短描述",
    "short_description_en": "English short description",
    "long_description_zh": "中文长描述",
    "long_description_en": "English long description",
    "release_notes_zh": "中文发布说明",
    "release_notes_en": "English release notes",
    "category": "tools 或 games 等分类"
  },
  "entrypoint": "app.py",
  "classname": "GeneratedApp",
  "app_code": "完整 Python 源码字符串",
  "acceptance_tests": ["用户可以完成的核心功能 1", "用户可以完成的核心功能 2"]
}

生成前必须先完整拆解需求，再写代码：
- requirement_coverage 必须覆盖用户明确要求的功能、交互、状态变化、定时行为和硬件能力；
  简单任务至少 2 项，复杂任务、连续修改和错误修复至少 3 项，禁止用“界面可用”“功能正常”等套话凑数
- implementation 必须逐项写出 app_code 中真实存在的 ASCII 方法名、Python 状态名或控件变量名，不能只说“已经实现”
- verification 必须描述用户在 320x240 界面上可以执行并看到结果的步骤
- app_code 必须逐项实现 requirement_coverage；不允许为了界面好看而漏掉需求，也不允许用静态文字、pass、假按钮冒充功能
- acceptance_tests 必须与 requirement_coverage 对应，描述具体操作和预期结果

参考最小代码结构：
import lvgl as lv
from mpos import Activity

class GeneratedApp(Activity):
    def onCreate(self):
        screen = lv.obj()
        label = lv.label(screen)
        label.set_text("Hello")
        label.center()
        self.setContentView(screen)

    def self_test(self):
        before = self.label.get_text()
        self.update_label("Test")
        changed = self.label.get_text() != before
        self.update_label(before)
        return {"label_updates": changed, "state_restored": self.label.get_text() == before}
"""


VISUAL_REQUIREMENTS = """
本次 App 必须达到可直接展示的视觉质量：
1. 严格使用本次需求后附带的专属视觉方向，不允许套用固定深蓝色模板，也不允许默认白底灰按钮。
2. 必须设置 screen 背景色、至少一个表面/控件背景色、文本色和圆角；主要内容卡片必须有内边距。
3. 信息需要有标题层、内容层和操作层，不能把控件随意堆在一起。
4. 游戏必须有 HUD、明确的玩家/敌人/子弹颜色和整齐的底部触控按钮。
5. 只能调用系统提示中列出的稳定 API；位置只从 Python 数字状态读取。
6. 使用 8px 间距体系：页面安全边距 8-12px，卡片间距 8px，控件内部间距 6-10px。
7. 至少创建三层视觉层级：页面背景、内容卡片、主要操作按钮；不能只给 screen 换颜色。
8. 每个主要控件必须明确设置尺寸或位置，避免重叠、贴边和大小不一致。
9. 主按钮、次按钮和危险操作使用不同但协调的颜色；同类按钮保持等高、等宽、等间距。
10. 输出前自行检查：屏幕 320x240 内无溢出、文字可读、触控按钮高度不小于 34px。
"""


REQUIREMENT_COVERAGE_REQUIREMENTS = """
需求完整性同样是验收条件：
1. 先把本次用户需求拆成 requirement_coverage，逐项写明 requirement、implementation、verification。
2. 简单任务至少 2 项；复杂任务、连续修改和错误修复至少 3 项需求—实现—验证记录。
3. implementation 必须点名 app_code 中实际存在的 ASCII 状态名、方法名或控件变量名；verification 必须是用户可执行且结果可见的步骤。
4. app_code 和 acceptance_tests 必须逐项对应覆盖表，禁止漏功能、静态占位、pass、假按钮和泛化套话。
"""


GENERAL_UI_BLUEPRINT = """
请按下面的安全设计系统组织界面：
- screen 使用本次专属调色板的页面背景；顶部放 32-40px 高的标题/状态区。
- 中间使用一个或多个有圆角、内边距和细边框的内容卡片。
- 底部或卡片内放主要操作区；同组按钮等高、等宽、间距一致。
- 标题文字明亮，说明文字使用次要颜色，关键数字或状态使用强调色。
- screen 设置页面背景；内容卡片设置背景、圆角、内边距和边框；主要按钮设置强调色、圆角和一致尺寸。
- 只使用系统提示列出的稳定 LVGL API，不使用主题、字体、grid、style 对象或坐标读取。
- 参考 MicroPythonOS 内置 App 的成品规律：主状态一眼可见、控件紧凑但不拥挤、每个操作都有明确反馈；
  只学习这些设计规律，不复制任何内置 App 的源码、素材或固定配色。
"""


APP_UI_BLUEPRINTS = {
    "calendar": """
日历成品骨架：
- 顶部 36px 月份导航条，左侧 PREV、中间英文月份和年份、右侧 NEXT。
- 导航条下方放 MON-SUN 七列星期标题，再放 6x7 日期触控网格；今天、选中日和普通日期必须有不同状态。
- 底部放 TODAY 主操作和当前选择摘要。日期按钮统一尺寸、圆角和间距，不能只显示一串数字文本。
- 月份切换和日期选择必须立即重绘网格并更新摘要。
""",
    "timer": """
计时与提醒成品骨架：
- 顶部 32px 标题/模式栏，中间使用大号视觉区域突出唯一主状态（剩余时间、进度或下次提醒）。
- 主状态下放一条简短状态说明；底部放 START/PAUSE、RESET 和必要的设置按钮，按钮等高等宽。
- 计时、暂停、重置必须由真实 Python 状态驱动；界面刷新统一使用 lv.timer_create，不能阻塞等待。
- 若需求包含记录，使用紧凑记录卡片显示最近状态，不要把记录与主数字混在同一行。
""",
    "calculator": """
计算器成品骨架：
- 顶部显示卡片清楚区分表达式和结果；下方使用 4 列触控键盘，数字键、运算符、清除键和等号键分级配色。
- 所有按键至少 34px 高，同类键等宽等高；等号是最醒目的主操作，清除键使用警示色但不抢占结果层级。
- 运算采用明确的数字/运算符状态机，支持用户要求的连续运算；严禁 eval/exec。
""",
    "dashboard": """
仪表盘成品骨架：
- 顶部显示标题和连接/刷新状态；中间用 2x2 或纵向指标卡展示 label、value、unit，关键数值使用强调色。
- 指标卡必须对齐并保留安全间距；底部放 REFRESH 或主要控制按钮，并提供最近更新时间/状态反馈。
- 不要用纯文本列表冒充仪表盘，不要让不同单位和值挤在一起。
""",
    "habit": """
习惯与喝水记录成品骨架：
- 顶部是今日目标和状态；中间用大数字/进度卡显示已完成量、目标量和下一次提醒。
- 主要操作（例如 +1 CUP / DONE）必须最大最醒目，旁边或下方放 UNDO/RESET/SET 等次操作。
- 若包含历史记录，使用最多 3 条的紧凑记录卡；每次操作后立即更新主状态、进度和提示文字。
""",
    "game": """
触控游戏成品骨架：
- 顶部 28-32px HUD 显示 TITLE、SCORE、LIVES/STATUS；中间是边界清晰的游戏场景；底部是完整触控操作区。
- 玩家、目标、障碍物和场景背景必须使用明显不同的颜色或形状，不能用文字标签代替全部游戏对象。
- 开始、游戏中、结束三个状态都要有明确画面反馈和可见 RESTART 操作。
""",
    "generic": """
通用工具成品骨架：
- 顶部 32-36px 标题/状态区；中间只突出一个核心任务，用 1-2 张卡片组织内容；底部放一个主操作和必要的次操作。
- 用户每次点击后必须看到文字、数值、颜色或控件状态发生变化，不能只有静态说明。
""",
}


VISUAL_PALETTES = (
    (
        "清新薄荷",
        "明亮、轻盈、适合健康与生活记录",
        "页面背景 0xF0FDF4、卡片 0xFFFFFF、主色 0x10B981、强调色 0xF59E0B、正文 0x16302B、次要文字 0x64748B",
    ),
    (
        "暖沙日光",
        "温暖、清楚、适合日历与效率工具",
        "页面背景 0xFFF7ED、卡片 0xFFFFFF、主色 0xF97316、强调色 0x7C3AED、正文 0x292524、次要文字 0x78716C",
    ),
    (
        "柔和薰衣草",
        "友好、精致、适合个人工具和创意应用",
        "页面背景 0xF5F3FF、卡片 0xFFFFFF、主色 0x8B5CF6、强调色 0xEC4899、正文 0x2E1065、次要文字 0x6B7280",
    ),
    (
        "晴空柠檬",
        "活泼、清爽、适合天气、学习与轻量互动",
        "页面背景 0xEFF6FF、卡片 0xFFFFFF、主色 0x0EA5E9、强调色 0xEAB308、正文 0x172554、次要文字 0x64748B",
    ),
    (
        "珊瑚奶油",
        "亲和、有温度、适合提醒、习惯和日常应用",
        "页面背景 0xFFF1F2、卡片 0xFFFFFF、主色 0xFB7185、强调色 0x14B8A6、正文 0x3F1D2E、次要文字 0x78716C",
    ),
)


def _visual_direction_for_prompt(prompt: str) -> str:
    """Choose a stable, varied palette without exposing model routing."""

    normalized = prompt.lower()
    game_hints = ("game", "游戏", "射击", "跑酷", "碰撞", "闯关")
    health_hints = ("健康", "喝水", "饮水", "习惯", "运动", "health", "water")
    productivity_hints = (
        "日历", "calendar", "待办", "清单", "番茄", "计时", "提醒", "计划"
    )
    if any(hint in normalized for hint in game_hints):
        return (
            "本次专属视觉方向：霓虹竞技场。使用页面背景 0x1C1026、卡片 0x2D1B3D、"
            "主色 0xA855F7、强调色 0xF59E0B、正文 0xFFF7ED、次要文字 0xC4B5FD。"
            "游戏场景可以深色，但不要使用旧的深蓝色模板。"
        )
    if any(hint in normalized for hint in health_hints):
        palette = VISUAL_PALETTES[0]
    elif any(hint in normalized for hint in productivity_hints):
        palette = VISUAL_PALETTES[1]
    else:
        # crc32 is deterministic across processes, unlike Python's hash().
        palette = VISUAL_PALETTES[zlib.crc32(normalized.encode("utf-8")) % len(VISUAL_PALETTES)]
    name, mood, colors = palette
    return (
        f"本次专属视觉方向：{name}。{mood}。{colors}。"
        "必须明显区别于固定深蓝模板，并保证文字与背景对比清晰。"
    )


def _app_archetype_for_prompt(prompt: str) -> str:
    """Map a product request to a deterministic, screen-sized UI archetype."""

    normalized = prompt.casefold()
    rules = (
        ("calendar", ("日历", "calendar", "日期选择", "month view")),
        ("calculator", ("计算器", "calculator", "四则运算")),
        (
            "game",
            (
                "游戏", "game", "跑酷", "射击", "闯关", "碰撞", "reaction",
                "反应力", "连连看", "棋", "迷宫",
            ),
        ),
        (
            "habit",
            (
                "喝水", "饮水", "习惯", "打卡", "water reminder", "habit",
                "饮水记录", "完成一杯",
            ),
        ),
        (
            "timer",
            (
                "番茄", "倒计时", "计时器", "timer", "pomodoro", "闹铃",
                "提醒", "reminder", "秒表", "stopwatch",
            ),
        ),
        (
            "dashboard",
            (
                "仪表盘", "dashboard", "状态面板", "监控", "传感器", "sensor",
                "数据面板", "空气质量", "天气", "weather",
            ),
        ),
    )
    for archetype, hints in rules:
        if any(hint in normalized for hint in hints):
            return archetype
    return "generic"


def _ui_blueprint_for_prompt(prompt: str) -> str:
    archetype = _app_archetype_for_prompt(prompt)
    labels = {
        "calendar": "日历",
        "timer": "计时与提醒",
        "calculator": "计算器",
        "dashboard": "数据仪表盘",
        "habit": "习惯与记录",
        "game": "触控游戏",
        "generic": "通用工具",
    }
    return (
        f"本次产品界面类型：{labels[archetype]}。以下骨架属于验收条件，"
        "必须在 320x240 内完整实现：\n"
        f"{APP_UI_BLUEPRINTS[archetype].strip()}"
    )


SHOOTER_UI_BLUEPRINT = """
射击游戏视觉蓝图：
- screen 为深色太空背景；顶部 HUD 高 32px，显示 TITLE、SCORE、LIVES。
- 中间游戏区使用独立深色卡片；玩家、敌人和子弹分别用青色、红色、黄色。
- 底部控制区固定为 LEFT、FIRE、RIGHT 三个等高按钮，FIRE 使用最醒目的强调色。
- 游戏对象坐标只保存在 self.player_x/self.player_y、bullet/enemy 数字状态中。
- 严禁出现 get_pos、get_coords、get_x、get_y；碰撞直接比较保存的数字坐标。
- 每帧只更新数字状态后调用 set_pos；绝不从 LVGL 对象反查位置。
"""


class GenerationError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        code: str = "GENERATION_FAILED",
        owner: str = "app",
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.details = details or {}
        self.code = code
        self.owner = owner
        self.retryable = retryable


class UpstreamGenerationError(GenerationError):
    """A sanitized, structured error returned by an AI upstream."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        failover_allowed: bool,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable
        self.failover_allowed = failover_allowed
        self.details = details or {}
        self.owner = "external"


class ApiValidationError(GenerationError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


GenerationAttemptSink = Callable[[dict[str, Any]], None]


def _build_user_prompt(request: GenerateRequest, correction: str = "") -> str:
    visual_direction = _visual_direction_for_prompt(request.prompt)
    ui_blueprint = _ui_blueprint_for_prompt(request.prompt)
    user_prompt = (
        "请生成 JSON。用户需求：\n"
        f"{request.prompt}\n\n"
        f"显示名：{request.display_name}\n"
        f"包名：{request.package_name}\n"
        "入口文件固定为 app.py，入口类固定为 GeneratedApp。\n\n"
        f"{VISUAL_REQUIREMENTS}\n{REQUIREMENT_COVERAGE_REQUIREMENTS}\n"
        f"{visual_direction}\n{GENERAL_UI_BLUEPRINT}"
        f"\n{ui_blueprint}"
    )
    if request.required_capabilities:
        contract = hardware_capability_registry.resolve(
            request.required_capabilities, request.runtime_fallbacks
        )
        user_prompt += (
            "\n\n<HARDWARE_CAPABILITY_CONTRACT>\n"
            "按能力生成，禁止选择或猜测开发板。只能使用下列契约中列出的 mpos Manager API；"
            "运行时必须先 probe/has_*，不可用时使用 fallback，并在 onDestroy 清理资源。"
            "禁止 mpos.board、machine.Pin/I2C/SPI/UART/I2S/ADC、NeoPixel、GPIO/总线映射和设备 ID。\n"
            + json.dumps(contract, ensure_ascii=False)
            + "\n</HARDWARE_CAPABILITY_CONTRACT>"
        )
    if _is_shooter_prompt(request.prompt):
        user_prompt += (
            "\n\n这是射击游戏，属于强交互任务。验收条件："
            "\n1. 屏幕底部必须有可见的 LEFT、RIGHT、FIRE 三个 lv.button。"
            "\n2. 三个按钮都必须通过 add_event_cb 连接到真实回调，不能只是显示文字。"
            "\n3. LEFT/RIGHT 每次点击都要改变玩家数字坐标并调用 set_pos。"
            "\n4. FIRE 必须创建或激活子弹，lv.timer_create 回调必须让子弹移动并处理越界或碰撞。"
            "\n5. 不得依赖键盘、硬件摇杆、外接手柄或隐藏控件。"
            f"\n\n{SHOOTER_UI_BLUEPRINT}"
        )
    if request.previous_code:
        user_prompt += (
            "\n\n<PREVIOUS_CODE_REFERENCE>\n"
            "这是已有 App 的连续修改参考，不是正确答案。"
            "必须保留未被用户要求删除的功能，同时修复后续 correction 指出的全部问题。\n"
            "允许重新组织整个视图层级和调色板来提升成品感；不能机械保留旧版布局或固定深蓝配色。"
            "但必须保持已有核心功能、数据状态和用户未要求删除的交互。\n"
            f"运行错误（如果为空则表示功能修改）：\n{request.runtime_error or '无'}\n\n"
            f"上一次 app.py：\n{request.previous_code}\n"
            "</PREVIOUS_CODE_REFERENCE>"
        )
    if correction:
        user_prompt += f"\n\n{correction}"
    return user_prompt


def _message_text(value: Any) -> str:
    """Normalize OpenAI-compatible string and multipart message content."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        # Some OpenAI-compatible providers return a single content part as an
        # object instead of wrapping it in a list.  Preserve generated JSON
        # objects and recursively unwrap the common text containers.
        if any(
            key in value
            for key in (
                "app_code",
                "code",
                "python_code",
                "source_code",
                "files",
                "result",
                "generated_app",
            )
        ):
            return json.dumps(value, ensure_ascii=False)
        for key in ("text", "content", "value", "output_text"):
            text = _message_text(value.get(key))
            if text:
                return text
        return ""
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            text = _message_text(item)
            if text:
                parts.append(text)
        return "\n".join(parts).strip()
    return ""


def _parse_model_json(message: dict[str, Any]) -> dict[str, Any]:
    """Parse JSON even when a compatible provider wraps it in prose/fences."""
    candidates = [
        _message_text(message.get("content")),
        _message_text(message.get("reasoning_content")),
    ]
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            function = call.get("function")
            if isinstance(function, dict):
                arguments = function.get("arguments")
                if isinstance(arguments, str):
                    candidates.append(arguments.strip())

    decoder = json.JSONDecoder()

    def load_object(raw: str) -> dict[str, Any] | None:
        # A few compatible APIs JSON-encode the JSON payload twice.  Unwrap at
        # most two string layers so malformed or adversarial output cannot
        # cause an unbounded parsing loop.
        current = raw
        for _ in range(3):
            try:
                parsed = json.loads(current)
            except json.JSONDecodeError:
                return None
            if isinstance(parsed, dict):
                return parsed
            if not isinstance(parsed, str) or parsed == current:
                return None
            current = parsed.strip()
        return None

    def payload_score(payload: dict[str, Any]) -> int:
        """Prefer the actual App contract over JSON snippets in reasoning.

        Reasoning models frequently mention small JSON examples (including an
        empty ``{}``) before emitting the final answer.  Returning the first
        decodable object therefore turns a valid provider response into a
        misleading "missing app_code" failure.  Only an object that normalizes
        to real Python App source is eligible for selection.
        """

        normalized = _normalize_generation_payload(payload)
        code = normalized.get("app_code")
        if not isinstance(code, str) or not code.strip():
            return 0
        score = 100
        if "import lvgl" in code:
            score += 10
        if "from mpos import Activity" in code:
            score += 10
        if isinstance(normalized.get("acceptance_tests"), list):
            score += 2
        if normalized.get("summary"):
            score += 1
        return score

    parsed_candidates: list[dict[str, Any]] = []

    for raw in candidates:
        if not raw:
            continue
        cleaned = raw.strip().lstrip("\ufeff")
        fenced = re.fullmatch(
            r"```(?:json)?\s*(.*?)\s*```",
            cleaned,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if fenced:
            cleaned = fenced.group(1).strip()
        parsed_object = load_object(cleaned)
        if parsed_object is not None:
            parsed_candidates.append(parsed_object)
        for match in re.finditer(r"\{", cleaned):
            try:
                parsed, _end = decoder.raw_decode(cleaned[match.start() :])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                parsed_candidates.append(parsed)

    if parsed_candidates:
        best_payload = max(parsed_candidates, key=payload_score)
        # Parsing and App-contract validation are separate stages.  Prefer the
        # candidate that contains real source across content *and* reasoning,
        # but still return a syntactically valid JSON object when every
        # candidate is incomplete; the generation loop then emits the precise
        # missing app_code / acceptance_tests error and can repair or fall back.
        return best_payload

    # Last-resort compatibility for models that ignored JSON mode but still
    # returned one complete Python source fence.  The normal code/API/product
    # validators still run afterwards, so accepting the envelope does not skip
    # any safety or MicroPythonOS compatibility checks.
    for raw in candidates:
        code_fence = re.search(
            r"```(?:python|py)\s*(.*?)\s*```",
            raw,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not code_fence:
            continue
        code = code_fence.group(1).strip()
        if "import lvgl" in code and "class " in code:
            return {
                "summary": "Generated MicroPythonOS App",
                "app_code": code,
                "acceptance_tests": [
                    "App starts without an exception",
                    "Primary controls update the visible App state",
                ],
            }
    # Some coding models honor the requested program contract but ignore the
    # JSON envelope and emit bare Python.  Recover that complete source instead
    # of throwing away an otherwise usable result.  Syntax and all product/API
    # validators still run after this parser, so this does not weaken checks.
    for raw in candidates:
        cleaned = raw.strip().lstrip("\ufeff")
        source_start = re.search(
            r"(?m)^(?:import\s+lvgl\s+as\s+lv|from\s+mpos\s+import\s+Activity)\s*$",
            cleaned,
        )
        if not source_start:
            continue
        code = cleaned[source_start.start() :].strip()
        try:
            ast.parse(code)
        except SyntaxError:
            continue
        if "import lvgl" in code and "class " in code:
            return {
                "summary": "Generated MicroPythonOS App",
                "app_code": code,
                "acceptance_tests": [
                    "App starts without an exception",
                    "Primary controls update the visible App state",
                ],
            }
    raise GenerationError("AI 生成服务没有返回可解析的生成结果")


def _normalize_generation_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Accept common JSON shapes while keeping one internal generation contract."""
    normalized = dict(payload)

    containers: list[dict[str, Any]] = [payload]
    for key in ("result", "data", "output", "app", "application", "generated_app"):
        value = payload.get(key)
        if isinstance(value, dict):
            containers.append(value)

    code: str | None = None
    for container in containers:
        for key in (
            "app_code",
            "code",
            "python_code",
            "source_code",
            "source",
            "app_py",
            "main_py",
        ):
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                code = value
                break
        if code:
            break

    if not code:
        for container in containers:
            files = container.get("files")
            if isinstance(files, dict):
                preferred_paths = (
                    "app.py",
                    "assets/main.py",
                    "main.py",
                )
                for preferred_path in preferred_paths:
                    value = files.get(preferred_path)
                    if isinstance(value, str) and value.strip():
                        code = value
                        break
                    if isinstance(value, dict):
                        content = value.get("content") or value.get("code")
                        if isinstance(content, str) and content.strip():
                            code = content
                            break
                if not code:
                    for path, value in files.items():
                        if not str(path).lower().endswith((".py", "app.py")):
                            continue
                        if isinstance(value, str) and value.strip():
                            code = value
                            break
                        if isinstance(value, dict):
                            content = value.get("content") or value.get("code")
                            if isinstance(content, str) and content.strip():
                                code = content
                                break
            elif isinstance(files, list):
                for item in files:
                    if not isinstance(item, dict):
                        continue
                    path = str(
                        item.get("path")
                        or item.get("name")
                        or item.get("filename")
                        or ""
                    )
                    content = item.get("content") or item.get("code")
                    if (
                        path.lower().endswith((".py", "app.py"))
                        and isinstance(content, str)
                        and content.strip()
                    ):
                        code = content
                        break
            if code:
                break

    tests: list[str] | None = None
    for container in containers:
        for key in (
            "acceptance_tests",
            "tests",
            "acceptance_criteria",
            "test_cases",
        ):
            value = container.get(key)
            if isinstance(value, list):
                cleaned = [str(item).strip() for item in value if str(item).strip()]
                if cleaned:
                    tests = cleaned
                    break
        if tests:
            break

    if code:
        normalized["app_code"] = code
    if tests:
        normalized["acceptance_tests"] = tests
    for container in containers[1:]:
        for key in ("summary", "store_metadata"):
            if key not in normalized and key in container:
                normalized[key] = container[key]
    return normalized


def _validate_requirement_coverage(
    payload: dict[str, Any],
    request: GenerateRequest,
    code: str = "",
) -> list[dict[str, str]]:
    """Require an auditable plan before accepting otherwise-valid code.

    Syntax and styling checks cannot tell whether a model silently dropped the
    second half of a multi-feature request.  The coverage ledger makes every
    accepted generation name the requested behavior, its concrete
    implementation, and a user-visible verification step.  It is deliberately
    validated before code validation so a corrected retry receives the full
    candidate plus an exact quality failure.
    """

    raw_coverage = payload.get("requirement_coverage")
    tier, _ = _classify_request_complexity(request)
    minimum = 3 if tier in {"complex", "revision", "repair"} else 2
    if not isinstance(raw_coverage, list) or len(raw_coverage) < minimum:
        raise GenerationError(
            "需求覆盖表不完整：requirement_coverage 必须至少包含 "
            f"{minimum} 项需求—实现—验证记录"
        )

    coverage: list[dict[str, str]] = []
    seen_requirements: set[str] = set()
    generic_phrases = {
        "功能正常",
        "界面可用",
        "正常工作",
        "works",
        "working",
        "usable",
    }
    for index, item in enumerate(raw_coverage, start=1):
        if not isinstance(item, dict):
            raise GenerationError(f"需求覆盖表第 {index} 项不是对象")
        normalized: dict[str, str] = {}
        for field in ("requirement", "implementation", "verification"):
            value = item.get(field)
            if not isinstance(value, str) or len(value.strip()) < 4:
                raise GenerationError(
                    f"需求覆盖表第 {index} 项缺少具体的 {field}"
                )
            normalized[field] = value.strip()
        requirement_key = normalized["requirement"].casefold()
        if requirement_key in seen_requirements:
            raise GenerationError("需求覆盖表包含重复需求，不能用同一项凑数")
        if any(phrase in requirement_key for phrase in generic_phrases):
            raise GenerationError("需求覆盖表必须写具体用户需求，不能使用泛化套话")
        if code:
            implementation_tokens = {
                token.casefold()
                for token in re.findall(
                    r"[A-Za-z_][A-Za-z0-9_]{2,}", normalized["implementation"]
                )
                if token.casefold()
                not in {
                    "the", "and", "with", "from", "into", "uses", "using",
                    "button", "label", "method", "state", "control", "callback",
                    "visible", "actual", "real", "implementation",
                }
            }
            lowered_code = code.casefold()
            if not implementation_tokens or not any(
                token in lowered_code for token in implementation_tokens
            ):
                raise GenerationError(
                    f"需求覆盖表第 {index} 项的 implementation 没有引用 app_code "
                    "中真实存在的状态、方法或控件标识符"
                )
        seen_requirements.add(requirement_key)
        coverage.append(normalized)
    return coverage


def _settings() -> tuple[str, str, str]:
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()
    if not key or key == "replace_with_your_deepseek_api_key":
        raise GenerationError(
            "未配置 DEEPSEEK_API_KEY。请复制 backend/.env.example 为 backend/.env 并填写 Key。"
        )
    return key, base_url, model


async def _collect_streaming_completion(
    response: httpx.Response,
    *,
    default_model: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Collect an OpenAI-compatible SSE response without losing whitespace.

    Provider read timeouts are idle timeouts while streaming. This lets a
    large App take longer than the configured window in total, as long as the
    provider is still returning data, while a genuinely stalled connection is
    still interrupted and failed over.
    """

    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    finish_reason: Any = None
    selected_model = default_model
    request_id: str | None = None
    usage: dict[str, Any] | None = None
    saw_event = False

    def append_fragment(value: Any, target: list[str]) -> None:
        # String fragments are individual tokens and must not be stripped:
        # doing so corrupts indentation and JSON string contents.
        if isinstance(value, str):
            target.append(value)
            return
        text = _message_text(value)
        if text:
            target.append(text)

    async for raw_line in response.aiter_lines():
        line = raw_line.strip()
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            # Compatible gateways can inject non-JSON keepalive lines.
            continue
        if not isinstance(chunk, dict):
            continue
        saw_event = True
        model_value = chunk.get("model")
        if isinstance(model_value, str) and model_value:
            selected_model = model_value
        id_value = chunk.get("id")
        if isinstance(id_value, str) and id_value:
            request_id = id_value
        usage_value = chunk.get("usage")
        if isinstance(usage_value, dict):
            usage = usage_value

        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        choice = choices[0]
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            delta = choice.get("message")
        if isinstance(delta, dict):
            append_fragment(delta.get("content"), content_parts)
            append_fragment(delta.get("reasoning_content"), reasoning_parts)
        if choice.get("finish_reason") is not None:
            finish_reason = choice.get("finish_reason")

    message: dict[str, Any] = {"content": "".join(content_parts)}
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
    if not message["content"] and not message.get("reasoning_content"):
        raise GenerationError(
            "AI generation service did not return a parseable result",
            details={
                "model": selected_model,
                "streamed": True,
                "saw_event": saw_event,
            },
        )

    choice = {"message": message, "finish_reason": finish_reason}
    body: dict[str, Any] = {
        "model": selected_model,
        "choices": [choice],
    }
    if request_id:
        body["id"] = request_id
    if usage is not None:
        body["usage"] = usage
    return body, choice, message


async def _call_deepseek_legacy(
    request: GenerateRequest,
    correction: str = "",
    timeout_seconds: float | None = None,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    key, base_url, model = _settings()
    active_provider = _ACTIVE_PROVIDER_CONFIG.get()
    user_prompt = _build_user_prompt(request, correction)
    provider_id = active_provider.id if active_provider is not None else "deepseek"
    provider_token_env = {
        "zhipu_glm52": "ZHIPU_MAX_OUTPUT_TOKENS",
        "kimi": "KIMI_MAX_OUTPUT_TOKENS",
        "kimi_k27": "KIMI_MAX_OUTPUT_TOKENS",
        "deepseek": "DEEPSEEK_MAX_OUTPUT_TOKENS",
    }.get(provider_id, "AI_MAX_OUTPUT_TOKENS")
    # Large unconditional output budgets make reasoning-oriented providers
    # spend minutes filling a response even for a one-screen utility. Scale the
    # budget with the actual request instead. The upper bounds also prevent an
    # old 12k/24k deployment setting from turning a small request into a
    # ten-minute generation.
    routing_tier, _ = _classify_request_complexity(request, correction)
    tier_token_policy = {
        "simple": (5_000, 3_500, 6_000),
        "standard": (6_500, 4_500, 8_000),
        "complex": (8_500, 6_000, 10_000),
        "revision": (8_500, 6_000, 10_000),
        "repair": (7_500, 5_000, 9_000),
    }
    default_tokens, minimum_tokens, maximum_tokens = tier_token_policy.get(
        routing_tier,
        tier_token_policy["standard"],
    )
    max_tokens = max(
        minimum_tokens,
        min(
            maximum_tokens,
            int(
                _first_env(
                    provider_token_env,
                    "AI_MAX_OUTPUT_TOKENS",
                    "DEEPSEEK_MAX_TOKENS",
                    default=str(default_tokens),
                )
            ),
        ),
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
        # GLM and current Kimi K2 models enable deep thinking by default. App
        # generation already has deterministic validators and repair passes,
        # so instant mode is both faster and more predictable. Moonshot's
        # current API documents the same ``thinking: disabled`` contract for
        # K2.5/K2.6. K2.7 Code currently rejects that option, so it is excluded.
        **(
            {"thinking": {"type": "disabled"}}
            if provider_id in {"deepseek", "zhipu_glm52", "kimi"}
            else {}
        ),
        # Kimi K2.6 and K2.7 reject custom temperature values with HTTP 400.
        # Let those models use their required server-side default.
        **(
            {"temperature": 0.12 if correction else 0.25}
            if provider_id not in {"kimi", "kimi_k27", "zhipu_glm52"}
            else {}
        ),
        **({"do_sample": False} if provider_id == "zhipu_glm52" else {}),
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    request_timeout = timeout_seconds
    if request_timeout is None:
        request_timeout = _bounded_float_env(
            "AI_READ_TIMEOUT_SECONDS",
            # Complex MicroPythonOS apps can spend several minutes in the
            # provider before the first streamed token arrives.  This is an
            # idle-read timeout (not a whole-generation deadline), so a
            # generous default avoids killing healthy GLM/Kimi requests while
            # still detecting a genuinely stalled connection.
            default=600.0,
            minimum=2.0,
            maximum=600.0,
            fallbacks=("DEEPSEEK_REQUEST_TIMEOUT_SECONDS",),
        )
    request_timeout = max(2.0, min(600.0, float(request_timeout)))
    connect_timeout = _bounded_float_env(
        "AI_CONNECT_TIMEOUT_SECONDS",
        default=5.0,
        minimum=0.1,
        maximum=120.0,
    )
    timeout = httpx.Timeout(
        request_timeout,
        connect=min(connect_timeout, request_timeout),
    )
    streaming = _enabled_env("AI_STREAM_RESPONSES", default=True)
    async with httpx.AsyncClient(timeout=timeout) as client:
        if streaming:
            stream_payload = {**payload, "stream": True}
            async with client.stream(
                "POST",
                f"{base_url}/chat/completions",
                headers=headers,
                json=stream_payload,
            ) as response:
                if response.is_error:
                    raise _ProviderHTTPStatusError(
                        response.status_code,
                        _safe_upstream_request_id(response),
                    )
                body, choice, message = await _collect_streaming_completion(
                    response,
                    default_model=model,
                )
        else:
            response = await client.post(
                f"{base_url}/chat/completions", headers=headers, json=payload
            )
            if response.is_error:
                raise _ProviderHTTPStatusError(
                    response.status_code,
                    _safe_upstream_request_id(response),
                )
            try:
                body = response.json()
                choice = body["choices"][0]
                message = choice["message"]
                if not isinstance(message, dict):
                    raise TypeError("message is not an object")
            except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                raise GenerationError("AI 生成服务没有返回可解析的生成结果") from exc

    try:
        generated = _parse_model_json(message)
    except GenerationError as exc:
        finish_reason = choice.get("finish_reason")
        if str(finish_reason or "").lower() in {
            "length",
            "max_tokens",
            "token_limit",
        }:
            exc = GenerationError(
                "AI 返回内容达到输出上限，完整 App 尚未生成完毕",
                details=exc.details,
            )
        exc.details = {
            **exc.details,
            "model": str(body.get("model") or model),
            "finish_reason": (
                str(finish_reason)[:80]
                if finish_reason is not None
                else None
            ),
            "content_type": type(message.get("content")).__name__,
            "has_reasoning_content": bool(message.get("reasoning_content")),
        }
        raise
    selected_model = str(body.get("model") or model)
    model_meta: dict[str, Any] = {"model": selected_model}
    finish_reason = choice.get("finish_reason")
    if finish_reason is not None:
        model_meta["finish_reason"] = str(finish_reason)[:80]
    request_id = body.get("id")
    if isinstance(request_id, str) and request_id:
        model_meta["request_id"] = request_id[:200]
    usage = body.get("usage")
    if isinstance(usage, dict):
        safe_usage = {
            key: int(value)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            if isinstance((value := usage.get(key)), (int, float))
            and not isinstance(value, bool)
        }
        if safe_usage:
            model_meta["usage"] = safe_usage
    return generated, selected_model, model_meta


def _validate_code(code: str) -> list[str]:
    if len(code) > 100_000:
        raise GenerationError("生成的代码过大")
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise GenerationError(f"生成的 Python 语法错误：第 {exc.lineno} 行 {exc.msg}") from exc
    required = ["import lvgl as lv", "from mpos import Activity", "class GeneratedApp"]
    missing = [item for item in required if item not in code]
    if missing:
        raise GenerationError(f"生成结果缺少必要结构：{', '.join(missing)}")
    hits: list[str] = []
    forbidden_calls = {"eval", "exec", "compile", "open"}
    blocking_while_nodes: dict[int, int] = {}

    def _extract_assigned_names(node: ast.AST) -> set[str]:
        names: set[str] = set()
        for candidate in ast.walk(node):
            if isinstance(candidate, ast.Name) and isinstance(candidate.ctx, ast.Store):
                names.add(candidate.id)
            elif isinstance(candidate, ast.AugAssign):
                for target in ast.walk(candidate.target):
                    if isinstance(target, ast.Name):
                        names.add(target.id)
            elif isinstance(candidate, (ast.Assign, ast.AnnAssign)):
                targets = (
                    candidate.targets
                    if isinstance(candidate, ast.Assign)
                    else [candidate.target]
                )
                for target in targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
                    elif isinstance(target, (ast.Tuple, ast.List)):
                        for item in ast.walk(target):
                            if isinstance(item, ast.Name):
                                names.add(item.id)
        return names

    def _contains_load_name(test: ast.AST) -> bool:
        return any(isinstance(node, ast.Name) for node in ast.walk(test))

    def _is_finite_while_in_on_create(loop: ast.While) -> bool:
        if not _contains_load_name(loop.test):
            return False
        if _contains_load_name(loop.test) and _extract_assigned_names(loop):
            test_names = {
                node.id
                for node in ast.walk(loop.test)
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
            }
            mutated_names = _extract_assigned_names(loop)
            if not (test_names & mutated_names):
                return False
            for child in ast.walk(loop):
                if isinstance(child, ast.Call):
                    if isinstance(child.func, ast.Name) and child.func.id in {
                        "sleep",
                        "sleep_ms",
                        "sleep_us",
                    }:
                        return False
                    if (
                        isinstance(child.func, ast.Attribute)
                        and child.func.attr in {"sleep", "sleep_ms", "sleep_us"}
                    ):
                        return False
            return True
        return False

    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.walk(function):
            if not isinstance(child, ast.While):
                continue
            condition_is_always_true = (
                isinstance(child.test, ast.Constant)
                and child.test.value in {True, 1}
            )
            is_blocking_on_create = (
                function.name == "onCreate" and not _is_finite_while_in_on_create(child)
            )
            if is_blocking_on_create or condition_is_always_true:
                blocking_while_nodes[id(child)] = int(getattr(child, "lineno", 0))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (
            node.module == "mpos.board" or (node.module or "").startswith("mpos.board.")
        ):
            hits.append("mpos.board.*（必须使用 Manager 能力探测）")
        elif isinstance(node, ast.ImportFrom) and node.module in {"machine", "neopixel"}:
            hits.append(f"{node.module}（必须使用 mpos Manager API）")
        elif isinstance(node, ast.Import) and any(
            alias.name == "machine" or alias.name.startswith("mpos.board")
            for alias in node.names
        ):
            hits.append("直接硬件模块（必须使用 mpos Manager API）")
        if isinstance(node, ast.Call):
            dotted_call = _dotted_name(node.func) if isinstance(node.func, ast.Attribute) else None
            if dotted_call and any(
                dotted_call.endswith(f".{name}")
                for name in ("Pin", "I2C", "SPI", "UART", "I2S", "ADC", "NeoPixel")
            ):
                hits.append(f"{dotted_call}（禁止直接访问硬件）")
            if isinstance(node.func, ast.Name) and node.func.id in forbidden_calls:
                hits.append(node.func.id)
            elif (
                isinstance(node.func, ast.Name)
                and node.func.id in {"sleep", "sleep_ms", "sleep_us"}
            ):
                hits.append(f"{node.func.id}（会阻塞 WASM）")
            elif (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "os"
                and node.func.attr == "system"
            ):
                hits.append("os.system")
            elif (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in {"sleep", "sleep_ms", "sleep_us"}
            ):
                hits.append(f"{node.func.attr}（会阻塞 WASM）")
            elif (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "align"
                and len(node.args) == 4
            ):
                hits.append(
                    "align(base, align, x, y)（请改用 align_to(base, align, x, y)）"
                )
            elif (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "set_repeat_count"
                and len(node.args) == 1
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == 0
            ):
                hits.append(
                    "set_repeat_count(0)（一次性定时器请使用 set_repeat_count(1)）"
                )
        elif isinstance(node, ast.While) and id(node) in blocking_while_nodes:
            line_number = blocking_while_nodes[id(node)]
            hits.append(
                f"第 {line_number} 行阻塞式 while 循环"
                "（有限计算循环可以保留；界面更新请使用 lv.timer_create）"
            )
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = [alias.name for alias in node.names]
            if any(name == "subprocess" for name in modules):
                hits.append("subprocess")
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "lv"
            and (
                node.attr in {"btn", "scr_act", "event_code"}
                or node.attr.startswith("font_")
                or node.attr.startswith("EVENT_")
            )
        ):
            hits.append(f"lv.{node.attr}（当前 Web LVGL 不支持）")
        elif isinstance(node, ast.Attribute) and node.attr == "set_text_align":
            hits.append("set_text_align（请使用 label.align 定位）")
        elif isinstance(node, ast.Attribute) and node.attr == "_del":
            hits.append("_del（定时器请使用公开方法 timer.delete()）")
        elif (
            isinstance(node, ast.Attribute)
            and node.attr
            in {
                "get_pos",
                "get_coords",
                "get_x",
                "get_y",
                "get_child_cnt",
                "get_child_by_type",
            }
        ):
            hits.append(
                f"{node.attr}（控件状态和控件引用必须保存在 Python 数字或列表中）"
            )
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and re.search(r"[\u3400-\u9fff]", node.value)
        ):
            hits.append("中文控件文字（Web 默认字体会显示方框，请改用英文）")
    hits = sorted(set(hits))
    if hits:
        raise GenerationError(f"生成代码包含当前版本不允许的能力：{', '.join(hits)}")
    self_tests = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "self_test"
    ]
    if not self_tests:
        raise GenerationError("生成代码缺少通用运行验收方法 self_test(self)")
    self_test = self_tests[0]
    if not any(isinstance(node, ast.Call) for node in ast.walk(self_test)):
        raise GenerationError("self_test 必须调用 App 的真实功能方法")
    if not any(isinstance(node, ast.Compare) for node in ast.walk(self_test)):
        raise GenerationError("self_test 必须比较操作前后的真实状态")
    if not any(
        isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)
        for node in ast.walk(self_test)
    ):
        raise GenerationError("self_test 必须返回包含布尔验收结果的 dict")
    return ["已通过 Python 语法检查和基础安全检查"]


def _is_shooter_prompt(prompt: str) -> bool:
    normalized = prompt.casefold()
    return any(
        term in normalized
        for term in ("射击", "打飞机", "枪战", "shooting", "shooter", "space invader")
    )


def _validate_interaction_contract(code: str, prompt: str) -> list[str]:
    if not _is_shooter_prompt(prompt):
        return []
    tree = ast.parse(code)
    visible_text = " ".join(
        node.value.upper()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )
    attribute_names = [
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    ]
    missing: list[str] = []
    for control in ("LEFT", "RIGHT", "FIRE"):
        if control not in visible_text:
            missing.append(f"屏幕按钮 {control}")
    if "add_event_cb" not in attribute_names:
        missing.append("按钮的 add_event_cb 交互")
    if "timer_create" not in attribute_names:
        missing.append("lv.timer_create 游戏更新")
    if not any(name in attribute_names for name in ("set_pos", "set_x")):
        missing.append("玩家或子弹的位置更新")
    lowered = code.casefold()
    if "bullet" not in lowered and "projectile" not in lowered:
        missing.append("子弹状态与移动逻辑")
    if "enemy" not in lowered and "enemies" not in lowered:
        missing.append("敌人状态与移动逻辑")
    if "score" not in lowered:
        missing.append("命中计分逻辑")
    if not any(term in lowered for term in ("collision", "hit_enemy", "check_hit")):
        missing.append("子弹与敌人的碰撞判定")
    if missing:
        raise GenerationError(
            "射击游戏缺少可实际操作的功能：" + "、".join(missing)
        )
    return ["已通过射击游戏 LEFT / RIGHT / FIRE 交互检查"]


def _validate_product_contract(code: str, prompt: str) -> list[str]:
    """Reject styled code that does not implement the requested product."""

    lowered_prompt = prompt.casefold()
    lowered_code = code.casefold()
    tree = ast.parse(code)
    function_names = {
        node.name.casefold()
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    event_binding_count = sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_event_cb"
    )
    missing: list[str] = []
    if any(token in lowered_prompt for token in ("日历", "calendar")):
        if "month" not in lowered_code:
            missing.append("月份状态和切换逻辑")
        if not any(
            token in lowered_code
            for token in (
                "day_buttons",
                "date_buttons",
                "month_days",
                "days_in_month",
                "range(1, 32)",
                "range(1, 43)",
            )
        ):
            missing.append("日期按钮集合")
        if event_binding_count < 1:
            missing.append("上月/下月或返回今天的真实交互")
    if any(token in lowered_prompt for token in ("计算器", "calculator")):
        calculator_function_tokens = (
            "calculate",
            "compute",
            "equals",
            "equal",
            "evaluate",
            "apply_operator",
            "perform_operation",
            "execute_operation",
            "handle_operator",
        )
        has_named_logic = any(
            any(token in name for token in calculator_function_tokens)
            for name in function_names
        )
        has_ast_arithmetic = any(
            isinstance(node, ast.BinOp)
            and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div))
            for node in ast.walk(tree)
        )
        has_state_machine = (
            any(token in lowered_code for token in ("current_operator", "pending_operator", "operator"))
            and any(token in lowered_code for token in ("result", "display", "operand"))
        )
        if not (has_named_logic or has_ast_arithmetic or has_state_machine):
            missing.append("计算执行逻辑")
        if not all(token in code for token in ("+", "-", "*", "/")):
            missing.append("四则运算符")
        # A single add_event_cb inside a button factory/loop can bind every key.
        if event_binding_count < 1:
            missing.append("数字和运算按钮交互")
    if any(token in lowered_prompt for token in ("番茄", "pomodoro", "计时器", "timer")):
        if "lv.timer_create" not in code:
            missing.append("非阻塞计时器")
        if event_binding_count < 1:
            missing.append("开始/暂停/重置交互")
    if missing:
        raise GenerationError(
            "生成代码虽然可显示，但没有完整实现用户要求的产品功能："
            + "、".join(missing)
        )
    return [f"产品语义验收通过：{prompt[:80]}"]


def _numeric_literal(node: ast.AST) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, (int, float))
    ):
        return -float(node.operand.value)
    return None


def _validate_visual_contract(code: str, prompt: str = "") -> list[str]:
    """Reject functional-but-unstyled prototypes before they reach the user."""
    tree = ast.parse(code)
    attributes = [
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    ]
    required_groups = {
        "背景色": {"set_style_bg_color"},
        "文字颜色": {"set_style_text_color"},
        "圆角": {"set_style_radius"},
        "内边距": {
            "set_style_pad_all",
            "set_style_pad_hor",
            "set_style_pad_ver",
            "set_style_pad_top",
            "set_style_pad_bottom",
            "set_style_pad_left",
            "set_style_pad_right",
        },
    }
    missing = [
        label
        for label, methods in required_groups.items()
        if not methods.intersection(attributes)
    ]
    color_calls = 0
    color_values: set[int] = set()
    button_names: set[str] = set()
    widget_sizes: dict[str, tuple[float, float]] = {}
    widget_positions: dict[str, tuple[float, float]] = {}
    explicit_button_heights: dict[str, float] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if (
                isinstance(value, ast.Call)
                and _dotted_name(value.func) == "lv.button"
            ):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    target_name = _dotted_name(target)
                    if target_name:
                        button_names.add(target_name)
        if not isinstance(node, ast.Call):
            continue
        if _dotted_name(node.func) == "lv.color_hex":
            color_calls += 1
            if node.args:
                color_value = _numeric_literal(node.args[0])
                if color_value is not None:
                    color_values.add(int(color_value))
        if not isinstance(node.func, ast.Attribute):
            continue
        receiver = _dotted_name(node.func.value)
        if not receiver:
            continue
        if node.func.attr == "set_size" and len(node.args) >= 2:
            width = _numeric_literal(node.args[0])
            height = _numeric_literal(node.args[1])
            if width is not None and height is not None:
                widget_sizes[receiver] = (width, height)
                if receiver in button_names:
                    explicit_button_heights[receiver] = height
        elif node.func.attr == "set_height" and node.args:
            height = _numeric_literal(node.args[0])
            if height is not None and receiver in button_names:
                explicit_button_heights[receiver] = height
        elif node.func.attr == "set_pos" and len(node.args) >= 2:
            x = _numeric_literal(node.args[0])
            y = _numeric_literal(node.args[1])
            if x is not None and y is not None:
                widget_positions[receiver] = (x, y)
    if color_calls < 4:
        missing.append("至少 4 个协调的 lv.color_hex 颜色")
    if {0x0F172A, 0x1E293B, 0x6366F1}.issubset(color_values):
        missing.append("删除固定的深蓝 0x0F172A/0x1E293B/0x6366F1 模板并使用本次专属调色板")
    if attributes.count("set_style_bg_color") < 3:
        missing.append("screen、内容卡片与主要操作的三层背景")
    if attributes.count("set_style_radius") < 2:
        missing.append("至少两个层级的统一圆角")
    padding_calls = sum(
        attributes.count(name)
        for name in (
            "set_style_pad_all",
            "set_style_pad_hor",
            "set_style_pad_ver",
            "set_style_pad_top",
            "set_style_pad_bottom",
            "set_style_pad_left",
            "set_style_pad_right",
        )
    )
    if padding_calls < 1:
        missing.append("主要内容卡片的明确内边距")
    layout_calls = sum(
        attributes.count(name)
        for name in (
            "set_size",
            "set_width",
            "set_height",
            "set_pos",
            "set_x",
            "set_y",
            "align",
            "center",
            "set_flex_flow",
            "set_flex_align",
        )
    )
    if layout_calls < 3:
        missing.append("至少三个明确的尺寸或布局设置")
    if not any(
        name in attributes
        for name in (
            "set_style_border_width",
            "set_style_border_color",
            "set_style_shadow_width",
        )
    ):
        missing.append("卡片细边框或轻阴影")
    undersized_buttons = sorted(
        name
        for name, height in explicit_button_heights.items()
        if height < 34
    )
    if undersized_buttons:
        missing.append(
            "触控按钮高度不得小于 34px：" + ", ".join(undersized_buttons[:5])
        )
    if button_names and not explicit_button_heights:
        missing.append("为主要触控按钮明确设置至少 34px 高度")
    overflow_widgets: list[str] = []
    for name, (x, y) in widget_positions.items():
        size = widget_sizes.get(name)
        if size is None:
            continue
        width, height = size
        if x < 0 or y < 0 or x + width > 320 or y + height > 240:
            overflow_widgets.append(name)
    if overflow_widgets:
        missing.append(
            "以下控件的明确坐标超出 320x240 安全区域："
            + ", ".join(sorted(overflow_widgets)[:5])
        )
    if missing:
        raise GenerationError(
            "界面仍像未设计的原型，必须补齐：" + "、".join(dict.fromkeys(missing))
        )
    archetype = _app_archetype_for_prompt(prompt) if prompt else "generic"
    return [
        "已通过三层背景、专属配色、圆角、间距、触控尺寸、屏幕边界和卡片细节检查"
        f"（{archetype}）"
    ]


@lru_cache(maxsize=1)
def _api_indexes() -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[2]
    reference_root = (
        repo_root
        / "vendor"
        / "MicroPython_Skills"
        / "mpos-dev"
        / "reference"
    )
    try:
        lvgl_summary = json.loads(
            (reference_root / "lvgl_api_summary.json").read_text(encoding="utf-8")
        )
        mpos_summary = json.loads(
            (reference_root / "mpos_api_summary.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ApiValidationError(
            "TOOLCHAIN_MISSING", "无法完整读取 MicroPythonOS API summary"
        ) from exc

    widget_records = {
        item["name"]: item for item in lvgl_summary.get("widgets", [])
    }
    data_class_records = {
        item["name"]: item
        for item in lvgl_summary.get("data_classes", [])
        if isinstance(item, dict) and item.get("name")
    }

    def inherited_methods(name: str, seen: set[str] | None = None) -> set[str]:
        if seen is None:
            seen = set()
        if name in seen or name not in widget_records:
            return set()
        seen.add(name)
        item = widget_records[name]
        methods = {method["name"] for method in item.get("methods", [])}
        parent = item.get("parent")
        if isinstance(parent, str):
            methods.update(inherited_methods(parent, seen))
        return methods

    widgets = {name: inherited_methods(name) for name in widget_records}
    object_methods = widgets.get("obj", set())
    for name in widgets:
        if name != "obj":
            widgets[name].update(object_methods)
    data_classes = {
        name: {
            method["name"]
            for method in item.get("methods", [])
            if isinstance(method, dict) and method.get("name")
        }
        for name, item in data_class_records.items()
    }
    object_types = {**widgets, **data_classes}
    functions = {
        item["name"]
        for item in lvgl_summary.get("functions", [])
        if isinstance(item, dict) and item.get("name")
    }
    function_returns = {
        item["name"]: str(item.get("returns") or "").strip().strip("\"'")
        for item in lvgl_summary.get("functions", [])
        if isinstance(item, dict) and item.get("name")
    }
    enums = {
        item["name"]
        for item in lvgl_summary.get("enums", [])
        if isinstance(item, dict) and item.get("name")
    }
    exports = {
        item["name"]
        for item in mpos_summary.get("root_exports", {}).get("exports", [])
        if isinstance(item, dict) and item.get("name")
    }
    return {
        "widgets": widgets,
        "object_types": object_types,
        "functions": functions,
        "function_returns": function_returns,
        "enums": enums,
        "mpos_exports": exports,
        "lvgl_generated_at": lvgl_summary.get("generated_at"),
        "mpos_generated_at": mpos_summary.get("generated_at"),
    }


def _dotted_name(node: ast.AST) -> str | None:
    parts: list[str] = []
    current: ast.AST | None = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return None


def _validate_api_summaries(code: str) -> tuple[list[str], dict[str, Any]]:
    indexes = _api_indexes()
    tree = ast.parse(code)
    object_types: dict[str, str] = {}
    planned: set[str] = set()
    missing: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "mpos":
            for alias in node.names:
                planned.add(f"mpos.{alias.name}")
                if alias.name not in indexes["mpos_exports"]:
                    missing.add(f"mpos.{alias.name}")
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            inferred_type: str | None = None
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Attribute)
                and isinstance(value.func.value, ast.Name)
                and value.func.value.id == "lv"
            ):
                if value.func.attr in indexes["object_types"]:
                    inferred_type = value.func.attr
                else:
                    returned_type = indexes["function_returns"].get(value.func.attr)
                    if returned_type in indexes["object_types"]:
                        inferred_type = returned_type
            if inferred_type:
                for target in targets:
                    name = _dotted_name(target)
                    if name:
                        object_types[name] = inferred_type

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        dotted = _dotted_name(node.func)
        if not dotted:
            continue
        if dotted.startswith("lv."):
            parts = dotted.split(".")
            if len(parts) == 2:
                symbol = parts[1]
                planned.add(dotted)
                if (
                    symbol not in indexes["widgets"]
                    and symbol not in indexes["object_types"]
                    and symbol not in indexes["functions"]
                    and symbol not in indexes["enums"]
                ):
                    missing.add(dotted)
            continue
        owner_name = dotted.rsplit(".", 1)[0]
        method_name = dotted.rsplit(".", 1)[1]
        widget_name = object_types.get(owner_name)
        if widget_name:
            symbol = f"lv.{widget_name}.{method_name}"
            planned.add(symbol)
            if method_name not in indexes["object_types"][widget_name]:
                missing.add(symbol)

    if missing:
        raise ApiValidationError(
            "LVGL_API_MISSING",
            "以下调用不在当前 API summary 中：" + ", ".join(sorted(missing)),
        )
    metadata = {
        "checked": True,
        "planned": sorted(planned),
        "missing": [],
        "lvgl_summary_generated_at": indexes["lvgl_generated_at"],
        "mpos_summary_generated_at": indexes["mpos_generated_at"],
    }
    return ["已完整读取并交叉校验 LVGL / MPOS API summary"], metadata


def _normalize_lvgl_code(code: str) -> tuple[str, list[str]]:
    replacements = {
        "lv.event_code.": "lv.EVENT.",
        "lv.EVENT_CLICKED": "lv.EVENT.CLICKED",
        "lv.EVENT_VALUE_CHANGED": "lv.EVENT.VALUE_CHANGED",
        "lv.EVENT_PRESSED": "lv.EVENT.PRESSED",
        "lv.EVENT_RELEASED": "lv.EVENT.RELEASED",
        # LVGL 9 renamed clear_flag() to remove_flag().  Models commonly emit
        # the LVGL 8 spelling even when every other call targets the current
        # MicroPythonOS binding.  This is a mechanical compatibility fix, not
        # a reason to discard an otherwise valid generated application.
        ".clear_flag(": ".remove_flag(",
        # LVGL 9 uses remove_state(); clear_state() is an LVGL 8 spelling and
        # raises AttributeError in the WebAssembly binding.
        ".clear_state(": ".remove_state(",
        # Some providers copy the C enum name (lv_obj_flag_t) into a Pythonic
        # looking ``lv.obj_flag`` namespace.  The MicroPythonOS LVGL binding
        # exposes these values through ``lv.obj.FLAG`` instead.  Static API
        # summaries can miss this because ``remove_flag`` itself is valid, so
        # normalize the enum namespace before validation and WASM execution.
        "lv.obj_flag.": "lv.obj.FLAG.",
    }
    normalized = code
    applied: list[str] = []
    for old, new in replacements.items():
        if old in normalized:
            normalized = normalized.replace(old, new)
            applied.append(f"已自动兼容 {old} → {new}")

    # MicroPython intentionally ships a compact ``random`` module without
    # CPython's random.shuffle().  Replace the common call mechanically with a
    # tiny in-place Fisher-Yates helper so a valid game does not need another
    # slow and stochastic model round trip just for this runtime difference.
    if "random.shuffle(" in normalized:
        normalized = normalized.replace("random.shuffle(", "_mpos_shuffle(")
        if "def _mpos_shuffle(" not in normalized:
            helper = (
                "\n\ndef _mpos_shuffle(items):\n"
                "    for _mpos_i in range(len(items) - 1, 0, -1):\n"
                "        _mpos_j = random.getrandbits(16) % (_mpos_i + 1)\n"
                "        items[_mpos_i], items[_mpos_j] = items[_mpos_j], items[_mpos_i]\n"
                "\n"
            )
            class_marker = re.search(r"(?m)^class\s+GeneratedApp\b", normalized)
            insertion = class_marker.start() if class_marker else len(normalized)
            normalized = normalized[:insertion].rstrip() + helper + normalized[insertion:]
        applied.append(
            "已将 MicroPython 不支持的 random.shuffle 转换为兼容的 Fisher-Yates 洗牌"
        )
    normalized, flex_flow_count = re.subn(
        r"(\.set_flex_flow\(\s*[^,\n]+?)\s*,\s*0\s*\)",
        r"\1)",
        normalized,
    )
    if flex_flow_count:
        applied.append("已移除 set_flex_flow 不支持的 selector 参数")
    normalized, legacy_child_count = re.subn(
        r"\.get_child_cnt\(\s*\)",
        ".get_child_count()",
        normalized,
    )
    if legacy_child_count:
        applied.append("已将旧式 get_child_cnt() 转换为 get_child_count()")
    try:
        timer_tree = ast.parse(normalized)
    except SyntaxError:
        timer_tree = None
    timer_replacements: list[tuple[int, int, bytes]] = []
    if timer_tree is not None:
        encoded = normalized.encode("utf-8")
        line_offsets: list[int] = []
        offset = 0
        for line in normalized.splitlines(keepends=True):
            line_offsets.append(offset)
            offset += len(line.encode("utf-8"))
        for node in ast.walk(timer_tree):
            if (
                not isinstance(node, ast.Call)
                or not isinstance(node.func, ast.Attribute)
                or not isinstance(node.func.value, ast.Name)
                or node.func.value.id != "lv"
                or node.func.attr != "timer_del"
                or len(node.args) != 1
                or node.keywords
                or node.end_lineno is None
                or node.end_col_offset is None
            ):
                continue
            timer_name = _dotted_name(node.args[0])
            if not timer_name:
                continue
            start = line_offsets[node.lineno - 1] + node.col_offset
            end = line_offsets[node.end_lineno - 1] + node.end_col_offset
            timer_replacements.append(
                (start, end, f"{timer_name}.delete()".encode("utf-8"))
            )
        for start, end, replacement in sorted(timer_replacements, reverse=True):
            encoded = encoded[:start] + replacement + encoded[end:]
        normalized = encoded.decode("utf-8")
    if timer_replacements:
        applied.append("已将旧式 lv.timer_del(timer) 转换为 timer.delete()")
    normalized, legacy_align_count = re.subn(
        r"(?P<target>[A-Za-z_][\w.]*)\.align\(\s*"
        r"(?P<base>[A-Za-z_][\w.]*)\s*,\s*"
        r"(?P<align>lv\.ALIGN\.[A-Z0-9_]+)\s*,\s*"
        r"(?P<x>[^,\n()]+)\s*,\s*(?P<y>[^,\n()]+)\s*\)",
        r"\g<target>.align_to(\g<base>, \g<align>, \g<x>, \g<y>)",
        normalized,
    )
    if legacy_align_count:
        applied.append(
            "已将旧式 align(base, align, x, y) 转换为 align_to(base, align, x, y)"
        )

    # Providers occasionally ignore the explicit 34px touch-target rule by a
    # few pixels.  That is deterministic geometry, so correct literal button
    # heights locally instead of spending another slow model round trip on an
    # otherwise valid application.  Dynamic sizes remain untouched and still
    # have to satisfy the visual validator.
    try:
        button_tree = ast.parse(normalized)
    except SyntaxError:
        button_tree = None
    button_height_replacements: list[tuple[int, int, bytes]] = []
    if button_tree is not None:
        button_names: set[str] = set()
        for node in ast.walk(button_tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if not (
                isinstance(value, ast.Call)
                and _dotted_name(value.func) == "lv.button"
            ):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                target_name = _dotted_name(target)
                if target_name:
                    button_names.add(target_name)

        encoded = normalized.encode("utf-8")
        line_offsets: list[int] = []
        offset = 0
        for line in normalized.splitlines(keepends=True):
            line_offsets.append(offset)
            offset += len(line.encode("utf-8"))

        def replace_button_height(node: ast.AST) -> None:
            if node.end_lineno is None or node.end_col_offset is None:
                return
            start = line_offsets[node.lineno - 1] + node.col_offset
            end = line_offsets[node.end_lineno - 1] + node.end_col_offset
            button_height_replacements.append((start, end, b"34"))

        for node in ast.walk(button_tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            receiver = _dotted_name(node.func.value)
            if receiver not in button_names:
                continue
            if node.func.attr == "set_size" and len(node.args) >= 2:
                height_node = node.args[1]
            elif node.func.attr == "set_height" and node.args:
                height_node = node.args[0]
            else:
                continue
            height = _numeric_literal(height_node)
            if height is not None and height < 34:
                replace_button_height(height_node)

        for start, end, replacement in sorted(
            button_height_replacements, reverse=True
        ):
            encoded = encoded[:start] + replacement + encoded[end:]
        normalized = encoded.decode("utf-8")
    if button_height_replacements:
        applied.append("已自动将过小的固定触控按钮高度提升到 34px")

    # Clamp only literal set_pos(x, y) calls for widgets whose literal size is
    # known.  A model can produce a polished, functional app with one object a
    # few pixels outside the 320x240 preview.  Sending the entire app through
    # another slow model round-trip for that arithmetic slip made generation
    # stochastic and needlessly expensive.  Dynamic/game coordinates remain
    # untouched and still go through the deterministic visual validator.
    try:
        position_tree = ast.parse(normalized)
    except SyntaxError:
        position_tree = None
    position_replacements: list[tuple[int, int, bytes]] = []
    if position_tree is not None:
        widget_sizes: dict[str, tuple[float, float]] = {}
        for node in ast.walk(position_tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "set_size"
                and len(node.args) >= 2
            ):
                receiver = _dotted_name(node.func.value)
                width = _numeric_literal(node.args[0])
                height = _numeric_literal(node.args[1])
                if receiver and width is not None and height is not None:
                    widget_sizes[receiver] = (width, height)

        encoded = normalized.encode("utf-8")
        line_offsets: list[int] = []
        offset = 0
        for line in normalized.splitlines(keepends=True):
            line_offsets.append(offset)
            offset += len(line.encode("utf-8"))

        def literal_replacement(
            node: ast.AST, value: float
        ) -> tuple[int, int, bytes] | None:
            if node.end_lineno is None or node.end_col_offset is None:
                return None
            start = line_offsets[node.lineno - 1] + node.col_offset
            end = line_offsets[node.end_lineno - 1] + node.end_col_offset
            text = str(int(value)) if float(value).is_integer() else repr(value)
            return start, end, text.encode("utf-8")

        for node in ast.walk(position_tree):
            if (
                not isinstance(node, ast.Call)
                or not isinstance(node.func, ast.Attribute)
                or node.func.attr != "set_pos"
                or len(node.args) < 2
            ):
                continue
            receiver = _dotted_name(node.func.value)
            size = widget_sizes.get(receiver or "")
            x = _numeric_literal(node.args[0])
            y = _numeric_literal(node.args[1])
            if size is None or x is None or y is None:
                continue
            width, height = size
            if width > 320 or height > 240:
                continue
            clamped_x = min(max(x, 0), 320 - width)
            clamped_y = min(max(y, 0), 240 - height)
            if clamped_x != x:
                replacement = literal_replacement(node.args[0], clamped_x)
                if replacement is not None:
                    position_replacements.append(replacement)
            if clamped_y != y:
                replacement = literal_replacement(node.args[1], clamped_y)
                if replacement is not None:
                    position_replacements.append(replacement)
        for start, end, replacement in sorted(position_replacements, reverse=True):
            encoded = encoded[:start] + replacement + encoded[end:]
        normalized = encoded.decode("utf-8")
    if position_replacements:
        applied.append("已自动将超出 320x240 预览区域的固定控件坐标移回屏幕内")
    font_free_lines: list[str] = []
    for line in normalized.splitlines():
        if "set_style_text_font" in line or "lv.font_" in line:
            indentation = line[: len(line) - len(line.lstrip())]
            font_free_lines.append(
                f"{indentation}pass  # Web preview uses the inherited system font"
            )
            applied.append("已移除当前 Web 构建不支持的自定义字体设置")
        else:
            font_free_lines.append(line)
    normalized = "\n".join(font_free_lines)
    return normalized, applied


def _build_correction(
    error: GenerationError, candidate: str = "", attempt: int | None = None
) -> str:
    message = str(error)
    suggestions: list[str] = []
    if "get_pos" in message or "get_coords" in message:
        suggestions.append(
            "不要从 LVGL 控件读取坐标。把坐标保存在 self.player_x / "
            "self.player_y 等整数中，移动时先更新整数，再调用 widget.set_pos(x, y)。"
            "最终 app_code 文本中 get_pos 和 get_coords 的出现次数必须为 0。"
        )
    if "get_x" in message or "get_y" in message:
        suggestions.append(
            "不要调用控件 get_x/get_y；使用 Python 数字状态作为唯一位置来源。"
            "最终 app_code 文本中 get_x 和 get_y 的出现次数必须为 0。"
        )
    if re.search(r"\b(?:get_child_cnt|get_child_by_type)\b", message):
        suggestions.append(
            "不要使用旧式 get_child_cnt 或不稳定的 get_child_by_type。"
            "创建控件时优先把引用加入 self.day_buttons，需要数量时使用 "
            "len(self.day_buttons) 或当前绑定支持的 get_child_count()。"
        )
    if "timer_del" in message or "._del" in message:
        suggestions.append(
            "当前绑定删除周期定时器必须使用保存下来的 timer 引用调用 timer.delete()；"
            "不要使用 lv.timer_del(timer) 或 timer._del()。删除后把引用设为 None，避免重复删除。"
        )
    if "set_repeat_count(0)" in message:
        suggestions.append(
            "不要使用 set_repeat_count(0)，它会自动删除定时器并可能导致后续重复释放。"
            "一次性定时器使用 set_repeat_count(1)，且不要再手工删除。"
        )
    if "set_text_align" in message:
        suggestions.append("删除 set_text_align，使用 label.align(...) 摆放标签。")
    if "random.shuffle" in message or "has no attribute 'shuffle'" in message:
        suggestions.append(
            "当前 MicroPython random 模块没有 shuffle。使用 random.getrandbits 实现 "
            "Fisher-Yates 原地洗牌，不得再次调用 random.shuffle。"
        )
    if "clear_state" in message or "has no attribute 'clear_state'" in message:
        suggestions.append(
            "当前 LVGL 绑定使用 widget.remove_state(state)，不得调用 clear_state。"
        )
    if "align(base, align, x, y)" in message:
        suggestions.append(
            "当前绑定中 widget.align 只能传 align、x、y 三个参数。"
            "相对另一个控件定位必须改为 "
            "widget.align_to(base, lv.ALIGN.CENTER, x, y)。"
        )
    if "阻塞式 while" in message:
        suggestions.append(
            "删除 while True、持续刷新界面的主循环。"
            "动画和倒计时改用 lv.timer_create。若是计算器解析表达式，"
            "可以保留有限 while，但 onCreate 内只能用于短暂一次性计算，不允许界面持续刷新。"
        )
    if "界面仍像未设计的原型" in message:
        suggestions.append(
            "不要只在失败代码上补几行样式；按照本次需求中的专属 App 成品骨架完整重做视图层级："
            "screen、内容卡片和主要按钮分别设置背景；"
            "至少两处圆角、内容卡片的一处明确内边距、三个明确尺寸/布局设置，"
            "并增加细边框或轻阴影。"
            "颜色统一使用 4-6 个协调的 lv.color_hex。"
        )
    if "固定的深蓝" in message:
        suggestions.append(
            "删除 0x0F172A、0x1E293B、0x6366F1 这一整套旧模板色，"
            "严格改用用户提示中给出的本次专属调色板，并保持 4-6 种颜色。"
        )
    if "34px" in message:
        suggestions.append(
            "所有可点击主控件必须显式 set_size 或 set_height，触控高度不小于 34px；"
            "同组按钮保持等高、等宽、等间距。"
        )
    if "320x240" in message:
        suggestions.append(
            "重新计算固定坐标和尺寸，确保 set_pos(x, y) 与 set_size(w, h) 满足 "
            "0<=x、0<=y、x+w<=320、y+h<=240；四周保留至少 8px 安全边距。"
        )
    if "API summary" in message:
        suggestions.append(
            "只能使用系统提示列出的稳定 API；不要用你记忆中的普通 LVGL API 猜测方法名。"
        )
    context = ""
    if candidate:
        context = (
            "<FAILED_CANDIDATE_REFERENCE>\n"
            "这是刚才未通过检查的 app.py，仅供定位问题，不是正确示例：\n"
            f"{candidate[:24_000]}\n"
            "</FAILED_CANDIDATE_REFERENCE>\n\n"
        )
    attempt_text = f"第 {attempt} 次生成" if attempt is not None else "本轮生成"
    correction_lines = "\n".join([message, *suggestions])
    return (
        f"{context}<FINAL_CORRECTION>\n"
        f"{attempt_text}被安全检查拒绝。请完整重写代码并修复下面的问题，"
        "不要解释，只返回新的 JSON。\n"
        "输出前必须逐字检查 app_code：不得仍然包含被指出的调用；"
        "不得为了通过检查而删除用户要求的功能；不得用 pass 或假按钮代替交互。\n"
        f"检查失败原因与强制修复规则：\n{correction_lines}\n"
        "返回完整、可解析且已经修复的 app_code。\n"
        "</FINAL_CORRECTION>"
    )


def _safe_attempt_message(error: GenerationError) -> str:
    message = str(error)[:2000]
    for pattern in (
        r"Bearer\s+[A-Za-z0-9._~+/=-]+",
        r"\b(?:sk|sbp)_[A-Za-z0-9_-]+\b",
        r"(?i)(authorization|cookie|api[-_ ]?key)\s*[:=]\s*\S+",
    ):
        message = re.sub(pattern, "[REDACTED]", message)
    return message


def _emit_generation_attempt(
    sink: GenerationAttemptSink | None,
    *,
    attempt: int,
    status: str,
    candidate: str = "",
    error: GenerationError | None = None,
    model_meta: dict[str, Any] | None = None,
) -> None:
    if sink is None:
        return
    validation: dict[str, Any] = {
        "status": "passed" if status == "passed" else "failed"
    }
    if error is not None:
        message = _safe_attempt_message(error)
        code = getattr(error, "code", "GENERATION_VALIDATION_FAILED")
        if model_meta is None and isinstance(error, UpstreamGenerationError):
            model_meta = error.details
        line_match = re.search(r"第\s*(\d+)\s*行", message)
        validation.update(
            {
                "code": code,
                "message": message,
                "line": int(line_match.group(1)) if line_match else None,
            }
        )
    record: dict[str, Any] = {
        "attempt": attempt,
        "status": status,
        "validation": validation,
        "model_meta": model_meta or {},
    }
    if candidate:
        record["candidate"] = candidate
    try:
        sink(record)
    except Exception:
        # Private diagnostics are best-effort and must not mask generation results.
        pass


def _manifest(request: GenerateRequest) -> dict[str, Any]:
    return {
        "name": request.display_name,
        "publisher": request.publisher,
        "short_description": request.prompt[:100],
        "long_description": request.prompt,
        "fullname": request.package_name,
        "version": request.version,
        "category": "generated",
        "activities": [
            {
                "entrypoint": "app.py",
                "classname": "GeneratedApp",
                "intent_filters": [{"action": "main", "category": "launcher"}],
            }
        ],
    }


def _png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
    return (
        struct.pack(">I", len(payload))
        + chunk_type
        + payload
        + struct.pack(">I", checksum)
    )


def _default_icon_png(package_name: str) -> bytes:
    """Create a deterministic 64x64 RGBA icon without external dependencies."""
    seed = zlib.crc32(package_name.encode("utf-8")) & 0xFFFFFFFF
    start = (52 + (seed & 63), 62 + ((seed >> 8) & 63), 180 + ((seed >> 16) & 55))
    end = (92 + ((seed >> 6) & 63), 42 + ((seed >> 14) & 63), 190 + ((seed >> 22) & 55))
    raw = bytearray()
    tiles = ((13, 13), (37, 13), (13, 37), (37, 37))
    for y in range(64):
        raw.append(0)
        for x in range(64):
            ratio = (x + y) / 126
            red = round(start[0] * (1 - ratio) + end[0] * ratio)
            green = round(start[1] * (1 - ratio) + end[1] * ratio)
            blue = round(start[2] * (1 - ratio) + end[2] * ratio)
            if any(left <= x < left + 14 and top <= y < top + 14 for left, top in tiles):
                red, green, blue = 245, 247, 255
            raw.extend((red, green, blue, 255))
    header = struct.pack(">IIBBBBB", 64, 64, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _png_chunk(b"IEND", b"")
    )


def _build_mpk(package_name: str, manifest: dict[str, Any], app_code: str) -> str:
    stream = io.BytesIO()
    manifest_bytes = json.dumps(
        manifest,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    icon_bytes = _default_icon_png(package_name)
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(f"{package_name}/", b"")
        archive.writestr(f"{package_name}/assets/", b"")
        archive.writestr(f"{package_name}/META-INF/", b"")
        archive.writestr(f"{package_name}/res/", b"")
        archive.writestr(f"{package_name}/res/mipmap-mdpi/", b"")
        archive.writestr(f"{package_name}/MANIFEST.JSON", manifest_bytes)
        archive.writestr(f"{package_name}/META-INF/MANIFEST.JSON", manifest_bytes)
        archive.writestr(f"{package_name}/icon_64x64.png", icon_bytes)
        archive.writestr(
            f"{package_name}/res/mipmap-mdpi/icon_64x64.png",
            icon_bytes,
        )
        archive.writestr(f"{package_name}/assets/main.py", app_code)
    return base64.b64encode(stream.getvalue()).decode("ascii")


AI_PROVIDER_IDS = (
    "deepseek",
    "kimi",
    "kimi_k27",
    "zhipu_glm52",
)

LEGACY_PROVIDER_ALIASES = {
    "deepseek_primary": "deepseek",
    "deepseek_secondary": "deepseek",
    "aigocode": "zhipu_glm52",
    "zhipu_glm45": "zhipu_glm52",
    "zhipu_glm47": "zhipu_glm52",
}

DEFAULT_PROVIDER_ORDERS = {
    # Keep short single-purpose apps fast. General, multi-feature, revision,
    # and repair work uses GLM-5.2 first because those tasks benefit much more
    # from requirement retention than from shaving a few seconds off latency.
    # K2.7 remains available for explicit diagnostics but is excluded from
    # automatic routing because it does not accept instant mode and has
    # repeatedly consumed the whole time budget.
    "simple": ("kimi", "zhipu_glm52", "deepseek"),
    "standard": ("zhipu_glm52", "kimi", "deepseek"),
    "complex": ("zhipu_glm52", "kimi", "deepseek"),
    "revision": ("zhipu_glm52", "kimi", "deepseek"),
    "repair": ("zhipu_glm52", "kimi", "deepseek"),
}


class _ProviderHTTPStatusError(Exception):
    """HTTP failure stripped of response body, URL, and headers."""

    def __init__(self, status_code: int, request_id: str | None = None) -> None:
        self.status_code = status_code
        self.request_id = request_id
        message = f"AI generation service returned HTTP {status_code}"
        if request_id:
            message += f" (request_id={request_id})"
        super().__init__(message)


def _safe_upstream_request_id(response: httpx.Response) -> str | None:
    for header_name in ("x-request-id", "request-id"):
        value = response.headers.get(header_name, "").strip()
        if value and re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", value):
            return value
    return None


@dataclass(frozen=True)
class AIProviderConfig:
    id: str
    label: str
    api_key: str
    base_url: str
    model: str

    @property
    def configured(self) -> bool:
        return bool(
            self.api_key
            and self.api_key
            not in {
                "replace_with_your_deepseek_api_key",
                "replace_with_your_aigocode_api_key",
                "replace_with_your_kimi_api_key",
                "replace_with_your_zhipu_api_key",
            }
        )


_ACTIVE_PROVIDER_CONFIG: ContextVar[AIProviderConfig | None] = ContextVar(
    "active_ai_provider_config",
    default=None,
)
_PROVIDER_CIRCUITS: dict[str, dict[str, float]] = {}
_PROVIDER_CIRCUITS_LOCK = threading.RLock()


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


def _bounded_float_env(
    name: str,
    *,
    default: float,
    minimum: float,
    maximum: float,
    fallbacks: tuple[str, ...] = (),
) -> float:
    raw = _first_env(name, *fallbacks, default=str(default))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _optional_timeout_env(
    name: str,
    *,
    default: float = 0.0,
    maximum: float = 3600.0,
    fallbacks: tuple[str, ...] = (),
) -> float | None:
    """Return None when an aggregate timeout is disabled with zero.

    Individual HTTP requests still have their own read timeout, so disabling the
    aggregate deadline cannot leave a dead upstream connection running forever.
    It only prevents one slow provider or one validation repair from consuming
    the budget needed by later fallbacks.
    """
    raw = _first_env(name, *fallbacks, default=str(default))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = default
    if value <= 0:
        return None
    return min(maximum, max(1.0, value))


def _enabled_env(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _bounded_int_env(
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _provider_configs() -> dict[str, AIProviderConfig]:
    deepseek = AIProviderConfig(
        id="deepseek",
        label="DeepSeek · 快速代码",
        api_key=_first_env("DEEPSEEK_API_KEY", "DEEPSEEK_PRIMARY_API_KEY"),
        base_url=_first_env(
            "DEEPSEEK_BASE_URL",
            "DEEPSEEK_PRIMARY_BASE_URL",
            default="https://api.deepseek.com",
        ).rstrip("/"),
        model=_first_env(
            "DEEPSEEK_MODEL",
            "DEEPSEEK_PRIMARY_MODEL",
            default="deepseek-chat",
        ),
    )
    kimi = AIProviderConfig(
        id="kimi",
        label="Kimi K2.6 · 通用",
        api_key=_first_env("KIMI_API_KEY", "MOONSHOT_API_KEY"),
        base_url=_first_env(
            "KIMI_BASE_URL",
            "MOONSHOT_BASE_URL",
            default="https://api.moonshot.cn/v1",
        ).rstrip("/"),
        model=_first_env("KIMI_MODEL", default="kimi-k2.6"),
    )
    kimi_k27 = AIProviderConfig(
        id="kimi_k27",
        label="Kimi K2.7 Code · 复杂代码",
        api_key=_first_env("KIMI_API_KEY", "MOONSHOT_API_KEY"),
        base_url=_first_env(
            "KIMI_BASE_URL",
            "MOONSHOT_BASE_URL",
            default="https://api.moonshot.cn/v1",
        ).rstrip("/"),
        model=_first_env("KIMI_K27_MODEL", default="kimi-k2.7-code"),
    )
    zhipu_key = _first_env("ZHIPU_API_KEY", "BIGMODEL_API_KEY", "AIGOCODE_API_KEY")
    zhipu_base_url = _first_env(
        "ZHIPU_BASE_URL",
        "BIGMODEL_BASE_URL",
        default="https://open.bigmodel.cn/api/paas/v4",
    ).rstrip("/")
    zhipu_glm52 = AIProviderConfig(
        id="zhipu_glm52",
        label="智谱 GLM-5.2 · 高质量",
        api_key=zhipu_key,
        base_url=zhipu_base_url,
        model=_first_env("ZHIPU_GLM52_MODEL", "AIGOCODE_MODEL", default="glm-5.2"),
    )
    configs = (deepseek, kimi, kimi_k27, zhipu_glm52)
    return {item.id: item for item in configs}


def provider_metadata() -> list[dict[str, str | bool]]:
    configs = _provider_configs()
    configured_any = any(config.configured for config in configs.values())
    providers: list[dict[str, str | bool]] = [
        {
            "id": "auto",
            "label": "自动选择（按复杂度）",
            "configured": configured_any,
            "model": "动态路由",
        }
    ]
    providers.extend(
        {
            "id": config.id,
            "label": config.label,
            "configured": config.configured,
            "model": config.model,
        }
        for config in configs.values()
    )
    return providers


def _settings() -> tuple[str, str, str]:
    config = _ACTIVE_PROVIDER_CONFIG.get() or _provider_configs()["deepseek"]
    if not config.configured:
        raise UpstreamGenerationError(
            "AI_UPSTREAM_UNAVAILABLE",
            "AI generation service is not configured",
            retryable=False,
            failover_allowed=False,
            details={"provider": config.id},
        )
    return config.api_key, config.base_url, config.model


def _canonical_provider_id(provider_id: str) -> str:
    return LEGACY_PROVIDER_ALIASES.get(provider_id, provider_id)


def _classify_request_complexity(
    request: GenerateRequest,
    correction: str = "",
) -> tuple[str, str]:
    """Return a deterministic routing tier without sending content elsewhere."""

    # Existing code is also supplied when a user creates a normal revision.  It
    # is useful context, but it does not by itself mean that the request is an
    # error repair.  Treat only an actual validation/runtime error (or an
    # internal correction produced by the validator) as repair work.  This
    # prevents a fresh app idea from being sent through the slow repair-model
    # route merely because an older successful revision exists.
    if correction or request.runtime_error:
        return "repair", "validation or runtime error requires repair"

    # A user-requested continuation must understand both the previous program
    # and the requested delta.  Route it to the higher-quality coding models,
    # even when the new instruction itself is short.
    if request.previous_code:
        return "revision", "continuing an existing app requires change-aware generation"

    prompt = request.prompt.lower()
    complex_hints = (
        "game", "游戏", "射击", "跑酷", "动画", "animation", "multi-screen",
        "多页面", "多步骤", "multi-step", "workflow", "dashboard", "仪表盘", "network", "联网",
        "sensor", "传感器", "camera", "摄像头", "audio", "音频", "chart", "图表",
        "bluetooth", "蓝牙", "wifi", "拖拽", "drag", "碰撞", "collision",
        "五子棋", "棋盘", "双人", "关卡", "排行榜",
    )
    simple_hints = (
        "calculator", "计算器", "timer", "计时器", "倒计时", "clock", "时钟",
        "counter", "计数器", "status", "状态", "hello", "文本", "calendar", "日历",
    )
    interaction_hints = (
        "点击", "点一下", "按钮", "输入", "选择", "切换", "开始", "暂停", "重置",
        "记录", "提醒", "每隔", "定时", "下一步", "返回", "提交", "滑动", "跳跃",
        "下棋", "轮流", "获胜", "闹铃", "设置",
        "click", "button", "input", "select", "switch", "start", "pause", "reset",
        "record", "remind", "every hour", "next", "back", "submit", "swipe",
    )
    complex_matches = sum(hint in prompt for hint in complex_hints)
    simple_matches = sum(hint in prompt for hint in simple_hints)
    interaction_matches = sum(hint in prompt for hint in interaction_hints)
    score = complex_matches * 2 - min(simple_matches, 1)
    if len(prompt) >= 500:
        score += 2
    elif len(prompt) >= 220:
        score += 1
    if any(separator in prompt for separator in ("并且", "同时", "以及", " and ")):
        score += 1

    # Two or more explicit user actions/states indicate a multi-step product,
    # even when the request is short (for example: record + hourly reminder).
    if interaction_matches >= 2:
        return "complex", "multiple controls, states, or timed interactions"

    if score >= 4:
        return "complex", "interactive, hardware, or multi-feature request"
    if score <= 0 and len(prompt) < 180:
        return "simple", "short single-purpose request"
    return "standard", "general app generation request"


def _provider_order(tier: str = "standard") -> list[str]:
    defaults = DEFAULT_PROVIDER_ORDERS.get(tier, DEFAULT_PROVIDER_ORDERS["standard"])
    env_name = f"AI_PROVIDER_ORDER_{tier.upper()}"
    raw = _first_env(env_name, "AI_PROVIDER_ORDER", default=",".join(defaults))
    ordered: list[str] = []
    for provider_id in raw.split(","):
        provider_id = _canonical_provider_id(provider_id.strip())
        if provider_id in AI_PROVIDER_IDS and provider_id not in ordered:
            ordered.append(provider_id)
    return ordered or list(defaults)


def _circuit_open(provider_id: str) -> bool:
    with _PROVIDER_CIRCUITS_LOCK:
        state = _PROVIDER_CIRCUITS.get(provider_id)
        if not state:
            return False
        if state.get("half_open_in_flight", 0.0):
            return True
        open_until = state.get("open_until", 0.0)
        return open_until > time.monotonic()


def _claim_provider_slot(provider_id: str) -> bool:
    """Atomically admit a normal call or the sole half-open probe."""

    with _PROVIDER_CIRCUITS_LOCK:
        state = _PROVIDER_CIRCUITS.get(provider_id)
        if not state:
            return True
        open_until = state.get("open_until", 0.0)
        if open_until > time.monotonic():
            return False
        if open_until <= 0:
            return True
        if state.get("half_open_in_flight", 0.0):
            return False
        state["half_open_in_flight"] = 1.0
        return True


def _release_provider_probe(provider_id: str) -> None:
    with _PROVIDER_CIRCUITS_LOCK:
        state = _PROVIDER_CIRCUITS.get(provider_id)
        if state:
            state["half_open_in_flight"] = 0.0


def _record_provider_failure(provider_id: str) -> None:
    threshold = _bounded_int_env(
        "AI_PROVIDER_CIRCUIT_FAILURE_THRESHOLD",
        default=2,
        minimum=1,
        maximum=20,
    )
    cooldown = _bounded_float_env(
        "AI_PROVIDER_CIRCUIT_COOLDOWN_SECONDS",
        default=60.0,
        minimum=0.1,
        maximum=3600.0,
    )
    with _PROVIDER_CIRCUITS_LOCK:
        state = _PROVIDER_CIRCUITS.setdefault(
            provider_id,
            {
                "failures": 0.0,
                "open_until": 0.0,
                "half_open_in_flight": 0.0,
            },
        )
        state["half_open_in_flight"] = 0.0
        state["failures"] += 1.0
        if state["failures"] >= threshold:
            state["open_until"] = time.monotonic() + cooldown


def _record_provider_success(provider_id: str) -> None:
    with _PROVIDER_CIRCUITS_LOCK:
        _PROVIDER_CIRCUITS.pop(provider_id, None)


def _reset_provider_circuits() -> None:
    """Reset process-local circuit state; intended for isolated tests."""

    with _PROVIDER_CIRCUITS_LOCK:
        _PROVIDER_CIRCUITS.clear()


def _provider_candidates(
    requested_provider: str,
    request: GenerateRequest,
    correction: str = "",
) -> tuple[list[AIProviderConfig], bool, str, str]:
    configs = _provider_configs()
    if requested_provider != "auto":
        canonical_id = _canonical_provider_id(requested_provider)
        config = configs.get(canonical_id)
        if config is None:
            raise UpstreamGenerationError(
                "AI_UPSTREAM_UNAVAILABLE",
                "The requested AI generation service is unavailable",
                retryable=False,
                failover_allowed=False,
                details={"provider": requested_provider},
            )
        if not config.configured:
            raise UpstreamGenerationError(
                "AI_UPSTREAM_UNAVAILABLE",
                "AI generation service is not configured",
                retryable=False,
                failover_allowed=False,
                details={"provider": requested_provider},
            )
        if _circuit_open(canonical_id):
            raise UpstreamGenerationError(
                "AI_UPSTREAM_UNAVAILABLE",
                "AI generation service is temporarily unavailable",
                retryable=True,
                failover_allowed=False,
                details={"provider": requested_provider},
            )
        return [config], True, "manual", "model selected manually"

    tier, reason = _classify_request_complexity(request, correction)
    candidates = [
        configs[provider_id]
        for provider_id in _provider_order(tier)
        if configs[provider_id].configured and not _circuit_open(provider_id)
    ]
    if not candidates:
        raise UpstreamGenerationError(
            "AI_UPSTREAM_UNAVAILABLE",
            "AI generation service is currently unavailable",
            retryable=True,
            failover_allowed=False,
            details={"attempted_providers": []},
        )
    return candidates, False, tier, reason


def _status_from_generation_error(message: str) -> int | None:
    match = re.search(
        r"(?:返回|status(?:\s+code)?|http)\s*[:=]?\s*(\d{3})",
        message,
        re.IGNORECASE,
    )
    return int(match.group(1)) if match else None


async def _call_provider_with_retries(
    config: AIProviderConfig,
    request: GenerateRequest,
    correction: str,
    deadline: float,
) -> tuple[dict[str, Any], str, dict[str, Any], list[dict[str, Any]]]:
    retries = _bounded_int_env(
        "AI_UPSTREAM_MAX_RETRIES",
        default=0,
        minimum=0,
        maximum=5,
    )
    provider_timeout_env = {
        "zhipu_glm52": "ZHIPU_READ_TIMEOUT_SECONDS",
        "kimi": "KIMI_READ_TIMEOUT_SECONDS",
        "kimi_k27": "KIMI_READ_TIMEOUT_SECONDS",
        "deepseek": "DEEPSEEK_READ_TIMEOUT_SECONDS",
    }.get(config.id, "AI_READ_TIMEOUT_SECONDS")
    provider_default_timeout = {
        "zhipu_glm52": 600.0,
        "kimi": 600.0,
        "kimi_k27": 600.0,
        "deepseek": 300.0,
    }.get(config.id, 600.0)
    read_timeout = _bounded_float_env(
        provider_timeout_env,
        default=provider_default_timeout,
        minimum=0.1,
        maximum=600.0,
        fallbacks=("AI_READ_TIMEOUT_SECONDS", "DEEPSEEK_REQUEST_TIMEOUT_SECONDS"),
    )
    provider_wall_timeout_env = {
        "zhipu_glm52": "ZHIPU_WALL_TIMEOUT_SECONDS",
        "kimi": "KIMI_WALL_TIMEOUT_SECONDS",
        "kimi_k27": "KIMI_WALL_TIMEOUT_SECONDS",
        "deepseek": "DEEPSEEK_WALL_TIMEOUT_SECONDS",
    }.get(config.id, "AI_PROVIDER_WALL_TIMEOUT_SECONDS")
    provider_default_wall_timeout = {
        "zhipu_glm52": 210.0,
        "kimi": 180.0,
        "kimi_k27": 180.0,
        "deepseek": 90.0,
    }.get(config.id, 180.0)
    wall_timeout = _bounded_float_env(
        provider_wall_timeout_env,
        default=provider_default_wall_timeout,
        minimum=0.05,
        maximum=600.0,
        fallbacks=("AI_PROVIDER_WALL_TIMEOUT_SECONDS",),
    )
    backoff = _bounded_float_env(
        "AI_RETRY_BACKOFF_SECONDS",
        default=0.5,
        minimum=0.0,
        maximum=30.0,
    )
    attempts: list[dict[str, Any]] = []

    for attempt in range(1, retries + 2):
        attempt_started = time.monotonic()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise UpstreamGenerationError(
                "AI_UPSTREAM_TIMEOUT",
                "AI generation service exceeded the overall timeout",
                retryable=True,
                failover_allowed=True,
                details={
                    "provider": config.id,
                    "attempts": attempt - 1,
                    "provider_attempts": attempts,
                },
            )

        # Give the active provider its complete remaining window. Fast 429/5xx
        # responses can still be retried, while an actual timeout fails over
        # immediately below and therefore does not need a pre-reserved retry slot.
        attempt_timeout = min(read_timeout, remaining)
        attempt_wall_timeout = min(wall_timeout, remaining)
        upstream_request_id: str | None = None
        token = _ACTIVE_PROVIDER_CONFIG.set(config)
        try:
            provider_call = _call_deepseek_legacy(
                request,
                correction=correction,
                timeout_seconds=attempt_timeout,
            )
            # ``httpx`` read timeouts only measure silence between two chunks.
            # Reasoning models can keep a broken request alive forever by
            # streaming heartbeats or internal reasoning.  The wall timeout is
            # therefore deliberately separate and applies to streaming too.
            generated, model, model_meta = await asyncio.wait_for(
                provider_call,
                timeout=attempt_wall_timeout,
            )
        except (asyncio.TimeoutError, httpx.TimeoutException):
            outcome = "timeout"
            status_code = None
            upstream_code = "AI_UPSTREAM_TIMEOUT"
        except _ProviderHTTPStatusError as exc:
            status_code = exc.status_code
            upstream_request_id = exc.request_id
            if status_code == 429:
                outcome = "http_429"
                upstream_code = "AI_UPSTREAM_UNAVAILABLE"
            elif status_code >= 500:
                outcome = f"http_{status_code}"
                upstream_code = "AI_UPSTREAM_UNAVAILABLE"
            else:
                if status_code in {401, 403}:
                    error_code = "AI_UPSTREAM_AUTH_FAILED"
                    error_message = (
                        "AI generation service rejected its credentials or permissions"
                    )
                elif status_code == 404:
                    error_code = "AI_UPSTREAM_CONFIG_ERROR"
                    error_message = "AI generation service endpoint is not configured"
                else:
                    error_code = "AI_UPSTREAM_REJECTED"
                    error_message = "AI generation service rejected the request"
                attempt_record: dict[str, Any] = {
                    "provider": config.id,
                    "attempt": attempt,
                    "outcome": f"http_{status_code}",
                    "status_code": status_code,
                    "elapsed_seconds": round(time.monotonic() - attempt_started, 3),
                }
                if upstream_request_id:
                    attempt_record["request_id"] = upstream_request_id
                attempts.append(attempt_record)
                raise UpstreamGenerationError(
                    error_code,
                    error_message,
                    retryable=False,
                    failover_allowed=False,
                    details={
                        "provider": config.id,
                        "status_code": status_code,
                        "attempts": attempt,
                        "provider_attempts": attempts,
                    },
                ) from exc
        except UpstreamGenerationError:
            raise
        except GenerationError as exc:
            # Parsing/contract failures happen after a successful HTTP call.
            # Attach only safe diagnostics so the outer quality loop can avoid
            # sending the exact same repair request to the same provider again.
            exc.details = {
                **exc.details,
                "provider": config.id,
                "model": str(exc.details.get("model") or config.model),
                "provider_attempts": [
                    *attempts,
                    {
                        "provider": config.id,
                        "attempt": attempt,
                        "outcome": "invalid_response",
                        "elapsed_seconds": round(time.monotonic() - attempt_started, 3),
                    },
                ],
            }
            raise
        except httpx.HTTPError:
            outcome = "connection_error"
            status_code = None
            upstream_code = "AI_UPSTREAM_UNAVAILABLE"
        else:
            attempts.append(
                {
                    "provider": config.id,
                    "attempt": attempt,
                    "outcome": "success",
                    "elapsed_seconds": round(time.monotonic() - attempt_started, 3),
                }
            )
            return generated, model, model_meta, attempts
        finally:
            _ACTIVE_PROVIDER_CONFIG.reset(token)

        attempt_record = {
            "provider": config.id,
            "attempt": attempt,
            "outcome": outcome,
            "elapsed_seconds": round(time.monotonic() - attempt_started, 3),
        }
        if status_code is not None:
            attempt_record["status_code"] = status_code
        if upstream_request_id:
            attempt_record["request_id"] = upstream_request_id
        attempts.append(attempt_record)
        # A timeout has already consumed this provider's allotted budget.
        # Repeating the same call would starve fallback providers, so switch
        # provider immediately while retaining retries for 429/5xx failures.
        if upstream_code == "AI_UPSTREAM_TIMEOUT":
            raise UpstreamGenerationError(
                upstream_code,
                "AI generation service timed out",
                retryable=True,
                failover_allowed=True,
                details={
                    "provider": config.id,
                    "status_code": status_code,
                    "attempts": attempt,
                    "provider_attempts": attempts,
                },
            )
        if attempt > retries:
            message = (
                "AI generation service timed out"
                if upstream_code == "AI_UPSTREAM_TIMEOUT"
                else "AI generation service is unavailable"
            )
            raise UpstreamGenerationError(
                upstream_code,
                message,
                retryable=True,
                failover_allowed=True,
                details={
                    "provider": config.id,
                    "status_code": status_code,
                    "attempts": attempt,
                    "provider_attempts": attempts,
                },
            )
        delay = backoff * (2 ** (attempt - 1))
        remaining = deadline - time.monotonic()
        if delay > 0 and remaining > 0:
            await asyncio.sleep(min(delay, remaining))

    raise AssertionError("unreachable")


async def _call_deepseek(
    request: GenerateRequest,
    correction: str = "",
    timeout_seconds: float | None = None,
    excluded_providers: set[str] | None = None,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    requested_provider = getattr(request, "ai_provider", "auto") or "auto"
    candidates, explicit, routing_tier, routing_reason = _provider_candidates(
        requested_provider,
        request,
        correction,
    )
    excluded = excluded_providers or set()
    if not explicit and excluded:
        candidates = [config for config in candidates if config.id not in excluded]
        if not candidates:
            raise GenerationError(
                "所有已配置的 AI 生成服务都已返回不合格结果",
                details={"excluded_providers": sorted(excluded)},
            )
    if not explicit:
        max_candidates = _bounded_int_env(
            "AI_MAX_FAILOVER_PROVIDERS",
            default=3,
            minimum=1,
            maximum=len(AI_PROVIDER_IDS),
        )
        candidates = candidates[:max_candidates]
    overall_timeout = _optional_timeout_env(
        "AI_OVERALL_TIMEOUT_SECONDS",
        default=480.0,
        maximum=3600.0,
        fallbacks=("DEEPSEEK_GENERATION_BUDGET_SECONDS",),
    )
    provider_timeout = _bounded_float_env(
        "AI_READ_TIMEOUT_SECONDS",
        default=600.0,
        minimum=3.0,
        maximum=600.0,
        fallbacks=("DEEPSEEK_REQUEST_TIMEOUT_SECONDS",),
    )
    if timeout_seconds is not None:
        try:
            requested_timeout = float(timeout_seconds)
        except (TypeError, ValueError):
            requested_timeout = provider_timeout
        provider_timeout = max(0.05, requested_timeout)
        if overall_timeout is not None:
            overall_timeout = min(overall_timeout, provider_timeout)
    deadline = (
        time.monotonic() + overall_timeout
        if overall_timeout is not None
        else None
    )
    attempted_providers: list[str] = []
    provider_attempts: list[dict[str, Any]] = []
    last_error: UpstreamGenerationError | None = None

    for index, config in enumerate(candidates):
        if deadline is None:
            # No aggregate deadline: every configured fallback gets a complete
            # request window. This favors successful output over an arbitrary
            # wall-clock cutoff while still failing over a stalled provider.
            provider_budget = provider_timeout
        else:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            providers_remaining = len(candidates) - index
            if providers_remaining <= 1:
                provider_budget = remaining
            else:
                # Compatibility mode for deployments that explicitly configure
                # a finite aggregate timeout.
                fallback_reserve = min(
                    10.0,
                    remaining / (providers_remaining + 1),
                )
                provider_budget = remaining - fallback_reserve * (providers_remaining - 1)
        provider_deadline = time.monotonic() + max(0.05, provider_budget)
        if not _claim_provider_slot(config.id):
            if explicit:
                raise UpstreamGenerationError(
                    "AI_UPSTREAM_UNAVAILABLE",
                    "AI generation service is temporarily unavailable",
                    retryable=True,
                    failover_allowed=False,
                    details={
                        "provider": config.id,
                        "attempted_providers": attempted_providers,
                        "provider_attempts": provider_attempts,
                    },
                )
            continue
        attempted_providers.append(config.id)
        try:
            generated, model, model_meta, attempts = await _call_provider_with_retries(
                config,
                request,
                correction,
                provider_deadline,
            )
        except UpstreamGenerationError as exc:
            last_error = exc
            safe_attempts = exc.details.get("provider_attempts", [])
            if isinstance(safe_attempts, list):
                provider_attempts.extend(safe_attempts)
            if exc.retryable:
                _record_provider_failure(config.id)
            else:
                _record_provider_success(config.id)
            if explicit or not exc.failover_allowed:
                exc.details = {
                    **exc.details,
                    "attempted_providers": attempted_providers,
                    "provider_attempts": provider_attempts,
                    "failover_used": False,
                }
                raise
            continue
        except GenerationError as exc:
            _release_provider_probe(config.id)
            safe_attempts = exc.details.get("provider_attempts", [])
            if isinstance(safe_attempts, list):
                provider_attempts.extend(safe_attempts)
            exc.details = {
                **exc.details,
                "provider": config.id,
                "attempted_providers": attempted_providers,
                "provider_attempts": provider_attempts,
                "failover_used": len(attempted_providers) > 1,
                "routing_tier": routing_tier,
                "routing_reason": routing_reason,
            }
            raise
        except BaseException:
            _release_provider_probe(config.id)
            raise

        _record_provider_success(config.id)
        provider_attempts.extend(attempts)
        routing = {
            "provider": config.id,
            "model": model,
            "failover_used": len(attempted_providers) > 1,
            "attempted_providers": attempted_providers,
            "provider_attempts": provider_attempts,
            "routing_tier": routing_tier,
            "routing_reason": routing_reason,
        }
        generated = dict(generated)
        generated["ai_routing"] = routing
        model_meta = {**model_meta, **routing}
        return generated, model, model_meta

    code = last_error.code if last_error else "AI_UPSTREAM_UNAVAILABLE"
    message = (
        "AI generation service timed out"
        if code == "AI_UPSTREAM_TIMEOUT"
        else "AI generation service is unavailable"
    )
    raise UpstreamGenerationError(
        code,
        message,
        retryable=True,
        failover_allowed=False,
        details={
            "attempted_providers": attempted_providers,
            "provider_attempts": provider_attempts,
            "failover_used": len(attempted_providers) > 1,
            "routing_tier": routing_tier,
            "routing_reason": routing_reason,
        },
    )


async def generate_app(
    request: GenerateRequest,
    *,
    attempt_sink: GenerationAttemptSink | None = None,
) -> GenerateResponse:
    capability_contract = hardware_capability_registry.resolve(
        request.required_capabilities, request.runtime_fallbacks
    )
    if capability_contract.get("status") == "blocked":
        error = capability_contract["error"]
        raise GenerationError(
            error["message"],
            code=error["code"],
            owner=error["owner"],
            retryable=error["retryable"],
            details=error.get("details", {}),
        )
    budget_seconds = _optional_timeout_env(
        "AI_OVERALL_TIMEOUT_SECONDS",
        default=480.0,
        maximum=3600.0,
        fallbacks=("DEEPSEEK_GENERATION_BUDGET_SECONDS",),
    )
    request_timeout_seconds = _bounded_float_env(
        "AI_READ_TIMEOUT_SECONDS",
        default=600.0,
        minimum=3.0,
        maximum=600.0,
        fallbacks=("DEEPSEEK_REQUEST_TIMEOUT_SECONDS",),
    )
    max_attempts = _bounded_int_env(
        "DEEPSEEK_MAX_ATTEMPTS",
        # One quality attempt per configured provider.  Provider failover is
        # more useful than asking the same model to repeat a malformed answer,
        # and prevents a single generation from growing into a 20-minute job.
        default=3,
        minimum=1,
        maximum=12,
    )
    quality_attempts_per_provider = _bounded_int_env(
        "AI_QUALITY_ATTEMPTS_PER_PROVIDER",
        # Allow one correction with the validator's exact error before moving
        # to the next provider.  The whole-job deadline still prevents this
        # quality retry from extending a task indefinitely.
        default=2,
        minimum=1,
        maximum=4,
    )
    deadline = (
        time.monotonic() + budget_seconds
        if budget_seconds is not None
        else None
    )
    correction = ""
    generated: dict[str, Any] = {}
    model = ""
    code = ""
    warnings: list[str] = []
    acceptance_tests: list[str] = []
    requirement_coverage: list[dict[str, str]] = []
    api_usage: dict[str, Any] = {"checked": False, "planned": [], "missing": []}
    last_error: GenerationError | None = None
    model_meta: dict[str, Any] = {}
    attempts_used = 0
    rejected_providers: set[str] = set()
    provider_quality_failures: dict[str, int] = {}

    def record_provider_quality_failure(meta: dict[str, Any]) -> None:
        """Keep a provider for one corrected retry before failing over.

        HTTP/auth/time-out failover is handled inside ``_call_deepseek``.  This
        counter is only for successful upstream responses that fail parsing or
        our deterministic product/API checks.  Retrying the same provider with
        ``correction`` is important: it is the only model that has just seen
        the complete candidate it needs to repair.
        """
        if (getattr(request, "ai_provider", "auto") or "auto") != "auto":
            return
        provider = meta.get("provider")
        if isinstance(provider, str) and provider:
            failure_count = provider_quality_failures.get(provider, 0) + 1
            provider_quality_failures[provider] = failure_count
            if failure_count >= quality_attempts_per_provider:
                rejected_providers.add(provider)

    for attempt in range(1, max_attempts + 1):
        remaining = deadline - time.monotonic() if deadline is not None else None
        if remaining is not None and remaining < 2.0:
            last_error = GenerationError(
                f"生成已达到 {budget_seconds:.0f} 秒时间预算"
            )
            break
        attempts_used = attempt
        call_timeout = (
            min(
                request_timeout_seconds,
                max(2.0, remaining),
            )
            if remaining is not None
            else request_timeout_seconds
        )
        model_meta = {}
        try:
            generated, model, model_meta = await _call_deepseek(
                request,
                correction,
                timeout_seconds=call_timeout,
                excluded_providers=set(rejected_providers),
            )
        except UpstreamGenerationError as exc:
            _emit_generation_attempt(
                attempt_sink,
                attempt=attempt,
                status="upstream_error",
                error=exc,
                model_meta=exc.details,
            )
            raise
        except GenerationError as exc:
            last_error = exc
            safe_meta = exc.details if isinstance(exc.details, dict) else {}
            record_provider_quality_failure(safe_meta)
            _emit_generation_attempt(
                attempt_sink,
                attempt=attempt,
                status="model_error",
                error=exc,
                model_meta=safe_meta,
            )
            correction = _build_correction(
                GenerationError(
                    "上一轮模型响应不可用。请只返回一个完整 JSON 对象，"
                    "不要 Markdown、代码围栏或解释；app_code 必须是 JSON 字符串。"
                ),
                attempt=attempt,
            )
            continue
        generated = _normalize_generation_payload(generated)
        candidate = generated.get("app_code")
        candidate_tests = generated.get("acceptance_tests")
        if not isinstance(candidate, str) or not candidate.strip():
            received_fields = ", ".join(sorted(str(key) for key in generated)[:8])
            field_hint = f"（收到字段：{received_fields}）" if received_fields else ""
            last_error = GenerationError(
                f"AI 生成结果中缺少可识别的 App 源码{field_hint}"
            )
            _emit_generation_attempt(
                attempt_sink,
                attempt=attempt,
                status="validation_failed",
                error=last_error,
                model_meta=model_meta,
            )
            record_provider_quality_failure(model_meta)
            correction = _build_correction(last_error, attempt=attempt)
            continue
        quality_tier, _ = _classify_request_complexity(request)
        minimum_quality_items = (
            3 if quality_tier in {"complex", "revision", "repair"} else 2
        )
        if (
            not isinstance(candidate_tests, list)
            or len(candidate_tests) < minimum_quality_items
            or not all(isinstance(item, str) and item.strip() for item in candidate_tests)
        ):
            last_error = GenerationError(
                "AI 生成结果中缺少与需求对应的具体 acceptance_tests：至少需要 "
                f"{minimum_quality_items} 项"
            )
            _emit_generation_attempt(
                attempt_sink,
                attempt=attempt,
                status="validation_failed",
                candidate=candidate,
                error=last_error,
                model_meta=model_meta,
            )
            record_provider_quality_failure(model_meta)
            correction = _build_correction(last_error, candidate, attempt)
            continue
        try:
            requirement_coverage = _validate_requirement_coverage(
                generated, request, candidate
            )
        except GenerationError as exc:
            last_error = exc
            _emit_generation_attempt(
                attempt_sink,
                attempt=attempt,
                status="validation_failed",
                candidate=candidate,
                error=exc,
                model_meta=model_meta,
            )
            record_provider_quality_failure(model_meta)
            correction = _build_correction(exc, candidate, attempt)
            continue
        candidate, compatibility_warnings = _normalize_lvgl_code(candidate)
        try:
            code_warnings = _validate_code(candidate)
            interaction_warnings = _validate_interaction_contract(
                candidate, request.prompt
            )
            product_warnings = _validate_product_contract(candidate, request.prompt)
            visual_warnings = _validate_visual_contract(candidate, request.prompt)
            api_warnings, api_usage = _validate_api_summaries(candidate)
            warnings = (
                compatibility_warnings
                + code_warnings
                + interaction_warnings
                + product_warnings
                + visual_warnings
                + api_warnings
            )
            code = candidate
            acceptance_tests = [str(item) for item in candidate_tests]
            last_error = None
            _emit_generation_attempt(
                attempt_sink,
                attempt=attempt,
                status="passed",
                candidate=candidate,
                model_meta=model_meta,
            )
            break
        except GenerationError as exc:
            last_error = exc
            _emit_generation_attempt(
                attempt_sink,
                attempt=attempt,
                status="validation_failed",
                candidate=candidate,
                error=exc,
                model_meta=model_meta,
            )
            record_provider_quality_failure(model_meta)
            correction = _build_correction(exc, candidate, attempt)
    if last_error is not None or not code:
        budget_label = (
            f"{budget_seconds:.0f} 秒预算内"
            if budget_seconds is not None
            else "自动修复流程中"
        )
        raise GenerationError(
            f"AI 生成服务在{budget_label}经过 "
            f"{attempts_used} 次尝试仍未通过检查：{last_error}"
        )
    manifest = _manifest(request)
    manifest["activities"][0]["entrypoint"] = "assets/main.py"
    summary = str(generated.get("summary") or f"已生成 {request.display_name}")
    prompt_normalized_zh = str(
        generated.get("prompt_normalized_zh") or request.prompt
    ).strip()
    prompt_normalized_en = str(
        generated.get("prompt_normalized_en") or request.prompt
    ).strip()
    raw_store_metadata = generated.get("store_metadata")
    store_metadata = raw_store_metadata if isinstance(raw_store_metadata, dict) else {}
    metadata_defaults = {
        "display_name_zh": request.display_name,
        "display_name_en": request.display_name,
        "short_description_zh": prompt_normalized_zh[:100],
        "short_description_en": prompt_normalized_en[:100],
        "long_description_zh": prompt_normalized_zh,
        "long_description_en": prompt_normalized_en,
        "release_notes_zh": f"首次生成 {request.display_name}",
        "release_notes_en": f"Initial generated release of {request.display_name}",
        "category": "generated",
    }
    store_metadata = {
        key: str(store_metadata.get(key) or default).strip()
        for key, default in metadata_defaults.items()
    }
    manifest.update(
        {
            "name": store_metadata["display_name_en"] or request.display_name,
            "short_description": store_metadata["short_description_en"],
            "long_description": store_metadata["long_description_en"],
            "category": store_metadata["category"],
        }
    )
    mpk = _build_mpk(request.package_name, manifest, code)
    mpk_filename = f"{request.package_name}_r{request.revision}.mpk"
    generation_result = {
        "schema_version": "mpos-gen-app-web-v1",
        "phase": "mpos-gen-app-web",
        "result": "success",
        "mode": "create" if not request.previous_code else "repair",
        "summary": summary,
        "model": model,
        "provider": str(model_meta.get("provider") or ""),
        "failover_used": bool(model_meta.get("failover_used", False)),
        "attempted_providers": model_meta.get("attempted_providers", []),
        "provider_attempts": model_meta.get("provider_attempts", []),
        "routing_tier": str(model_meta.get("routing_tier") or ""),
        "routing_reason": str(model_meta.get("routing_reason") or ""),
        "language": {
            "prompt_original": request.prompt,
            "prompt_normalized_zh": prompt_normalized_zh,
            "prompt_normalized_en": prompt_normalized_en,
        },
        "store_metadata": store_metadata,
        "app": {
            "fullname": request.package_name,
            "publisher": request.publisher,
            "version": request.version,
            "name": request.display_name,
        },
        "files_written": ["MANIFEST.JSON", "icon_64x64.png", "assets/main.py"],
        "api_usage": api_usage,
        "required_capabilities": request.required_capabilities,
        "required_accessories": request.required_accessories,
        "runtime_fallbacks": request.runtime_fallbacks,
        "physical_validation_required": (
            request.physical_validation_required
            or capability_contract.get("physical_validation_required", False)
        ),
        "capability_contract": capability_contract,
        "validation": {"gates": warnings},
        "requirement_coverage": requirement_coverage,
        "acceptance_tests": acceptance_tests,
        "warnings": warnings,
        "structured_errors": [],
        "handoff": {"next_phase": "mpos-test-app-web"},
    }
    return GenerateResponse(
        package_name=request.package_name,
        summary=summary,
        manifest=manifest,
        files=[
            GeneratedFile(
                path="MANIFEST.JSON",
                content=json.dumps(manifest, ensure_ascii=False, indent=2),
            ),
            GeneratedFile(path="assets/main.py", content=code),
            GeneratedFile(
                path="generation_result.json",
                content=json.dumps(generation_result, ensure_ascii=False, indent=2),
            ),
        ],
        mpk_base64=mpk,
        model=model,
        provider=str(model_meta.get("provider") or ""),
        failover_used=bool(model_meta.get("failover_used", False)),
        attempted_providers=model_meta.get("attempted_providers", []),
        provider_attempts=model_meta.get("provider_attempts", []),
        routing_tier=str(model_meta.get("routing_tier") or ""),
        routing_reason=str(model_meta.get("routing_reason") or ""),
        warnings=warnings,
        acceptance_tests=acceptance_tests,
        requirement_coverage=requirement_coverage,
        mpk_filename=mpk_filename,
        revision=request.revision,
        prompt_normalized_zh=prompt_normalized_zh,
        prompt_normalized_en=prompt_normalized_en,
        store_metadata=store_metadata,
        required_capabilities=request.required_capabilities,
        required_accessories=request.required_accessories,
        runtime_fallbacks=request.runtime_fallbacks,
        physical_validation_required=(
            request.physical_validation_required
            or capability_contract.get("physical_validation_required", False)
        ),
        capability_contract=capability_contract,
    )
