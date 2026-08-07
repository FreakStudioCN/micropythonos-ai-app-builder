import json
import os
import secrets
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.generator import (
    UpstreamGenerationError,
    _call_deepseek,
    _classify_request_complexity,
    _record_provider_failure,
    _reset_provider_circuits,
    provider_metadata,
)
from app.models import GenerateRequest, SessionCreateRequest


class ProviderRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _reset_provider_circuits()
        self.deepseek_key = secrets.token_urlsafe(32)
        self.kimi_key = secrets.token_urlsafe(32)
        self.zhipu_key = secrets.token_urlsafe(32)

    def _env(self, **overrides: str) -> dict[str, str]:
        values = {
            "DEEPSEEK_API_KEY": self.deepseek_key,
            "DEEPSEEK_BASE_URL": "https://deepseek.invalid/v1",
            "DEEPSEEK_MODEL": "deepseek-test",
            "KIMI_API_KEY": self.kimi_key,
            "KIMI_BASE_URL": "https://kimi.invalid/v1",
            "KIMI_MODEL": "kimi-test",
            "KIMI_K27_MODEL": "kimi-code-test",
            "ZHIPU_API_KEY": self.zhipu_key,
            "ZHIPU_BASE_URL": "https://zhipu.invalid/v4",
            "ZHIPU_GLM52_MODEL": "glm52-test",
            "AI_PROVIDER_ORDER_SIMPLE": "zhipu_glm52,kimi_k27,deepseek",
            "AI_PROVIDER_ORDER_STANDARD": "zhipu_glm52,kimi_k27,deepseek",
            "AI_PROVIDER_ORDER_COMPLEX": "zhipu_glm52,kimi_k27,deepseek",
            "AI_PROVIDER_ORDER_REVISION": "zhipu_glm52,kimi_k27,deepseek",
            "AI_PROVIDER_ORDER_REPAIR": "zhipu_glm52,kimi_k27,deepseek",
            "AI_UPSTREAM_MAX_RETRIES": "0",
            "AI_RETRY_BACKOFF_SECONDS": "0",
            "AI_CONNECT_TIMEOUT_SECONDS": "1",
            "AI_READ_TIMEOUT_SECONDS": "2",
            # Most routing tests mock the legacy one-shot response. Streaming
            # behavior has a dedicated test below.
            "AI_STREAM_RESPONSES": "0",
            "AI_OVERALL_TIMEOUT_SECONDS": "5",
            "AI_PROVIDER_CIRCUIT_FAILURE_THRESHOLD": "2",
            "AI_PROVIDER_CIRCUIT_COOLDOWN_SECONDS": "30",
        }
        values.update(overrides)
        return values

    @staticmethod
    def _response(status_code: int, *, model: str = "served-model") -> httpx.Response:
        request = httpx.Request("POST", "https://provider.invalid/chat/completions")
        if status_code >= 400:
            return httpx.Response(
                status_code,
                request=request,
                headers={"x-request-id": "request-safe"},
                json={"error": {"message": "private upstream body"}},
            )
        generated = {
            "summary": "test app",
            "app_code": "print('ok')",
            "acceptance_tests": ["starts", "responds"],
        }
        return httpx.Response(
            status_code,
            request=request,
            json={
                "id": "request-test",
                "model": model,
                "choices": [{"message": {"content": json.dumps(generated)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            },
        )

    @staticmethod
    def _client(*responses: object) -> AsyncMock:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False
        client.post.side_effect = list(responses)
        return client

    async def test_provider_metadata_is_safe(self) -> None:
        env = self._env()
        with patch.dict(os.environ, env, clear=True):
            providers = provider_metadata()

        self.assertEqual(
            [item["id"] for item in providers],
            ["auto", "deepseek", "kimi", "kimi_k27", "zhipu_glm52"],
        )
        self.assertTrue(all(set(item) == {"id", "label", "configured", "model"} for item in providers))
        serialized = json.dumps(providers)
        for secret in (self.deepseek_key, self.kimi_key, self.zhipu_key):
            self.assertNotIn(secret, serialized)
        self.assertNotIn("deepseek.invalid", serialized)
        self.assertNotIn("zhipu.invalid", serialized)

    async def test_explicit_zhipu_uses_only_selected_provider(self) -> None:
        client = self._client(self._response(200, model="gpt-served"))
        with patch.dict(os.environ, self._env(), clear=True), patch(
            "app.generator.httpx.AsyncClient", return_value=client
        ):
            generated, model, meta = await _call_deepseek(
                GenerateRequest(prompt="build a test app", ai_provider="zhipu_glm52")
            )

        self.assertEqual(model, "gpt-served")
        self.assertEqual(meta["provider"], "zhipu_glm52")
        self.assertFalse(meta["failover_used"])
        self.assertEqual(meta["attempted_providers"], ["zhipu_glm52"])
        self.assertEqual(generated["ai_routing"]["provider"], "zhipu_glm52")
        call = client.post.await_args
        self.assertEqual(call.args[0], "https://zhipu.invalid/v4/chat/completions")
        self.assertEqual(
            call.kwargs["json"]["thinking"],
            {"type": "disabled"},
        )
        self.assertFalse(call.kwargs["json"]["do_sample"])
        self.assertNotIn("temperature", call.kwargs["json"])

    async def test_auto_can_exclude_a_provider_rejected_by_quality_checks(self) -> None:
        client = self._client(self._response(200, model="kimi-served"))
        with patch.dict(os.environ, self._env(), clear=True), patch(
            "app.generator.httpx.AsyncClient", return_value=client
        ):
            _, _, meta = await _call_deepseek(
                GenerateRequest(prompt="build a test app", ai_provider="auto"),
                excluded_providers={"zhipu_glm52"},
            )

        self.assertEqual(meta["provider"], "kimi_k27")
        self.assertEqual(meta["attempted_providers"], ["kimi_k27"])
        self.assertEqual(
            client.post.await_args.args[0],
            "https://kimi.invalid/v1/chat/completions",
        )

    async def test_kimi_omits_unsupported_temperature(self) -> None:
        client = self._client(self._response(200, model="kimi-served"))
        with patch.dict(
            os.environ,
            self._env(KIMI_MAX_OUTPUT_TOKENS="5200"),
            clear=True,
        ), patch(
            "app.generator.httpx.AsyncClient", return_value=client
        ):
            _, _, meta = await _call_deepseek(
                GenerateRequest(prompt="build a test app", ai_provider="kimi")
            )

        self.assertEqual(meta["provider"], "kimi")
        payload = client.post.await_args.kwargs["json"]
        self.assertNotIn("temperature", payload)
        self.assertNotIn("thinking", payload)
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["max_tokens"], 10_000)

    async def test_streaming_response_preserves_complete_app_json(self) -> None:
        generated = {
            "summary": "streamed test app",
            "app_code": "import lvgl as lv\n\nclass App:\n    pass\n",
            "acceptance_tests": ["starts", "responds"],
        }
        encoded = json.dumps(generated, ensure_ascii=False)
        fragments = [encoded[:31], encoded[31:77], encoded[77:]]

        class FakeStreamingResponse:
            is_error = False

            async def aiter_lines(self):
                for index, fragment in enumerate(fragments):
                    yield "data: " + json.dumps(
                        {
                            "id": "stream-request",
                            "model": "glm-streamed",
                            "choices": [
                                {
                                    "delta": {"content": fragment},
                                    "finish_reason": (
                                        "stop" if index == len(fragments) - 1 else None
                                    ),
                                }
                            ],
                        }
                    )
                yield "data: [DONE]"

        class FakeStreamContext:
            async def __aenter__(self):
                return FakeStreamingResponse()

            async def __aexit__(self, *_: object):
                return False

        class FakeClient:
            def __init__(self) -> None:
                self.payload: dict[str, object] | None = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_: object):
                return False

            def stream(self, _method: str, _url: str, **kwargs: object):
                payload = kwargs.get("json")
                if isinstance(payload, dict):
                    self.payload = payload
                return FakeStreamContext()

        client = FakeClient()
        env = self._env(AI_STREAM_RESPONSES="1")
        with patch.dict(os.environ, env, clear=True), patch(
            "app.generator.httpx.AsyncClient", return_value=client
        ):
            result, model, meta = await _call_deepseek(
                GenerateRequest(prompt="build a test app", ai_provider="zhipu_glm52")
            )

        self.assertEqual(model, "glm-streamed")
        self.assertEqual(result["app_code"], generated["app_code"])
        self.assertEqual(meta["request_id"], "stream-request")
        self.assertIsNotNone(client.payload)
        assert client.payload is not None
        self.assertTrue(client.payload["stream"])

    async def test_auto_retries_then_fails_over(self) -> None:
        client = self._client(
            self._response(503),
            self._response(503),
            self._response(200, model="secondary-served"),
        )
        env = self._env(AI_UPSTREAM_MAX_RETRIES="1")
        with patch.dict(os.environ, env, clear=True), patch(
            "app.generator.httpx.AsyncClient", return_value=client
        ):
            _, _, meta = await _call_deepseek(
                GenerateRequest(prompt="build a test app", ai_provider="auto")
            )

        self.assertEqual(client.post.await_count, 3)
        self.assertEqual(meta["provider"], "kimi_k27")
        self.assertTrue(meta["failover_used"])
        self.assertEqual(
            meta["attempted_providers"],
            ["zhipu_glm52", "kimi_k27"],
        )
        self.assertEqual(
            [item["outcome"] for item in meta["provider_attempts"]],
            ["http_503", "http_503", "success"],
        )

    async def test_deepseek_is_only_used_after_glm_and_kimi_fail(self) -> None:
        client = self._client(
            self._response(503),
            self._response(503),
            self._response(200, model="deepseek-fallback"),
        )
        with patch.dict(os.environ, self._env(), clear=True), patch(
            "app.generator.httpx.AsyncClient", return_value=client
        ):
            _, model, meta = await _call_deepseek(
                GenerateRequest(prompt="build a test app", ai_provider="auto")
            )

        self.assertEqual(model, "deepseek-fallback")
        self.assertEqual(meta["provider"], "deepseek")
        self.assertEqual(
            meta["attempted_providers"],
            ["zhipu_glm52", "kimi_k27", "deepseek"],
        )
        self.assertTrue(meta["failover_used"])

    async def test_non_retryable_4xx_codes_do_not_fail_over(self) -> None:
        expected_codes = {
            400: "AI_UPSTREAM_REJECTED",
            401: "AI_UPSTREAM_AUTH_FAILED",
            403: "AI_UPSTREAM_AUTH_FAILED",
            404: "AI_UPSTREAM_CONFIG_ERROR",
        }
        for status_code, expected_code in expected_codes.items():
            with self.subTest(status_code=status_code):
                client = self._client(self._response(status_code))
                with patch.dict(os.environ, self._env(), clear=True), patch(
                    "app.generator.httpx.AsyncClient", return_value=client
                ):
                    with self.assertRaises(UpstreamGenerationError) as raised:
                        await _call_deepseek(
                            GenerateRequest(
                                prompt="build a test app",
                                ai_provider="auto",
                            )
                        )

                error = raised.exception
                self.assertEqual(error.code, expected_code)
                self.assertFalse(error.retryable)
                self.assertFalse(error.failover_allowed)
                self.assertEqual(
                    error.details["attempted_providers"],
                    ["zhipu_glm52"],
                )
                self.assertEqual(client.post.await_count, 1)
                self.assertEqual(
                    error.details["provider_attempts"][0]["request_id"],
                    "request-safe",
                )
                self.assertNotIn("private upstream body", str(error))
                self.assertNotIn(
                    "private upstream body",
                    json.dumps(error.details),
                )

    async def test_call_timeout_reserves_budget_for_secondary(self) -> None:
        called_urls: list[str] = []

        async def post(url: str, **_: object) -> httpx.Response:
            called_urls.append(url)
            if "deepseek.invalid" in url:
                await asyncio.sleep(1)
            return self._response(200, model="secondary-served")

        client = self._client()
        client.post.side_effect = post
        env = self._env(
            AI_PROVIDER_ORDER_SIMPLE="deepseek,kimi",
            AI_READ_TIMEOUT_SECONDS="10",
            AI_OVERALL_TIMEOUT_SECONDS="10",
        )
        with patch.dict(os.environ, env, clear=True), patch(
            "app.generator.httpx.AsyncClient", return_value=client
        ):
            _, _, meta = await _call_deepseek(
                GenerateRequest(prompt="build a test app", ai_provider="auto"),
                timeout_seconds=0.3,
            )

        self.assertEqual(
            called_urls,
            [
                "https://deepseek.invalid/v1/chat/completions",
                "https://kimi.invalid/v1/chat/completions",
            ],
        )
        self.assertEqual(meta["provider"], "kimi")
        self.assertTrue(meta["failover_used"])

    async def test_disabled_aggregate_timeout_gives_fallback_a_full_window(self) -> None:
        called_urls: list[str] = []

        async def post(url: str, **_: object) -> httpx.Response:
            called_urls.append(url)
            if "deepseek.invalid" in url:
                await asyncio.sleep(0.1)
            return self._response(200, model="fallback-served")

        client = self._client()
        client.post.side_effect = post
        env = self._env(
            AI_PROVIDER_ORDER_SIMPLE="deepseek,kimi",
            AI_MAX_FAILOVER_PROVIDERS="2",
            AI_READ_TIMEOUT_SECONDS="0.05",
            AI_OVERALL_TIMEOUT_SECONDS="0",
        )
        with patch.dict(os.environ, env, clear=True), patch(
            "app.generator.httpx.AsyncClient", return_value=client
        ):
            _, _, meta = await _call_deepseek(
                GenerateRequest(prompt="build a test app", ai_provider="auto"),
                timeout_seconds=0.05,
            )

        self.assertEqual(
            called_urls,
            [
                "https://deepseek.invalid/v1/chat/completions",
                "https://kimi.invalid/v1/chat/completions",
            ],
        )
        self.assertEqual(meta["provider"], "kimi")
        self.assertTrue(meta["failover_used"])

    async def test_open_circuit_is_skipped_by_auto(self) -> None:
        env = self._env(AI_PROVIDER_CIRCUIT_FAILURE_THRESHOLD="1")
        first_client = self._client(
            self._response(503),
            self._response(200, model="secondary-served"),
        )
        with patch.dict(os.environ, env, clear=True), patch(
            "app.generator.httpx.AsyncClient", return_value=first_client
        ):
            await _call_deepseek(
                GenerateRequest(prompt="build a test app", ai_provider="auto")
            )

        second_client = self._client(self._response(200, model="secondary-served"))
        with patch.dict(os.environ, env, clear=True), patch(
            "app.generator.httpx.AsyncClient", return_value=second_client
        ):
            _, _, meta = await _call_deepseek(
                GenerateRequest(prompt="build a test app", ai_provider="auto")
            )

        self.assertEqual(meta["attempted_providers"], ["kimi_k27"])
        self.assertEqual(
            second_client.post.await_args.args[0],
            "https://kimi.invalid/v1/chat/completions",
        )

    async def test_half_open_circuit_allows_only_one_probe(self) -> None:
        env = self._env(
            AI_PROVIDER_ORDER_SIMPLE="deepseek,kimi",
            AI_PROVIDER_CIRCUIT_FAILURE_THRESHOLD="1",
            AI_PROVIDER_CIRCUIT_COOLDOWN_SECONDS="0.1",
        )
        probe_started = asyncio.Event()
        release_probe = asyncio.Event()
        primary_calls = 0

        async def post(url: str, **_: object) -> httpx.Response:
            nonlocal primary_calls
            if "deepseek.invalid" in url:
                primary_calls += 1
                probe_started.set()
                await release_probe.wait()
                return self._response(200, model="primary-recovered")
            return self._response(200, model="secondary-served")

        client = self._client()
        client.post.side_effect = post
        with patch.dict(os.environ, env, clear=True), patch(
            "app.generator.httpx.AsyncClient", return_value=client
        ):
            _record_provider_failure("deepseek")
            await asyncio.sleep(0.11)
            first = asyncio.create_task(
                _call_deepseek(
                    GenerateRequest(prompt="first probe", ai_provider="auto")
                )
            )
            await asyncio.wait_for(probe_started.wait(), timeout=1)
            try:
                _, _, second_meta = await _call_deepseek(
                    GenerateRequest(prompt="concurrent call", ai_provider="auto")
                )
            finally:
                release_probe.set()
            _, _, first_meta = await first

        self.assertEqual(primary_calls, 1)
        self.assertEqual(first_meta["provider"], "deepseek")
        self.assertEqual(second_meta["provider"], "kimi")

    async def test_explicit_unconfigured_provider_never_falls_back(self) -> None:
        env = self._env()
        env.pop("ZHIPU_API_KEY")
        client_factory = AsyncMock()
        with patch.dict(os.environ, env, clear=True), patch(
            "app.generator.httpx.AsyncClient", client_factory
        ):
            with self.assertRaises(UpstreamGenerationError) as raised:
                await _call_deepseek(
                    GenerateRequest(prompt="build a test app", ai_provider="zhipu_glm52")
                )

        self.assertEqual(raised.exception.code, "AI_UPSTREAM_UNAVAILABLE")
        self.assertFalse(raised.exception.retryable)
        client_factory.assert_not_called()

    async def test_timeout_has_structured_code(self) -> None:
        request = httpx.Request("POST", "https://deepseek.invalid/v1/chat/completions")
        client = self._client(httpx.ReadTimeout("private timeout", request=request))
        with patch.dict(os.environ, self._env(), clear=True), patch(
            "app.generator.httpx.AsyncClient", return_value=client
        ):
            with self.assertRaises(UpstreamGenerationError) as raised:
                await _call_deepseek(
                    GenerateRequest(
                        prompt="build a test app",
                        ai_provider="deepseek",
                    )
                )

        self.assertEqual(raised.exception.code, "AI_UPSTREAM_TIMEOUT")
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.details["attempted_providers"], ["deepseek"])
        self.assertNotIn("private timeout", str(raised.exception))

    async def test_session_request_does_not_expose_provider_selection(self) -> None:
        default_request = SessionCreateRequest(
            idempotency_key="provider-default-test",
            prompt="build a test app",
        )
        explicit_request = SessionCreateRequest(
            idempotency_key="provider-explicit-test",
            prompt="build a test app",
            ai_provider="kimi",
        )
        self.assertFalse(hasattr(default_request, "ai_provider"))
        self.assertFalse(hasattr(explicit_request, "ai_provider"))

    async def test_complex_request_prefers_glm52(self) -> None:
        client = self._client(self._response(200, model="glm52-served"))
        with patch.dict(os.environ, self._env(), clear=True), patch(
            "app.generator.httpx.AsyncClient", return_value=client
        ):
            _, _, meta = await _call_deepseek(
                GenerateRequest(
                    prompt="做一个多页面射击游戏，同时支持动画、碰撞、计分和排行榜",
                    ai_provider="auto",
                )
            )

        self.assertEqual(meta["routing_tier"], "complex")
        self.assertEqual(meta["provider"], "zhipu_glm52")
        self.assertEqual(
            client.post.await_args.args[0],
            "https://zhipu.invalid/v4/chat/completions",
        )

    async def test_revision_prefers_glm52(self) -> None:
        client = self._client(self._response(200, model="glm52-served"))
        with patch.dict(os.environ, self._env(), clear=True), patch(
            "app.generator.httpx.AsyncClient", return_value=client
        ):
            _, _, meta = await _call_deepseek(
                GenerateRequest(
                    prompt="把按钮改大并优化配色",
                    previous_code="print('existing app')",
                    ai_provider="auto",
                )
            )

        self.assertEqual(meta["routing_tier"], "revision")
        self.assertEqual(meta["provider"], "zhipu_glm52")
        self.assertEqual(
            client.post.await_args.args[0],
            "https://zhipu.invalid/v4/chat/completions",
        )

    def test_previous_code_routes_to_high_quality_revision_tier(self) -> None:
        tier, reason = _classify_request_complexity(
            GenerateRequest(prompt="fix this app", previous_code="print('broken')"),
        )
        self.assertEqual(tier, "revision")
        self.assertIn("continuing", reason)

    def test_simple_calendar_stays_on_simple_tier(self) -> None:
        tier, reason = _classify_request_complexity(
            GenerateRequest(prompt="做一个简洁的日历"),
        )
        self.assertEqual(tier, "simple")
        self.assertIn("single-purpose", reason)

    def test_short_multi_step_interaction_uses_complex_tier(self) -> None:
        tier, reason = _classify_request_complexity(
            GenerateRequest(
                prompt="做一个喝水提醒，点击按钮记录一杯，每隔一小时提醒"
            ),
        )
        self.assertEqual(tier, "complex")
        self.assertIn("multiple controls", reason)

    def test_runtime_error_prefers_repair_tier(self) -> None:
        tier, reason = _classify_request_complexity(
            GenerateRequest(
                prompt="fix this app",
                previous_code="print('broken')",
                runtime_error="TypeError: callback failed",
            ),
        )
        self.assertEqual(tier, "repair")
        self.assertIn("repair", reason)


if __name__ == "__main__":
    unittest.main()
