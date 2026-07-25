import json
import os
import re
from typing import Any

import httpx

from .models import RequirementChatRequest, RequirementChatResponse


class RequirementChatError(RuntimeError):
    pass


SYSTEM_PROMPT = """
You are the product requirement assistant inside Blockless-Make-APP.
Your job is to turn a beginner's rough idea into a concrete MicroPythonOS App
requirement through a short, friendly conversation.

Rules:
- Do not generate source code.
- Ask only one high-value question per response.
- Never ask for information the user already supplied.
- Prefer multiple-choice examples inside the question so beginners can answer easily.
- Normally finish after 2-4 user answers. If the idea is already specific enough,
  finish immediately.
- The target screen is usually 320x240 and touch-first. Apps may run in WebAssembly
  preview and on an ESP32 display.
- Clarify only what materially affects the product: goal and audience, core
  functions, interaction/state behavior, visual style, and hardware needs.
- Do not force hardware questions when the app does not need sensors or GPIO.
- When finalize=true, stop asking and produce the best complete requirement from
  the available information.
- Reply in the requested locale.
- Return JSON only, with no Markdown.

JSON schema:
{
  "assistant_message": "one concise question, or a concise completion message",
  "ready": false,
  "refined_prompt": "",
  "missing_fields": ["the most important missing item"],
  "brief": {
    "goal": "",
    "target_users": "",
    "core_features": [],
    "interaction": "",
    "visual_style": "",
    "hardware": "",
    "constraints": []
  }
}

When ready=true, refined_prompt must be a self-contained implementation requirement
that can replace the user's original prompt. It must explicitly describe the core
features, controls/interactions, important states, and visual direction. Keep it
under 1200 Chinese characters or 1800 English characters.
"""


def _settings() -> tuple[str, str, str]:
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash").strip()
    if not key or key == "replace_with_your_deepseek_api_key":
        raise RequirementChatError(
            "未配置 DEEPSEEK_API_KEY，请先在 backend/.env 中填写 DeepSeek API Key。"
        )
    return key, base_url, model


def _message_text(value: Any) -> str:
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


def _parse_json_message(message: dict[str, Any]) -> dict[str, Any]:
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
    raise RequirementChatError("DeepSeek 没有返回可解析的需求对话结果")


def _normalize_payload(data: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(data)
    containers: list[dict[str, Any]] = [data]
    for key in ("result", "data", "output", "response", "requirements"):
        value = data.get(key)
        if isinstance(value, dict):
            containers.append(value)

    aliases = {
        "assistant_message": ("assistant_message", "message", "question", "reply"),
        "ready": ("ready", "is_ready", "complete", "completed"),
        "refined_prompt": (
            "refined_prompt",
            "final_prompt",
            "prompt",
            "requirement",
            "specification",
        ),
        "missing_fields": (
            "missing_fields",
            "missing",
            "questions_remaining",
        ),
        "brief": ("brief", "summary", "requirement_brief"),
    }
    for target, source_keys in aliases.items():
        if target in normalized:
            continue
        for container in containers:
            found = False
            for source_key in source_keys:
                if source_key in container:
                    normalized[target] = container[source_key]
                    found = True
                    break
            if found:
                break
    return normalized


def _conversation_clarifications(
    request: RequirementChatRequest,
) -> list[tuple[str, str]]:
    clarifications: list[tuple[str, str]] = []
    pending_question = ""
    initial_prompt_skipped = False
    for item in request.messages:
        content = item.content.strip()
        if not content:
            continue
        if item.role == "assistant":
            pending_question = content
            continue
        if not initial_prompt_skipped:
            initial_prompt_skipped = True
            if content == request.draft_prompt.strip():
                continue
        if pending_question:
            clarifications.append((pending_question, content))
            pending_question = ""
    return clarifications


def _synthesize_refined_prompt(
    request: RequirementChatRequest,
    brief: dict[str, Any] | None = None,
) -> str:
    base = request.draft_prompt.strip()
    sections: list[str] = [base] if base else []
    brief = brief if isinstance(brief, dict) else {}

    if request.locale == "zh-CN":
        labels = {
            "goal": "产品目标",
            "target_users": "目标用户",
            "core_features": "核心功能",
            "interaction": "交互方式",
            "visual_style": "视觉风格",
            "hardware": "硬件需求",
            "constraints": "约束条件",
        }
        detail_lines: list[str] = []
        for key, label in labels.items():
            value = brief.get(key)
            if isinstance(value, list):
                text = "、".join(str(item).strip() for item in value if str(item).strip())
            else:
                text = str(value or "").strip()
            if text:
                detail_lines.append(f"- {label}：{text}")
        if detail_lines:
            sections.append("需求摘要：\n" + "\n".join(detail_lines))

        clarifications = _conversation_clarifications(request)
        if clarifications:
            lines = [
                f"- 关于“{question[:180]}”：用户确认“{answer[:240]}”"
                for question, answer in clarifications
            ]
            sections.append("对话中确认的详细需求：\n" + "\n".join(lines))
    else:
        labels = {
            "goal": "Goal",
            "target_users": "Target users",
            "core_features": "Core features",
            "interaction": "Interaction",
            "visual_style": "Visual style",
            "hardware": "Hardware",
            "constraints": "Constraints",
        }
        detail_lines = []
        for key, label in labels.items():
            value = brief.get(key)
            if isinstance(value, list):
                text = ", ".join(str(item).strip() for item in value if str(item).strip())
            else:
                text = str(value or "").strip()
            if text:
                detail_lines.append(f"- {label}: {text}")
        if detail_lines:
            sections.append("Requirement summary:\n" + "\n".join(detail_lines))
        clarifications = _conversation_clarifications(request)
        if clarifications:
            lines = [
                f'- For "{question[:180]}", the user confirmed: "{answer[:240]}"'
                for question, answer in clarifications
            ]
            sections.append("Confirmed conversation details:\n" + "\n".join(lines))
    return "\n\n".join(section for section in sections if section).strip()[:4000]


def _normalize_result(
    data: dict[str, Any],
    *,
    request: RequirementChatRequest,
    model: str,
) -> RequirementChatResponse:
    data = _normalize_payload(data)
    ready = bool(data.get("ready")) or request.finalize
    assistant_message = str(data.get("assistant_message") or "").strip()
    if not assistant_message:
        assistant_message = (
            "需求已经整理完成。"
            if request.locale == "zh-CN" and ready
            else "The requirement is ready."
            if ready
            else "你希望用户最重要的操作是什么？"
            if request.locale == "zh-CN"
            else "What is the most important action the user should be able to perform?"
        )
    brief = data.get("brief")
    if not isinstance(brief, dict):
        brief = {}
    missing_fields = data.get("missing_fields")
    if not isinstance(missing_fields, list):
        missing_fields = []
    refined_prompt = str(data.get("refined_prompt") or "").strip()
    if ready:
        synthesized = _synthesize_refined_prompt(request, brief)
        draft_compact = re.sub(r"\s+", "", request.draft_prompt)
        refined_compact = re.sub(r"\s+", "", refined_prompt)
        if not refined_prompt or refined_compact == draft_compact:
            refined_prompt = synthesized
        elif _conversation_clarifications(request):
            clarification_section = _synthesize_refined_prompt(request, {}).removeprefix(
                request.draft_prompt.strip()
            ).strip()
            if clarification_section:
                refined_prompt = (
                    refined_prompt.rstrip()
                    + "\n\n"
                    + clarification_section
                )[:4000]
    return RequirementChatResponse(
        assistant_message=assistant_message[:4000],
        ready=ready,
        refined_prompt=refined_prompt[:4000],
        missing_fields=[str(item)[:120] for item in missing_fields[:6]],
        brief=brief,
        model=model,
    )


def _fallback_result(
    request: RequirementChatRequest,
    *,
    model: str,
) -> RequirementChatResponse:
    if request.finalize:
        message = (
            "我已根据当前对话整理需求。"
            if request.locale == "zh-CN"
            else "I organized the requirement from the current conversation."
        )
        return RequirementChatResponse(
            assistant_message=message,
            ready=True,
            refined_prompt=_synthesize_refined_prompt(request),
            missing_fields=[],
            brief={},
            model=model,
        )
    message = (
        "这一步我没有完全读懂。请直接说你的选择，或回复“按你的建议”，我会继续帮你完善需求。"
        if request.locale == "zh-CN"
        else (
            "I did not fully understand that answer. State your choice directly, "
            'or reply "use your suggestion" and I will continue.'
        )
    )
    return RequirementChatResponse(
        assistant_message=message,
        ready=False,
        refined_prompt="",
        missing_fields=["当前问题的选择"],
        brief={},
        model=model,
    )


async def clarify_requirements(
    request: RequirementChatRequest,
) -> RequirementChatResponse:
    key, base_url, model = _settings()
    conversation = [
        {"role": item.role, "content": item.content}
        for item in request.messages[-20:]
    ]
    context = {
        "locale": request.locale,
        "draft_prompt": request.draft_prompt,
        "finalize": request.finalize,
        "user_answer_count": sum(
            1 for item in request.messages if item.role == "user"
        ),
    }
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "system",
            "content": (
                "Current conversation context:\n"
                + json.dumps(context, ensure_ascii=False)
            ),
        },
        *conversation,
    ]
    payload = {
        "model": model,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "temperature": 0.3,
        "max_tokens": 1800,
        "thinking": {"type": "disabled"},
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    last_model = model
    async with httpx.AsyncClient(timeout=60) as client:
        for attempt in range(2):
            attempt_payload = dict(payload)
            if attempt:
                attempt_payload["messages"] = [
                    *messages,
                    {
                        "role": "system",
                        "content": (
                            "Your previous response could not be parsed. Return exactly "
                            "one complete JSON object matching the schema. Do not use "
                            "Markdown, prose outside JSON, comments, or trailing commas."
                        ),
                    },
                ]
                attempt_payload["temperature"] = 0.1
            try:
                response = await client.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json=attempt_payload,
                )
            except httpx.HTTPError as exc:
                if attempt == 0:
                    continue
                raise RequirementChatError(f"无法连接 DeepSeek：{exc}") from exc
            if response.status_code >= 400:
                if attempt == 0:
                    continue
                raise RequirementChatError(
                    f"DeepSeek 返回 {response.status_code}：{response.text[:500]}"
                )
            try:
                body = response.json()
                message = body["choices"][0]["message"]
                if not isinstance(message, dict):
                    raise TypeError("message is not an object")
                last_model = str(body.get("model") or model)
                data = _parse_json_message(message)
            except (
                ValueError,
                KeyError,
                IndexError,
                TypeError,
                RequirementChatError,
            ):
                continue
            return _normalize_result(
                data,
                request=request,
                model=last_model,
            )
    return _fallback_result(request, model=last_model)
