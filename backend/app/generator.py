import ast
import base64
import io
import json
import os
import re
import struct
import time
import zipfile
import zlib
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx

from .models import GenerateRequest, GenerateResponse, GeneratedFile


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
- 不调用 `get_child_cnt()`、`get_child()` 或其他控件树读取方法；创建控件时保存到 `self.day_buttons` 等 Python 列表，需要数量时使用 `len(self.day_buttons)`
- `onCreate()` 必须快速返回，严禁 `while True`、界面主循环以及
  `sleep()`、`sleep_ms()` 等阻塞等待
- 计算器解析表达式等纯计算逻辑允许使用有明确退出条件的有限 `while`；
  循环体必须推进索引或缩小集合。动画、倒计时和游戏更新仍必须使用 lv.timer_create
- 动画和游戏更新必须使用 `self.update_timer = lv.timer_create(self.update_frame, 33, None)`；回调接收 timer 参数，不能自己写主循环
- 每个 App 都必须实现 `self_test(self)`，程序化调用真实功能方法并比较操作前后的状态，返回至少两个布尔结果组成的 dict
- `self_test()` 不能直接返回写死的 True；必须包含方法调用和前后值比较，并在结束前恢复被修改的状态，保证用户看到的是初始界面
- 所有代码必须放在一个入口 Python 文件中

只使用下面这些稳定的 UI 能力，不要猜 API：
- 创建：lv.obj(parent)、lv.label(parent)、lv.button(parent)、lv.textarea(parent)
- 布局：set_size、set_width、set_height、set_pos、set_x、set_y、
  align(align, x, y)、align_to(other, align, x, y)、center、
  set_flex_flow(flow)、set_flex_align(main, cross, track)
- set_flex_flow 只能传一个 flow 参数，正确示例：`container.set_flex_flow(lv.FLEX_FLOW.ROW_WRAP)`；严禁额外传 selector 或 0
- 交互：add_event_cb(callback, lv.EVENT.CLICKED, None)、lv.timer_create(callback, milliseconds, None)
- 文字：label.set_text、label.get_text
- 颜色：lv.color_hex(0xRRGGBB)
- 安全样式：set_style_bg_color、set_style_bg_opa、set_style_text_color、set_style_radius、
  set_style_border_width、set_style_border_color、set_style_pad_all、set_style_pad_hor、
  set_style_pad_ver、set_style_shadow_width、set_style_shadow_color、set_style_shadow_opa

视觉质量是验收条件，不是可选项。默认采用精致深色界面：
- 只使用 4 到 6 种协调颜色：背景 0x0F172A、卡片 0x1E293B、主色 0x6366F1、
  强调色 0x22D3EE、正文 0xF8FAFC、次要文字 0x94A3B8
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
1. 使用统一深色调色板和高对比文字，不允许默认白底灰按钮。
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


GENERAL_UI_BLUEPRINT = """
请按下面的安全设计系统组织界面：
- screen 使用深色背景；顶部放 32-40px 高的标题/状态区。
- 中间使用一个或多个有圆角、内边距和细边框的内容卡片。
- 底部或卡片内放主要操作区；同组按钮等高、等宽、间距一致。
- 标题文字明亮，说明文字使用次要颜色，关键数字或状态使用强调色。
- screen 设置页面背景；内容卡片设置背景、圆角、内边距和边框；主要按钮设置强调色、圆角和一致尺寸。
- 只使用系统提示列出的稳定 LVGL API，不使用主题、字体、grid、style 对象或坐标读取。
"""


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
    pass


class ApiValidationError(GenerationError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def _message_text(value: Any) -> str:
    """Normalize OpenAI-compatible string and multipart message content."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
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
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        for match in re.finditer(r"\{", cleaned):
            try:
                parsed, _end = decoder.raw_decode(cleaned[match.start() :])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    raise GenerationError("DeepSeek 没有返回可解析的生成结果")


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


def _settings() -> tuple[str, str, str]:
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash").strip()
    if not key or key == "replace_with_your_deepseek_api_key":
        raise GenerationError(
            "未配置 DEEPSEEK_API_KEY。请复制 backend/.env.example 为 backend/.env 并填写 Key。"
        )
    return key, base_url, model


async def _call_deepseek(
    request: GenerateRequest,
    correction: str = "",
    timeout_seconds: float | None = None,
) -> tuple[dict[str, Any], str]:
    key, base_url, model = _settings()
    user_prompt = (
        "请生成 JSON。用户需求：\n"
        f"{request.prompt}\n\n"
        f"显示名：{request.display_name}\n"
        f"包名：{request.package_name}\n"
        "入口文件固定为 app.py，入口类固定为 GeneratedApp。\n\n"
        f"{VISUAL_REQUIREMENTS}\n{GENERAL_UI_BLUEPRINT}"
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
    if correction:
        user_prompt += (
            "\n\n上一次生成被安全检查拒绝。请完整重写代码并修复下面的问题，"
            "不要解释，只返回新的 JSON。\n"
            "输出前必须逐字检查 app_code：不得仍然包含被指出的调用；"
            "不得为了通过检查而删除用户要求的功能；不得用 pass 或假按钮代替交互。\n"
            f"检查失败原因：\n{correction}"
        )
    if request.previous_code:
        user_prompt += (
            "\n\n这是已有 App 的连续修改，不是从零生成。"
            "必须保留未被用户要求删除的功能，基于下面的上一版代码修改。\n"
            f"运行错误（如果为空则表示功能修改）：\n{request.runtime_error or '无'}\n\n"
            f"上一次 app.py：\n{request.previous_code}"
        )
    max_tokens = max(2200, min(6000, int(os.getenv("DEEPSEEK_MAX_TOKENS", "3200"))))
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.12 if correction else 0.25,
        "max_tokens": max_tokens,
        "thinking": {"type": "disabled"},
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    request_timeout = timeout_seconds or float(
        os.getenv("DEEPSEEK_REQUEST_TIMEOUT_SECONDS", "19")
    )
    request_timeout = max(2.0, min(60.0, request_timeout))
    try:
        timeout = httpx.Timeout(
            request_timeout,
            connect=min(5.0, request_timeout),
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{base_url}/chat/completions", headers=headers, json=payload
            )
    except httpx.TimeoutException as exc:
        raise GenerationError(
            f"DeepSeek 在 {request_timeout:.1f} 秒内没有返回"
        ) from exc
    except httpx.HTTPError as exc:
        raise GenerationError(f"无法连接 DeepSeek：{exc}") from exc

    if response.status_code >= 400:
        detail = response.text[:800]
        raise GenerationError(f"DeepSeek 返回 {response.status_code}：{detail}")

    try:
        body = response.json()
        choice = body["choices"][0]
        message = choice["message"]
        if not isinstance(message, dict):
            raise TypeError("message is not an object")
        generated = _parse_model_json(message)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise GenerationError("DeepSeek 没有返回可解析的生成结果") from exc
    return generated, body.get("model", model)


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
    blocking_while_nodes: set[int] = set()
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
            if function.name == "onCreate" or condition_is_always_true:
                blocking_while_nodes.add(id(child))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
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
        elif isinstance(node, ast.While) and id(node) in blocking_while_nodes:
            hits.append(
                "阻塞式 while 循环（有限计算循环可以保留；界面更新请使用 lv.timer_create）"
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
        elif (
            isinstance(node, ast.Attribute)
            and node.attr
            in {
                "get_pos",
                "get_coords",
                "get_x",
                "get_y",
                "get_child_cnt",
                "get_child",
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


def _validate_visual_contract(code: str) -> list[str]:
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
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _dotted_name(node.func) == "lv.color_hex":
            color_calls += 1
    if color_calls < 4:
        missing.append("至少 4 个协调的 lv.color_hex 颜色")
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
    if missing:
        raise GenerationError(
            "界面仍像未设计的原型，必须补齐：" + "、".join(dict.fromkeys(missing))
        )
    return ["已通过三层背景、配色、圆角、间距、尺寸和卡片细节检查"]


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
    functions = {
        item["name"]
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
        "functions": functions,
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
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Attribute)
                and isinstance(value.func.value, ast.Name)
                and value.func.value.id == "lv"
                and value.func.attr in indexes["widgets"]
            ):
                for target in targets:
                    name = _dotted_name(target)
                    if name:
                        object_types[name] = value.func.attr

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
            if method_name not in indexes["widgets"][widget_name]:
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
    }
    normalized = code
    applied: list[str] = []
    for old, new in replacements.items():
        if old in normalized:
            normalized = normalized.replace(old, new)
            applied.append(f"已自动兼容 {old} → {new}")
    normalized, flex_flow_count = re.subn(
        r"(\.set_flex_flow\(\s*[^,\n]+?)\s*,\s*0\s*\)",
        r"\1)",
        normalized,
    )
    if flex_flow_count:
        applied.append("已移除 set_flex_flow 不支持的 selector 参数")
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


def _build_correction(error: GenerationError, candidate: str = "") -> str:
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
    if "get_child_cnt" in message or "get_child" in message:
        suggestions.append(
            "不要读取 LVGL 控件树。创建日期按钮时把引用加入 self.day_buttons，"
            "需要检查数量时使用 len(self.day_buttons)。最终 app_code 中 "
            "get_child_cnt 和 get_child 的出现次数必须为 0。"
        )
    if "set_text_align" in message:
        suggestions.append("删除 set_text_align，使用 label.align(...) 摆放标签。")
    if "align(base, align, x, y)" in message:
        suggestions.append(
            "当前绑定中 widget.align 只能传 align、x、y 三个参数。"
            "相对另一个控件定位必须改为 "
            "widget.align_to(base, lv.ALIGN.CENTER, x, y)。"
        )
    if "阻塞式 while" in message:
        suggestions.append(
            "只删除 while True、onCreate 内循环或持续刷新界面的主循环。"
            "动画和倒计时改用 lv.timer_create。若是计算器解析表达式，"
            "可以保留有限 while，但每轮必须推进索引并具有明确退出条件。"
        )
    if "界面仍像未设计的原型" in message:
        suggestions.append(
            "按照三层设计系统完整重做界面：screen、内容卡片和主要按钮分别设置背景；"
            "至少两处圆角、内容卡片的一处明确内边距、三个明确尺寸/布局设置，"
            "并增加细边框或轻阴影。"
            "颜色统一使用 4-6 个协调的 lv.color_hex。"
        )
    if "API summary" in message:
        suggestions.append(
            "只能使用系统提示列出的稳定 API；不要用你记忆中的普通 LVGL API 猜测方法名。"
        )
    context = ""
    if candidate:
        context = (
            "\n\n这是刚才未通过检查的完整 app.py。请在保留用户功能的前提下完整重写，"
            "逐项删除或替换不兼容调用：\n"
            f"{candidate[:24_000]}"
        )
    return "\n".join([message, *suggestions]) + context


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


async def generate_app(request: GenerateRequest) -> GenerateResponse:
    budget_seconds = max(
        8.0,
        min(60.0, float(os.getenv("DEEPSEEK_GENERATION_BUDGET_SECONDS", "20"))),
    )
    request_timeout_seconds = max(
        3.0,
        min(30.0, float(os.getenv("DEEPSEEK_REQUEST_TIMEOUT_SECONDS", "19"))),
    )
    max_attempts = max(
        1,
        min(2, int(os.getenv("DEEPSEEK_MAX_ATTEMPTS", "1"))),
    )
    deadline = time.monotonic() + budget_seconds
    correction = ""
    generated: dict[str, Any] = {}
    model = ""
    code = ""
    warnings: list[str] = []
    acceptance_tests: list[str] = []
    api_usage: dict[str, Any] = {"checked": False, "planned": [], "missing": []}
    last_error: GenerationError | None = None
    attempts_used = 0
    for attempt in range(max_attempts):
        remaining = deadline - time.monotonic()
        if remaining < 2.0:
            last_error = GenerationError(
                f"生成已达到 {budget_seconds:.0f} 秒时间预算"
            )
            break
        attempts_used = attempt + 1
        attempts_after_this = max_attempts - attempts_used
        reserved_for_retry = 3.0 if attempts_after_this else 0.0
        call_timeout = min(
            request_timeout_seconds,
            max(2.0, remaining - reserved_for_retry),
        )
        try:
            generated, model = await _call_deepseek(
                request,
                correction,
                timeout_seconds=call_timeout,
            )
        except GenerationError as exc:
            last_error = exc
            correction = (
                "上一轮模型响应不是可解析的 JSON 对象。"
                "请只返回一个完整 JSON 对象，不要 Markdown、代码围栏或解释；"
                "app_code 必须是 JSON 字符串。"
            )
            continue
        generated = _normalize_generation_payload(generated)
        candidate = generated.get("app_code")
        candidate_tests = generated.get("acceptance_tests")
        if not isinstance(candidate, str) or not candidate.strip():
            received_fields = ", ".join(sorted(str(key) for key in generated)[:8])
            field_hint = f"（收到字段：{received_fields}）" if received_fields else ""
            last_error = GenerationError(
                f"DeepSeek 返回结果中缺少可识别的 App 源码{field_hint}"
            )
            correction = _build_correction(last_error)
            continue
        if (
            not isinstance(candidate_tests, list)
            or len(candidate_tests) < 2
            or not all(isinstance(item, str) and item.strip() for item in candidate_tests)
        ):
            last_error = GenerationError("DeepSeek 返回结果中缺少至少两个 acceptance_tests")
            correction = _build_correction(last_error, candidate)
            continue
        candidate, compatibility_warnings = _normalize_lvgl_code(candidate)
        try:
            code_warnings = _validate_code(candidate)
            interaction_warnings = _validate_interaction_contract(
                candidate, request.prompt
            )
            product_warnings = _validate_product_contract(candidate, request.prompt)
            visual_warnings = _validate_visual_contract(candidate)
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
            break
        except GenerationError as exc:
            last_error = exc
            correction = _build_correction(exc, candidate)
    if last_error is not None or not code:
        raise GenerationError(
            f"DeepSeek 在 {budget_seconds:.0f} 秒预算内经过 "
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
        "validation": {"gates": warnings},
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
        warnings=warnings,
        acceptance_tests=acceptance_tests,
        mpk_filename=mpk_filename,
        revision=request.revision,
        prompt_normalized_zh=prompt_normalized_zh,
        prompt_normalized_en=prompt_normalized_en,
        store_metadata=store_metadata,
    )
