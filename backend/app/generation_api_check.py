"""MPOS/LVGL API summary validation and LVGL code normalisation.

The API summaries are the authority on which LVGL widgets, methods and MPOS
root exports actually exist in the pinned build, so generated code is checked
against them instead of against what the model believes the API looks like.

Split out of ``generator`` to keep the generation pipeline readable.
"""

from __future__ import annotations

import ast
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .generation_errors import ApiValidationError


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
