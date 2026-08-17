"""Interaction, product and visual contract checks for generated App code.

Pure functions over the generated source: each returns human-readable findings
or raises :class:`GenerationError` when the App is not acceptable. Kept out of
``generator`` so the generation pipeline stays readable.
"""

from __future__ import annotations

import ast

from .generation_errors import GenerationError


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
