from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from app.models.api_key import ApiKey
from app.providers.openai_compatible import create_openai_compatible_reply, stream_openai_compatible_reply


class OpenAIUsageTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_reply_exposes_accumulated_usage_on_result(self) -> None:
        api_key = ApiKey(provider="openai", key_encrypted="encrypted")
        responses = [
            httpx.Response(
                status_code=200,
                json={
                    "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {"name": "memory_search", "arguments": "{}"},
                                    }
                                ],
                            }
                        }
                    ],
                },
            ),
            httpx.Response(
                status_code=200,
                json={
                    "usage": {"prompt_tokens": 30, "completion_tokens": 25, "total_tokens": 55},
                    "choices": [{"message": {"role": "assistant", "content": "done"}}],
                },
            ),
        ]

        class FakeClient:
            def __init__(self, items):
                self._items = list(items)

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, _url, headers=None, json=None):
                return self._items.pop(0)

        async def fake_tool_executor(_name: str, _arguments: str) -> str:
            return "tool result"

        with (
            patch("app.providers.openai_compatible.decrypt_text", return_value="secret"),
            patch("app.providers.openai_compatible.httpx.AsyncClient", return_value=FakeClient(responses)),
        ):
            result = await create_openai_compatible_reply(
                api_key=api_key,
                model="gpt-4.1-mini",
                messages=[{"role": "user", "content": "hello"}],
                temperature=None,
                max_tokens=800,
                tools=[{"type": "function", "function": {"name": "memory_search", "parameters": {}}}],
                tool_executor=fake_tool_executor,
            )

        self.assertEqual(str(result), "done")
        self.assertEqual(
            result.usage,
            {"prompt_tokens": 130, "completion_tokens": 35, "total_tokens": 165},
        )

    async def test_stream_reply_reports_usage_via_callback(self) -> None:
        api_key = ApiKey(provider="openai", key_encrypted="encrypted")
        usage_events: list[dict[str, int] | None] = []

        class FakeStreamResponse:
            def __init__(self, lines):
                self.status_code = 200
                self.headers = {}
                self.request = httpx.Request("POST", "https://example.com/v1/chat/completions")
                self._lines = list(lines)

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def aiter_lines(self):
                for line in self._lines:
                    yield line

            async def aread(self):
                return b""

        class FakeClient:
            def __init__(self, streams):
                self._streams = list(streams)

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def stream(self, _method, _url, headers=None, json=None):
                return self._streams.pop(0)

        async def on_usage(value: dict[str, int] | None) -> None:
            usage_events.append(value)

        lines = [
            'data: {"choices":[{"delta":{"content":"hel"}}]}',
            'data: {"choices":[{"delta":{"content":"lo"}}]}',
            'data: {"usage":{"prompt_tokens":12,"completion_tokens":5,"total_tokens":17}}',
            "data: [DONE]",
        ]

        with (
            patch("app.providers.openai_compatible.decrypt_text", return_value="secret"),
            patch("app.providers.openai_compatible.httpx.AsyncClient", return_value=FakeClient([FakeStreamResponse(lines)])),
        ):
            chunks = []
            async for chunk in stream_openai_compatible_reply(
                api_key=api_key,
                model="gpt-4.1-mini",
                messages=[{"role": "user", "content": "hello"}],
                temperature=None,
                max_tokens=800,
                usage_callback=on_usage,
            ):
                chunks.append(chunk)

        self.assertEqual(
            chunks,
            [
                {"type": "content", "content": "hel"},
                {"type": "content", "content": "lo"},
            ],
        )
        self.assertEqual(
            usage_events,
            [{"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17}],
        )


if __name__ == "__main__":
    unittest.main()
