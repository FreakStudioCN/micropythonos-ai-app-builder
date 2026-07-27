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
    _record_provider_failure,
    _reset_provider_circuits,
    provider_metadata,
)
from app.models import GenerateRequest, SessionCreateRequest


class ProviderRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _reset_provider_circuits()
        self.primary_key = secrets.token_urlsafe(32)
        self.secondary_key = secrets.token_urlsafe(32)
        self.aigocode_key = secrets.token_urlsafe(32)

    def _env(self, **overrides: str) -> dict[str, str]:
        values = {
            "DEEPSEEK_PRIMARY_API_KEY": self.primary_key,
            "DEEPSEEK_PRIMARY_BASE_URL": "https://primary.invalid/v1",
            "DEEPSEEK_PRIMARY_MODEL": "primary-model",
            "DEEPSEEK_SECONDARY_API_KEY": self.secondary_key,
            "DEEPSEEK_SECONDARY_BASE_URL": "https://secondary.invalid/v1",
            "DEEPSEEK_SECONDARY_MODEL": "secondary-model",
            "AIGOCODE_API_KEY": self.aigocode_key,
            "AIGOCODE_BASE_URL": "https://aigocode.invalid/v1",
            "AIGOCODE_MODEL": "gpt-test",
            "AI_PROVIDER_ORDER": "deepseek_primary,deepseek_secondary,aigocode",
            "AI_UPSTREAM_MAX_RETRIES": "0",
            "AI_RETRY_BACKOFF_SECONDS": "0",
            "AI_CONNECT_TIMEOUT_SECONDS": "1",
            "AI_READ_TIMEOUT_SECONDS": "2",
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
            ["auto", "deepseek_primary", "deepseek_secondary", "aigocode"],
        )
        self.assertTrue(all(set(item) == {"id", "label", "configured", "model"} for item in providers))
        serialized = json.dumps(providers)
        for secret in (self.primary_key, self.secondary_key, self.aigocode_key):
            self.assertNotIn(secret, serialized)
        self.assertNotIn("primary.invalid", serialized)
        self.assertNotIn("aigocode.invalid", serialized)

    async def test_explicit_aigocode_uses_only_selected_provider(self) -> None:
        client = self._client(self._response(200, model="gpt-served"))
        with patch.dict(os.environ, self._env(), clear=True), patch(
            "app.generator.httpx.AsyncClient", return_value=client
        ):
            generated, model, meta = await _call_deepseek(
                GenerateRequest(prompt="build a test app", ai_provider="aigocode")
            )

        self.assertEqual(model, "gpt-served")
        self.assertEqual(meta["provider"], "aigocode")
        self.assertFalse(meta["failover_used"])
        self.assertEqual(meta["attempted_providers"], ["aigocode"])
        self.assertEqual(generated["ai_routing"]["provider"], "aigocode")
        call = client.post.await_args
        self.assertEqual(call.args[0], "https://aigocode.invalid/v1/chat/completions")
        self.assertNotIn("thinking", call.kwargs["json"])

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
        self.assertEqual(meta["provider"], "deepseek_secondary")
        self.assertTrue(meta["failover_used"])
        self.assertEqual(
            meta["attempted_providers"],
            ["deepseek_primary", "deepseek_secondary"],
        )
        self.assertEqual(
            [item["outcome"] for item in meta["provider_attempts"]],
            ["http_503", "http_503", "success"],
        )

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
                    ["deepseek_primary"],
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
            if "primary.invalid" in url:
                await asyncio.sleep(1)
            return self._response(200, model="secondary-served")

        client = self._client()
        client.post.side_effect = post
        env = self._env(
            AI_PROVIDER_ORDER="deepseek_primary,deepseek_secondary",
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
                "https://primary.invalid/v1/chat/completions",
                "https://secondary.invalid/v1/chat/completions",
            ],
        )
        self.assertEqual(meta["provider"], "deepseek_secondary")
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

        self.assertEqual(meta["attempted_providers"], ["deepseek_secondary"])
        self.assertEqual(
            second_client.post.await_args.args[0],
            "https://secondary.invalid/v1/chat/completions",
        )

    async def test_half_open_circuit_allows_only_one_probe(self) -> None:
        env = self._env(
            AI_PROVIDER_ORDER="deepseek_primary,deepseek_secondary",
            AI_PROVIDER_CIRCUIT_FAILURE_THRESHOLD="1",
            AI_PROVIDER_CIRCUIT_COOLDOWN_SECONDS="0.1",
        )
        probe_started = asyncio.Event()
        release_probe = asyncio.Event()
        primary_calls = 0

        async def post(url: str, **_: object) -> httpx.Response:
            nonlocal primary_calls
            if "primary.invalid" in url:
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
            _record_provider_failure("deepseek_primary")
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
        self.assertEqual(first_meta["provider"], "deepseek_primary")
        self.assertEqual(second_meta["provider"], "deepseek_secondary")

    async def test_explicit_unconfigured_provider_never_falls_back(self) -> None:
        env = self._env()
        env.pop("AIGOCODE_API_KEY")
        client_factory = AsyncMock()
        with patch.dict(os.environ, env, clear=True), patch(
            "app.generator.httpx.AsyncClient", client_factory
        ):
            with self.assertRaises(UpstreamGenerationError) as raised:
                await _call_deepseek(
                    GenerateRequest(prompt="build a test app", ai_provider="aigocode")
                )

        self.assertEqual(raised.exception.code, "AI_UPSTREAM_UNAVAILABLE")
        self.assertFalse(raised.exception.retryable)
        client_factory.assert_not_called()

    async def test_timeout_has_structured_code(self) -> None:
        request = httpx.Request("POST", "https://primary.invalid/v1/chat/completions")
        client = self._client(httpx.ReadTimeout("private timeout", request=request))
        with patch.dict(os.environ, self._env(), clear=True), patch(
            "app.generator.httpx.AsyncClient", return_value=client
        ):
            with self.assertRaises(UpstreamGenerationError) as raised:
                await _call_deepseek(
                    GenerateRequest(
                        prompt="build a test app",
                        ai_provider="deepseek_primary",
                    )
                )

        self.assertEqual(raised.exception.code, "AI_UPSTREAM_TIMEOUT")
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.details["attempted_providers"], ["deepseek_primary"])
        self.assertNotIn("private timeout", str(raised.exception))

    async def test_session_request_defaults_to_auto(self) -> None:
        default_request = SessionCreateRequest(
            idempotency_key="provider-default-test",
            prompt="build a test app",
        )
        explicit_request = SessionCreateRequest(
            idempotency_key="provider-explicit-test",
            prompt="build a test app",
            ai_provider="deepseek_secondary",
        )
        self.assertEqual(default_request.ai_provider, "auto")
        self.assertEqual(explicit_request.ai_provider, "deepseek_secondary")


if __name__ == "__main__":
    unittest.main()
