import ast
import base64
import io
import json
import os
import re
import zipfile
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
- 不使用自定义主题、复杂 style selector、grid template 或当前 Web 构建未验证的 LVGL API
- 事件回调必须使用 `widget.add_event_cb(callback, lv.EVENT.CLICKED, None)`
- 严禁使用 `lv.event_code`、`lv.EVENT_CLICKED` 等其他事件名称；当前 MicroPythonOS 只支持 `lv.EVENT.CLICKED` 这种写法
- 不使用 `label.set_text_align()`；需要摆放文字时使用 `label.align(...)`
- Web 模拟器的默认字体不含中文字形，所有可见控件文字必须使用简短英文或 ASCII，避免显示方框乱码
- 实际显示区域是 320x240，不是手机屏幕；优先使用百分比尺寸和 flex 布局，确保所有内容都在屏幕内
- 游戏必须真的提供可点击的开始/跳跃/重置等交互；日历使用 label、button、obj 组成简单网格，不使用未验证的 calendar 高级 API
- 游戏不能依赖电脑键盘、外接手柄或设备硬件键；所有操作必须在 App 画面内提供可点击按钮
- 射击游戏必须在底部提供清晰的 `LEFT`、`RIGHT`、`FIRE` 三个屏幕按钮：LEFT/RIGHT 点击后改变玩家位置，FIRE 点击后创建或激活子弹
- 射击游戏必须保存玩家和子弹的数字坐标，用 `set_pos()` 更新对象；使用 `lv.timer_create()` 更新子弹、敌人、碰撞和分数
- `onCreate()` 必须快速返回，严禁任何 `while` 循环以及 `sleep()`、`sleep_ms()` 等阻塞等待
- 动画和游戏更新必须使用 `self.update_timer = lv.timer_create(self.update_frame, 33, None)`；回调接收 timer 参数，不能自己写主循环
- 每个 App 都必须实现 `self_test(self)`，程序化调用真实功能方法并比较操作前后的状态，返回至少两个布尔结果组成的 dict
- `self_test()` 不能直接返回写死的 True；必须包含方法调用和前后值比较，并在结束前恢复被修改的状态，保证用户看到的是初始界面
- 所有代码必须放在一个入口 Python 文件中

JSON 格式必须严格为：
{
  "summary": "一句话说明生成了什么",
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


class GenerationError(RuntimeError):
    pass


class ApiValidationError(GenerationError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


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
    request: GenerateRequest, correction: str = ""
) -> tuple[dict[str, Any], str]:
    key, base_url, model = _settings()
    user_prompt = (
        "请生成 JSON。用户需求：\n"
        f"{request.prompt}\n\n"
        f"显示名：{request.display_name}\n"
        f"包名：{request.package_name}\n"
        "入口文件固定为 app.py，入口类固定为 GeneratedApp。"
    )
    if _is_shooter_prompt(request.prompt):
        user_prompt += (
            "\n\n这是射击游戏，属于强交互任务。验收条件："
            "\n1. 屏幕底部必须有可见的 LEFT、RIGHT、FIRE 三个 lv.button。"
            "\n2. 三个按钮都必须通过 add_event_cb 连接到真实回调，不能只是显示文字。"
            "\n3. LEFT/RIGHT 每次点击都要改变玩家数字坐标并调用 set_pos。"
            "\n4. FIRE 必须创建或激活子弹，lv.timer_create 回调必须让子弹移动并处理越界或碰撞。"
            "\n5. 不得依赖键盘、硬件摇杆、外接手柄或隐藏控件。"
        )
    if correction:
        user_prompt += (
            "\n\n上一次生成被安全检查拒绝。请完整重写代码并修复下面的问题，"
            "不要解释，只返回新的 JSON：\n"
            f"{correction}"
        )
    if request.runtime_error and request.previous_code:
        user_prompt += (
            "\n\n下面是上一次代码在真实 MicroPythonOS WASM 中运行时的错误。"
            "请保留用户要求的功能，完整重写并修复所有不兼容 API，不要只解释。\n"
            f"运行错误：\n{request.runtime_error}\n\n"
            f"上一次 app.py：\n{request.previous_code}"
        )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
        "max_tokens": 5000,
        "thinking": {"type": "disabled"},
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{base_url}/chat/completions", headers=headers, json=payload
            )
    except httpx.HTTPError as exc:
        raise GenerationError(f"无法连接 DeepSeek：{exc}") from exc

    if response.status_code >= 400:
        detail = response.text[:800]
        raise GenerationError(f"DeepSeek 返回 {response.status_code}：{detail}")

    try:
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        generated = json.loads(content)
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
        elif isinstance(node, ast.While):
            hits.append("while 循环（动画请使用 lv.timer_create）")
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


def _build_mpk(package_name: str, manifest: dict[str, Any], app_code: str) -> str:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(
            f"{package_name}/MANIFEST.JSON",
            json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
        )
        archive.writestr(f"{package_name}/assets/main.py", app_code)
    return base64.b64encode(stream.getvalue()).decode("ascii")


async def generate_app(request: GenerateRequest) -> GenerateResponse:
    correction = ""
    generated: dict[str, Any] = {}
    model = ""
    code = ""
    warnings: list[str] = []
    acceptance_tests: list[str] = []
    api_usage: dict[str, Any] = {"checked": False, "planned": [], "missing": []}
    last_error: GenerationError | None = None
    for _attempt in range(3):
        generated, model = await _call_deepseek(request, correction)
        candidate = generated.get("app_code")
        candidate_tests = generated.get("acceptance_tests")
        if not isinstance(candidate, str) or not candidate.strip():
            last_error = GenerationError("DeepSeek 返回结果中缺少 app_code")
            correction = str(last_error)
            continue
        if (
            not isinstance(candidate_tests, list)
            or len(candidate_tests) < 2
            or not all(isinstance(item, str) and item.strip() for item in candidate_tests)
        ):
            last_error = GenerationError("DeepSeek 返回结果中缺少至少两个 acceptance_tests")
            correction = str(last_error)
            continue
        candidate, compatibility_warnings = _normalize_lvgl_code(candidate)
        try:
            api_warnings, api_usage = _validate_api_summaries(candidate)
            warnings = (
                compatibility_warnings
                + _validate_code(candidate)
                + _validate_interaction_contract(candidate, request.prompt)
                + api_warnings
            )
            code = candidate
            acceptance_tests = [str(item) for item in candidate_tests]
            last_error = None
            break
        except GenerationError as exc:
            last_error = exc
            correction = str(exc)
    if last_error is not None or not code:
        raise GenerationError(f"DeepSeek 连续 3 次生成未通过检查：{last_error}")
    manifest = _manifest(request)
    manifest["activities"][0]["entrypoint"] = "assets/main.py"
    summary = str(generated.get("summary") or f"已生成 {request.display_name}")
    mpk = _build_mpk(request.package_name, manifest, code)
    mpk_filename = f"{request.package_name}_r{request.revision}.mpk"
    generation_result = {
        "schema_version": "mpos-gen-app-web-v1",
        "phase": "mpos-gen-app-web",
        "result": "success",
        "mode": "create" if not request.previous_code else "repair",
        "summary": summary,
        "model": model,
        "app": {
            "fullname": request.package_name,
            "publisher": request.publisher,
            "version": request.version,
            "name": request.display_name,
        },
        "files_written": ["MANIFEST.JSON", "assets/main.py"],
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
    )
