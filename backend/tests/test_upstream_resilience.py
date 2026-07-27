import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app.generator import UpstreamGenerationError, _call_deepseek
from app.models import (
    GenerateRequest,
    PermissionDecisionRequest,
    SessionActionRequest,
    SessionCreateRequest,
)
import app.session_service as session_module


def _response(status_code: int, payload: dict | None = None) -> httpx.Response:
    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    if payload is None:
        return httpx.Response(status_code, request=request, text="upstream error")
    return httpx.Response(status_code, request=request, json=payload)


def _success_response() -> httpx.Response:
    return _response(
        200,
        {
            "id": "request-test",
            "model": "test-model",
            "choices": [
                {
                    "message": {
                        "content": json.dumps({"summary": "ok"})
                    }
                }
            ],
        },
    )


def _mock_client(*outcomes):
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    client.post = AsyncMock(side_effect=list(outcomes))
    return client


class DeepSeekResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_retries_are_bounded_and_structured(self) -> None:
        client = _mock_client(
            httpx.ReadTimeout("late"),
            httpx.ReadTimeout("late"),
            httpx.ReadTimeout("late"),
        )
        sleep = AsyncMock()
        with patch.dict(
            "app.generator.os.environ",
            {
                "DEEPSEEK_API_KEY": "sk-test",
                "DEEPSEEK_UPSTREAM_MAX_RETRIES": "2",
                "DEEPSEEK_RETRY_BACKOFF_SECONDS": "0.01",
                "DEEPSEEK_OVERALL_TIMEOUT_SECONDS": "10",
            },
        ), patch(
            "app.generator.httpx.AsyncClient", return_value=client
        ), patch("app.generator.asyncio.sleep", new=sleep):
            with self.assertRaises(UpstreamGenerationError) as raised:
                await _call_deepseek(
                    GenerateRequest(prompt="Build a calculator"),
                    timeout_seconds=10,
                )
        self.assertEqual(raised.exception.code, "AI_UPSTREAM_TIMEOUT")
        self.assertEqual(client.post.await_count, 3)
        self.assertEqual(sleep.await_count, 2)

    async def test_429_and_5xx_retry_but_400_does_not(self) -> None:
        for status_code in (429, 503):
            with self.subTest(status_code=status_code):
                client = _mock_client(
                    _response(status_code),
                    _success_response(),
                )
                with patch.dict(
                    "app.generator.os.environ",
                    {
                        "DEEPSEEK_API_KEY": "sk-test",
                        "DEEPSEEK_UPSTREAM_MAX_RETRIES": "2",
                        "DEEPSEEK_RETRY_BACKOFF_SECONDS": "0",
                    },
                ), patch(
                    "app.generator.httpx.AsyncClient", return_value=client
                ):
                    generated, model, _meta = await _call_deepseek(
                        GenerateRequest(prompt="Build a calculator"),
                        timeout_seconds=10,
                    )
                self.assertEqual(generated["summary"], "ok")
                self.assertEqual(model, "test-model")
                self.assertEqual(client.post.await_count, 2)

        client = _mock_client(_response(400), _success_response())
        with patch.dict(
            "app.generator.os.environ",
            {
                "DEEPSEEK_API_KEY": "sk-test",
                "DEEPSEEK_UPSTREAM_MAX_RETRIES": "2",
            },
        ), patch("app.generator.httpx.AsyncClient", return_value=client):
            with self.assertRaises(UpstreamGenerationError) as raised:
                await _call_deepseek(
                    GenerateRequest(prompt="Build a calculator"),
                    timeout_seconds=10,
                )
        self.assertEqual(raised.exception.code, "AI_UPSTREAM_REJECTED")
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(client.post.await_count, 1)


class RecoverableSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.original_root = session_module.SESSION_ROOT
        session_module.SESSION_ROOT = Path(self.temp.name).resolve()
        self.service = session_module.SessionService()

    def tearDown(self) -> None:
        session_module.SESSION_ROOT = self.original_root
        self.temp.cleanup()

    def _ready_session(self) -> dict:
        state = self.service.create(
            SessionCreateRequest(
                idempotency_key="upstream-session-create-01",
                prompt="Build a calculator",
                package_name="com.example.upstream_timeout",
                targets=["package-only"],
            )
        )
        for permission in state["permissions"]:
            if permission["required"]:
                state = self.service.decide_permission(
                    permission["permission_id"],
                    PermissionDecisionRequest(
                        idempotency_key=f"allow-{permission['permission_id']}",
                        decision="allow_once",
                    ),
                )
        return state

    async def test_background_session_survives_caller_and_keeps_resume_checkpoint(
        self,
    ) -> None:
        state = self._ready_session()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def delayed_failure(*_args, **_kwargs):
            entered.set()
            await release.wait()
            raise UpstreamGenerationError(
                "AI_UPSTREAM_TIMEOUT",
                "upstream timed out",
                retryable=True,
                failover_allowed=False,
                details={"attempts": 3},
            )

        with patch.object(
            session_module, "generate_app", side_effect=delayed_failure
        ):
            self.service.start_generation(
                state["session_id"],
                SessionActionRequest(
                    idempotency_key="upstream-session-run-01"
                ),
            )
            await entered.wait()
            task = self.service._tasks[state["session_id"]]
            self.assertFalse(task.done())
            self.assertEqual(
                self.service.get(state["session_id"])["status"], "running"
            )
            release.set()
            await task

        failed = self.service.get(state["session_id"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["last_error"]["code"], "AI_UPSTREAM_TIMEOUT")
        self.assertEqual(failed["last_error"]["owner"], "external")
        self.assertTrue(failed["last_error"]["retryable"])
        self.assertEqual(
            failed["resume_checkpoint_id"], "dependencies_prepared"
        )
        self.assertEqual(failed["next_phase"], "mpos-gen-app-web")


if __name__ == "__main__":
    unittest.main()
