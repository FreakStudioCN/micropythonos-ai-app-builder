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
from typing import Any, Callable

import httpx

from .capabilities import capability_index, capability_versions
from .capability_policy import evaluate_generated_app
from .generation_errors import (
    ApiValidationError,
    GenerationError,
    UpstreamGenerationError,
)
from .generation_prompts import (
    GENERAL_UI_BLUEPRINT,
    INTERACTION_PROMPT,
    SHOOTER_UI_BLUEPRINT,
    SYSTEM_PROMPT,
    VISUAL_REQUIREMENTS,
    capability_contract_prompt,
)
from .generation_api_check import (
    _normalize_lvgl_code,
    _validate_api_summaries,
)
from .generation_validators import (
    _is_shooter_prompt,
    _validate_interaction_contract,
    _validate_product_contract,
    _validate_visual_contract,
)
from .models import GenerateRequest, GenerateResponse, GeneratedFile


GenerationAttemptSink = Callable[[dict[str, Any]], None]


def _build_user_prompt(request: GenerateRequest, correction: str = "") -> str:
    user_prompt = (
        "请生成 JSON。用户需求：\n"
        f"{request.prompt}\n\n"
        f"显示名：{request.display_name}\n"
        f"包名：{request.package_name}\n"
        "入口文件固定为 app.py，入口类固定为 GeneratedApp。\n\n"
        f"{VISUAL_REQUIREMENTS}\n{GENERAL_UI_BLUEPRINT}"
        + capability_contract_prompt(
            request.required_capabilities,
            accessories=request.required_accessories,
        )
        # Asked of every interactive App, because the focus/keypad check runs
        # on every interactive App. A rule only enforced is a rule that fails.
        + INTERACTION_PROMPT
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


async def _call_deepseek_legacy(
    request: GenerateRequest,
    correction: str = "",
    timeout_seconds: float | None = None,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    key, base_url, model = _settings()
    user_prompt = _build_user_prompt(request, correction)
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
        **(
            {"thinking": {"type": "disabled"}}
            if _ACTIVE_PROVIDER_CONFIG.get() is None
            or _ACTIVE_PROVIDER_CONFIG.get().id.startswith("deepseek_")
            else {}
        ),
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    request_timeout = timeout_seconds
    if request_timeout is None:
        request_timeout = _bounded_float_env(
            "AI_READ_TIMEOUT_SECONDS",
            default=60.0,
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
    async with httpx.AsyncClient(timeout=timeout) as client:
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
        generated = _parse_model_json(message)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise GenerationError("DeepSeek 没有返回可解析的生成结果") from exc
    selected_model = str(body.get("model") or model)
    model_meta: dict[str, Any] = {"model": selected_model}
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
    "deepseek_primary",
    "deepseek_secondary",
    "aigocode",
)


class _ProviderHTTPStatusError(Exception):
    """HTTP failure stripped of response body, URL, and headers."""

    def __init__(self, status_code: int, request_id: str | None = None) -> None:
        self.status_code = status_code
        self.request_id = request_id
        message = f"AI provider returned HTTP {status_code}"
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
    primary = AIProviderConfig(
        id="deepseek_primary",
        label="DeepSeek Primary",
        api_key=_first_env("DEEPSEEK_PRIMARY_API_KEY", "DEEPSEEK_API_KEY"),
        base_url=_first_env(
            "DEEPSEEK_PRIMARY_BASE_URL",
            "DEEPSEEK_BASE_URL",
            default="https://api.deepseek.com",
        ).rstrip("/"),
        model=_first_env(
            "DEEPSEEK_PRIMARY_MODEL",
            "DEEPSEEK_MODEL",
            default="deepseek-v4-flash",
        ),
    )
    secondary = AIProviderConfig(
        id="deepseek_secondary",
        label="DeepSeek Secondary",
        api_key=_first_env(
            "DEEPSEEK_SECONDARY_API_KEY",
            "DEEPSEEK_BACKUP_API_KEY",
        ),
        base_url=_first_env(
            "DEEPSEEK_SECONDARY_BASE_URL",
            "DEEPSEEK_BACKUP_BASE_URL",
            default="https://api.deepseek.com",
        ).rstrip("/"),
        model=_first_env(
            "DEEPSEEK_SECONDARY_MODEL",
            "DEEPSEEK_BACKUP_MODEL",
            default="deepseek-v4-flash",
        ),
    )
    aigocode = AIProviderConfig(
        id="aigocode",
        label="AIGoCode GLM",
        api_key=_first_env("AIGOCODE_API_KEY"),
        base_url=_first_env(
            "AIGOCODE_BASE_URL",
            default="https://api.aigocode.app/v1",
        ).rstrip("/"),
        model=_first_env("AIGOCODE_MODEL", default="glm-4.7"),
    )
    return {item.id: item for item in (primary, secondary, aigocode)}


def provider_metadata() -> list[dict[str, str | bool]]:
    configs = _provider_configs()
    configured_any = any(config.configured for config in configs.values())
    providers: list[dict[str, str | bool]] = [
        {
            "id": "auto",
            "label": "Automatic failover",
            "configured": configured_any,
            "model": "",
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
    config = _ACTIVE_PROVIDER_CONFIG.get() or _provider_configs()["deepseek_primary"]
    if not config.configured:
        raise UpstreamGenerationError(
            "AI_UPSTREAM_UNAVAILABLE",
            f"AI provider {config.id} is not configured",
            retryable=False,
            failover_allowed=False,
            details={"provider": config.id},
        )
    return config.api_key, config.base_url, config.model


def _provider_order() -> list[str]:
    raw = os.getenv(
        "AI_PROVIDER_ORDER",
        "deepseek_primary,deepseek_secondary,aigocode",
    )
    ordered: list[str] = []
    for provider_id in raw.split(","):
        provider_id = provider_id.strip()
        if provider_id in AI_PROVIDER_IDS and provider_id not in ordered:
            ordered.append(provider_id)
    return ordered or list(AI_PROVIDER_IDS)


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
        default=30.0,
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


def _provider_candidates(requested_provider: str) -> tuple[list[AIProviderConfig], bool]:
    configs = _provider_configs()
    if requested_provider != "auto":
        config = configs.get(requested_provider)
        if config is None:
            raise UpstreamGenerationError(
                "AI_UPSTREAM_UNAVAILABLE",
                f"Unknown AI provider {requested_provider}",
                retryable=False,
                failover_allowed=False,
                details={"provider": requested_provider},
            )
        if not config.configured:
            raise UpstreamGenerationError(
                "AI_UPSTREAM_UNAVAILABLE",
                f"AI provider {requested_provider} is not configured",
                retryable=False,
                failover_allowed=False,
                details={"provider": requested_provider},
            )
        if _circuit_open(requested_provider):
            raise UpstreamGenerationError(
                "AI_UPSTREAM_UNAVAILABLE",
                f"AI provider {requested_provider} is temporarily unavailable",
                retryable=True,
                failover_allowed=False,
                details={"provider": requested_provider},
            )
        return [config], True

    candidates = [
        configs[provider_id]
        for provider_id in _provider_order()
        if configs[provider_id].configured and not _circuit_open(provider_id)
    ]
    if not candidates:
        raise UpstreamGenerationError(
            "AI_UPSTREAM_UNAVAILABLE",
            "No configured AI provider is currently available",
            retryable=True,
            failover_allowed=False,
            details={"attempted_providers": []},
        )
    return candidates, False


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
        default=2,
        minimum=0,
        maximum=5,
    )
    read_timeout = _bounded_float_env(
        "AI_READ_TIMEOUT_SECONDS",
        default=60.0,
        minimum=0.1,
        maximum=600.0,
        fallbacks=("DEEPSEEK_REQUEST_TIMEOUT_SECONDS",),
    )
    backoff = _bounded_float_env(
        "AI_RETRY_BACKOFF_SECONDS",
        default=0.5,
        minimum=0.0,
        maximum=30.0,
    )
    attempts: list[dict[str, Any]] = []

    for attempt in range(1, retries + 2):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise UpstreamGenerationError(
                "AI_UPSTREAM_TIMEOUT",
                f"AI provider {config.id} exceeded the overall timeout",
                retryable=True,
                failover_allowed=True,
                details={
                    "provider": config.id,
                    "attempts": attempt - 1,
                    "provider_attempts": attempts,
                },
            )

        attempts_remaining = retries + 2 - attempt
        attempt_timeout = min(read_timeout, remaining / attempts_remaining)
        upstream_request_id: str | None = None
        token = _ACTIVE_PROVIDER_CONFIG.set(config)
        try:
            generated, model, model_meta = await asyncio.wait_for(
                _call_deepseek_legacy(
                    request,
                    correction=correction,
                    timeout_seconds=attempt_timeout,
                ),
                timeout=attempt_timeout,
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
                        f"AI provider {config.id} rejected its credentials or permissions"
                    )
                elif status_code == 404:
                    error_code = "AI_UPSTREAM_CONFIG_ERROR"
                    error_message = (
                        f"AI provider {config.id} endpoint or model is not configured"
                    )
                else:
                    error_code = "AI_UPSTREAM_REJECTED"
                    error_message = f"AI provider {config.id} rejected the request"
                attempt_record: dict[str, Any] = {
                    "provider": config.id,
                    "attempt": attempt,
                    "outcome": f"http_{status_code}",
                    "status_code": status_code,
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
                }
            )
            return generated, model, model_meta, attempts
        finally:
            _ACTIVE_PROVIDER_CONFIG.reset(token)

        attempt_record = {
            "provider": config.id,
            "attempt": attempt,
            "outcome": outcome,
        }
        if status_code is not None:
            attempt_record["status_code"] = status_code
        if upstream_request_id:
            attempt_record["request_id"] = upstream_request_id
        attempts.append(attempt_record)
        if attempt > retries:
            message = (
                f"AI provider {config.id} timed out"
                if upstream_code == "AI_UPSTREAM_TIMEOUT"
                else f"AI provider {config.id} is unavailable"
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
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    requested_provider = getattr(request, "ai_provider", "auto") or "auto"
    candidates, explicit = _provider_candidates(requested_provider)
    overall_timeout = _bounded_float_env(
        "AI_OVERALL_TIMEOUT_SECONDS",
        default=120.0,
        minimum=1.0,
        maximum=900.0,
        fallbacks=("DEEPSEEK_GENERATION_BUDGET_SECONDS",),
    )
    if timeout_seconds is not None:
        try:
            requested_timeout = float(timeout_seconds)
        except (TypeError, ValueError):
            requested_timeout = overall_timeout
        overall_timeout = min(overall_timeout, max(0.05, requested_timeout))
    deadline = time.monotonic() + overall_timeout
    attempted_providers: list[str] = []
    provider_attempts: list[dict[str, Any]] = []
    last_error: UpstreamGenerationError | None = None

    for index, config in enumerate(candidates):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        providers_remaining = len(candidates) - index
        provider_deadline = time.monotonic() + (remaining / providers_remaining)
        if not _claim_provider_slot(config.id):
            if explicit:
                raise UpstreamGenerationError(
                    "AI_UPSTREAM_UNAVAILABLE",
                    f"AI provider {config.id} is temporarily unavailable",
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
        }
        generated = dict(generated)
        generated["ai_routing"] = routing
        model_meta = {**model_meta, **routing}
        return generated, model, model_meta

    code = last_error.code if last_error else "AI_UPSTREAM_UNAVAILABLE"
    message = (
        "All configured AI providers timed out"
        if code == "AI_UPSTREAM_TIMEOUT"
        else "All configured AI providers are unavailable"
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
        },
    )


def _reject_non_portable_capabilities(request: GenerateRequest) -> None:
    """Stop before spending a model call on hardware MPOS cannot expose.

    Generating "something that looks like GPS" would be fabricating code for an
    API that does not exist, so this fails loudly and attributes the gap to
    MicroPythonOS rather than to the App.
    """
    blocking = capability_index().blocking(request.required_capabilities)
    if not blocking:
        return
    first = blocking[0]
    raise ApiValidationError(
        first.blocking_error_code(),
        f"MicroPythonOS 暂无 {first.name} 的可移植 App 能力 API：{first.reason}",
    )


async def generate_app(
    request: GenerateRequest,
    *,
    attempt_sink: GenerationAttemptSink | None = None,
) -> GenerateResponse:
    _reject_non_portable_capabilities(request)
    budget_seconds = _bounded_float_env(
        "AI_OVERALL_TIMEOUT_SECONDS",
        default=120.0,
        minimum=8.0,
        maximum=900.0,
        fallbacks=("DEEPSEEK_GENERATION_BUDGET_SECONDS",),
    )
    request_timeout_seconds = _bounded_float_env(
        "AI_READ_TIMEOUT_SECONDS",
        default=60.0,
        minimum=3.0,
        maximum=600.0,
        fallbacks=("DEEPSEEK_REQUEST_TIMEOUT_SECONDS",),
    )
    max_attempts = max(
        1,
        min(2, int(os.getenv("DEEPSEEK_MAX_ATTEMPTS", "2"))),
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
    model_meta: dict[str, Any] = {}
    attempts_used = 0
    for attempt in range(1, max_attempts + 1):
        remaining = deadline - time.monotonic()
        if remaining < 2.0:
            last_error = GenerationError(
                f"生成已达到 {budget_seconds:.0f} 秒时间预算"
            )
            break
        attempts_used = attempt
        attempts_after_this = max_attempts - attempts_used
        attempts_including_this = attempts_after_this + 1
        call_timeout = min(
            request_timeout_seconds,
            max(2.0, remaining / attempts_including_this),
        )
        model_meta = {}
        try:
            generated, model, model_meta = await _call_deepseek(
                request,
                correction,
                timeout_seconds=call_timeout,
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
            _emit_generation_attempt(
                attempt_sink,
                attempt=attempt,
                status="model_error",
                error=exc,
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
                f"DeepSeek 返回结果中缺少可识别的 App 源码{field_hint}"
            )
            _emit_generation_attempt(
                attempt_sink,
                attempt=attempt,
                status="validation_failed",
                error=last_error,
                model_meta=model_meta,
            )
            correction = _build_correction(last_error, attempt=attempt)
            continue
        if (
            not isinstance(candidate_tests, list)
            or len(candidate_tests) < 2
            or not all(isinstance(item, str) and item.strip() for item in candidate_tests)
        ):
            last_error = GenerationError("DeepSeek 返回结果中缺少至少两个 acceptance_tests")
            _emit_generation_attempt(
                attempt_sink,
                attempt=attempt,
                status="validation_failed",
                candidate=candidate,
                error=last_error,
                model_meta=model_meta,
            )
            correction = _build_correction(last_error, candidate, attempt)
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
            # Mandatory gate: board imports and direct hardware constructors
            # are a generator fault, so a violation fails this attempt and is
            # corrected instead of shipped.
            # The gate shells out to the vendored policy script with a 30s
            # timeout. Running that inline would stall the event loop — and
            # therefore every other request — once per validation attempt.
            capability_verdict = await asyncio.to_thread(
                evaluate_generated_app,
                candidate,
                capabilities=request.required_capabilities,
                app_fullname=request.package_name,
                # Never derived from detected accessory phrases. The spec only
                # allows the low-level exception after a *confirmed* accessory
                # handoff, and a keyword match is not a confirmation — wiring
                # it here silently disabled the whole board-hardware gate.
                allow_direct_hardware=False,
            )
            capability_warnings = capability_verdict["warnings"]
            if not capability_verdict["passed"]:
                raise GenerationError(
                    "生成代码直接访问了板级硬件，必须改用 MicroPythonOS Manager："
                    + "；".join(
                        str(item["details"].get("symbol"))
                        for item in capability_verdict["errors"]
                    )
                )
            warnings = (
                compatibility_warnings
                + code_warnings
                + interaction_warnings
                + product_warnings
                + visual_warnings
                + api_warnings
                + capability_verdict["warnings"]
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
            correction = _build_correction(exc, candidate, attempt)
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
        "provider": str(model_meta.get("provider") or ""),
        "failover_used": bool(model_meta.get("failover_used", False)),
        "attempted_providers": model_meta.get("attempted_providers", []),
        "provider_attempts": model_meta.get("provider_attempts", []),
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
        provider=str(model_meta.get("provider") or ""),
        failover_used=bool(model_meta.get("failover_used", False)),
        attempted_providers=model_meta.get("attempted_providers", []),
        provider_attempts=model_meta.get("provider_attempts", []),
        warnings=warnings,
        acceptance_tests=acceptance_tests,
        mpk_filename=mpk_filename,
        revision=request.revision,
        required_capabilities=list(request.required_capabilities),
        required_accessories=list(request.required_accessories),
        runtime_fallbacks=dict(request.runtime_fallbacks),
        physical_validation_required=request.physical_validation_required,
        capability_contracts=capability_index().public_contracts(
            request.required_capabilities
        ),
        capability_warnings=capability_warnings,
        skill_commit=capability_versions()["skill_commit"],
        mpos_commit=capability_versions()["mpos_commit"],
        board_capabilities_schema=capability_versions()[
            "board_capabilities_schema"
        ],
        prompt_normalized_zh=prompt_normalized_zh,
        prompt_normalized_en=prompt_normalized_en,
        store_metadata=store_metadata,
    )
