import unittest

from app.models import RequirementChatRequest, RequirementMessage
from app.requirements_chat import (
    _fallback_result,
    _normalize_payload,
    _normalize_result,
    _parse_json_message,
    _synthesize_refined_prompt,
)


class RequirementChatTests(unittest.TestCase):
    def request(self, finalize: bool = False) -> RequirementChatRequest:
        return RequirementChatRequest(
            locale="zh-CN",
            draft_prompt="做一个番茄钟",
            messages=[
                RequirementMessage(role="user", content="做一个番茄钟")
            ],
            finalize=finalize,
        )

    def test_parser_accepts_json_fence(self) -> None:
        parsed = _parse_json_message(
            {
                "content": (
                    "```json\n"
                    '{"assistant_message":"工作时长需要多久？",'
                    '"ready":false,"missing_fields":["duration"],"brief":{}}\n'
                    "```"
                )
            }
        )
        self.assertFalse(parsed["ready"])
        self.assertEqual(parsed["missing_fields"], ["duration"])

    def test_parser_accepts_prose_and_multipart_content(self) -> None:
        parsed = _parse_json_message(
            {
                "content": [
                    {"type": "text", "text": "需求建议如下：\n"},
                    {
                        "type": "text",
                        "text": (
                            '{"assistant_message":"希望播放什么声音？",'
                            '"ready":false,"missing_fields":["sound"],"brief":{}}'
                        ),
                    },
                ]
            }
        )
        self.assertEqual(parsed["assistant_message"], "希望播放什么声音？")

    def test_parser_accepts_tool_call_arguments(self) -> None:
        parsed = _parse_json_message(
            {
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "arguments": (
                                '{"question":"希望播放什么声音？",'
                                '"is_ready":false,"missing":["sound"]}'
                            )
                        }
                    }
                ],
            }
        )
        normalized = _normalize_payload(parsed)
        self.assertEqual(normalized["assistant_message"], "希望播放什么声音？")
        self.assertEqual(normalized["missing_fields"], ["sound"])

    def test_normalize_accepts_nested_aliases(self) -> None:
        normalized = _normalize_payload(
            {
                "result": {
                    "reply": "需求已经完成。",
                    "complete": True,
                    "final_prompt": "制作一个整点报时应用。",
                    "summary": {"goal": "整点报时"},
                }
            }
        )
        self.assertTrue(normalized["ready"])
        self.assertEqual(normalized["refined_prompt"], "制作一个整点报时应用。")
        self.assertEqual(normalized["brief"], {"goal": "整点报时"})

    def test_finalize_uses_brief_to_enrich_draft_prompt(self) -> None:
        result = _normalize_result(
            {
                "assistant_message": "需求已整理。",
                "ready": False,
                "refined_prompt": "",
                "brief": {"goal": "专注计时"},
            },
            request=self.request(finalize=True),
            model="deepseek-test",
        )
        self.assertTrue(result.ready)
        self.assertIn("做一个番茄钟", result.refined_prompt)
        self.assertIn("产品目标：专注计时", result.refined_prompt)

    def test_normalize_limits_missing_fields(self) -> None:
        result = _normalize_result(
            {
                "assistant_message": "请补充。",
                "ready": False,
                "missing_fields": list("abcdefgh"),
            },
            request=self.request(),
            model="deepseek-test",
        )
        self.assertEqual(len(result.missing_fields), 6)

    def test_fallback_keeps_conversation_usable(self) -> None:
        result = _fallback_result(self.request(), model="deepseek-test")
        self.assertFalse(result.ready)
        self.assertIn("继续", result.assistant_message)
        self.assertTrue(result.missing_fields)

    def test_finalize_fallback_returns_draft(self) -> None:
        result = _fallback_result(
            self.request(finalize=True),
            model="deepseek-test",
        )
        self.assertTrue(result.ready)
        self.assertEqual(result.refined_prompt, "做一个番茄钟")

    def test_refined_prompt_keeps_confirmed_answers(self) -> None:
        request = RequirementChatRequest(
            locale="zh-CN",
            draft_prompt="做一个计算器",
            messages=[
                RequirementMessage(role="user", content="做一个计算器"),
                RequirementMessage(
                    role="assistant",
                    content="是否支持连续运算？",
                ),
                RequirementMessage(role="user", content="支持连续运算"),
                RequirementMessage(
                    role="assistant",
                    content="是否显示历史记录？",
                ),
                RequirementMessage(role="user", content="显示最近五条"),
            ],
            finalize=True,
        )
        prompt = _synthesize_refined_prompt(request)
        self.assertIn("是否支持连续运算", prompt)
        self.assertIn("支持连续运算", prompt)
        self.assertIn("显示最近五条", prompt)

    def test_ready_result_does_not_fall_back_to_original_prompt(self) -> None:
        request = RequirementChatRequest(
            locale="zh-CN",
            draft_prompt="做一个计算器",
            messages=[
                RequirementMessage(role="user", content="做一个计算器"),
                RequirementMessage(role="assistant", content="是否支持连续运算？"),
                RequirementMessage(role="user", content="支持"),
            ],
            finalize=True,
        )
        result = _normalize_result(
            {
                "ready": True,
                "refined_prompt": "做一个计算器",
                "brief": {},
            },
            request=request,
            model="deepseek-test",
        )
        self.assertNotEqual(result.refined_prompt, request.draft_prompt)
        self.assertIn("是否支持连续运算", result.refined_prompt)


if __name__ == "__main__":
    unittest.main()
