from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from app.canonical_transcript import user_text_item
from app.models.api_key import ApiKey
from app.providers.openai_responses import create_openai_responses_reply, stream_openai_responses_reply


class _FakeStreamResponse:
    def __init__(self, lines: list[str]) -> None:
        self.status_code = 200
        self.headers = {}
        self.request = httpx.Request("POST", "https://api.openai.com/v1/responses")
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return b""


class _FakeClient:
    def __init__(self, *, responses=None, streams=None) -> None:
        self.responses = list(responses or [])
        self.streams = list(streams or [])
        self.requests: list[dict[str, object]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers=None, json=None):
        self.requests.append({"url": url, "headers": headers, "json": json})
        return self.responses.pop(0)

    def stream(self, method, url, headers=None, json=None):
        self.requests.append({"method": method, "url": url, "headers": headers, "json": json})
        return self.streams.pop(0)


class OpenAIResponsesTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_streaming_response_uses_native_tools_and_continues_after_a_tool_call(self) -> None:
        api_key = ApiKey(provider="openai", key_encrypted="encrypted")
        client = _FakeClient(
            responses=[
                httpx.Response(
                    200,
                    json={
                        "usage": {"input_tokens": 12, "output_tokens": 5, "total_tokens": 17},
                        "output": [{"type": "function_call", "call_id": "call_1", "name": "memory_search", "arguments": "{}"}],
                    },
                ),
                httpx.Response(
                    200,
                    json={
                        "usage": {"input_tokens": 4, "output_tokens": 3, "total_tokens": 7},
                        "output": [{"type": "message", "content": [{"type": "output_text", "text": "final answer"}]}],
                    },
                ),
            ]
        )

        async def tool_executor(name: str, arguments: str) -> str:
            self.assertEqual((name, arguments), ("memory_search", "{}"))
            return "memory result"

        with (
            patch("app.providers.openai_responses.decrypt_text", return_value="secret"),
            patch("app.providers.openai_responses.httpx.AsyncClient", return_value=client),
        ):
            reply = await create_openai_responses_reply(
                api_key=api_key,
                model="gpt-4.1-mini",
                transcript=[user_text_item("find my notes")],
                temperature=None,
                max_tokens=500,
                tools=[{"type": "function", "function": {"name": "memory_search", "description": "Find notes", "parameters": {"type": "object"}}}],
                tool_executor=tool_executor,
            )

        self.assertEqual(str(reply), "final answer")
        self.assertEqual(reply.usage, {"prompt_tokens": 16, "completion_tokens": 8, "total_tokens": 24})
        first = client.requests[0]["json"]
        self.assertEqual(first["input"], [{"role": "user", "content": "find my notes"}])
        self.assertEqual(first["tools"], [{"type": "function", "name": "memory_search", "description": "Find notes", "parameters": {"type": "object"}}])
        second = client.requests[1]["json"]
        self.assertEqual(second["input"][-1], {"type": "function_call_output", "call_id": "call_1", "output": "memory result"})

    async def test_streaming_response_emits_reasoning_summary_and_usage(self) -> None:
        api_key = ApiKey(provider="openai", key_encrypted="encrypted")
        client = _FakeClient(
            streams=[
                _FakeStreamResponse(
                    [
                        'data: {"type":"response.output_item.added","output_index":0,"item":{"id":"rs_1","type":"reasoning"}}',
                        'data: {"type":"response.reasoning_summary_text.delta","item_id":"rs_1","delta":"brief rationale"}',
                        'data: {"type":"response.output_item.done","output_index":0,"item":{"id":"rs_1","type":"reasoning"}}',
                        'data: {"type":"response.output_text.delta","delta":"Hello"}',
                        'data: {"type":"response.output_text.delta","delta":"!"}',
                        'data: {"type":"response.completed","response":{"usage":{"input_tokens":9,"output_tokens":4,"total_tokens":13},"output":[{"type":"message","content":[{"type":"output_text","text":"Hello!"}]}]}}',
                    ]
                )
            ]
        )
        usage: list[dict[str, int] | None] = []

        async def capture_usage(value: dict[str, int] | None) -> None:
            usage.append(value)

        with (
            patch("app.providers.openai_responses.decrypt_text", return_value="secret"),
            patch("app.providers.openai_responses.httpx.AsyncClient", return_value=client),
        ):
            chunks = [
                chunk
                async for chunk in stream_openai_responses_reply(
                    api_key=api_key,
                    model="gpt-5-mini",
                    transcript=[user_text_item("hello")],
                    temperature=0.7,
                    max_tokens=500,
                    usage_callback=capture_usage,
                )
            ]

        self.assertEqual(
            chunks,
            [
                {"type": "thinking_started", "thinking_id": "thinking-0-rs_1", "text": ""},
                {"type": "thinking_delta", "thinking_id": "thinking-0-rs_1", "text": "brief rationale"},
                {"type": "thinking_completed", "thinking_id": "thinking-0-rs_1"},
                {"type": "content", "content": "Hello"},
                {"type": "content", "content": "!"},
            ],
        )
        self.assertEqual(usage, [{"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13}])
        payload = client.requests[0]["json"]
        self.assertEqual(payload["reasoning"], {"summary": "auto"})
        self.assertNotIn("temperature", payload)


if __name__ == "__main__":
    unittest.main()
