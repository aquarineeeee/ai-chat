from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from app.canonical_transcript import user_text_item
from app.models.api_key import ApiKey
from app.providers.anthropic import create_anthropic_reply, stream_anthropic_reply


class AnthropicUsageTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_reply_exposes_usage(self) -> None:
        response = httpx.Response(
            status_code=200,
            json={
                "content": [{"type": "text", "text": "done"}],
                "usage": {
                    "input_tokens": 12,
                    "cache_creation_input_tokens": 3,
                    "cache_read_input_tokens": 5,
                    "output_tokens": 7,
                },
            },
        )

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, _url, headers=None, json=None):
                return response

        with (
            patch("app.providers.anthropic.decrypt_text", return_value="secret"),
            patch("app.providers.anthropic.httpx.AsyncClient", return_value=FakeClient()),
        ):
            result = await create_anthropic_reply(
                api_key=ApiKey(provider="anthropic", key_encrypted="encrypted"),
                model="claude-sonnet-4-20250514",
                transcript=[user_text_item("hello")],
                temperature=None,
                max_tokens=800,
            )

        self.assertEqual(str(result), "done")
        self.assertEqual(result.usage, {"prompt_tokens": 20, "completion_tokens": 7, "total_tokens": 27})

    async def test_stream_reply_reports_final_usage(self) -> None:
        lines = [
            'data: {"type":"message_start","message":{"usage":{"input_tokens":12,"cache_read_input_tokens":5,"output_tokens":1}}}',
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"done"}}',
            'data: {"type":"message_delta","usage":{"output_tokens":7}}',
            'data: {"type":"message_delta","usage":{"output_tokens":9}}',
            'data: {"type":"message_stop"}',
        ]

        class FakeStreamResponse:
            status_code = 200
            headers = {}
            request = httpx.Request("POST", "https://example.com/v1/messages")

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def aiter_lines(self):
                for line in lines:
                    yield line

            async def aread(self):
                return b""

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def stream(self, _method, _url, headers=None, json=None):
                return FakeStreamResponse()

        usage_events: list[dict[str, int] | None] = []

        async def on_usage(usage: dict[str, int] | None) -> None:
            usage_events.append(usage)

        with (
            patch("app.providers.anthropic.decrypt_text", return_value="secret"),
            patch("app.providers.anthropic.httpx.AsyncClient", return_value=FakeClient()),
        ):
            chunks = [
                chunk
                async for chunk in stream_anthropic_reply(
                    api_key=ApiKey(provider="anthropic", key_encrypted="encrypted"),
                    model="claude-sonnet-4-20250514",
                    transcript=[user_text_item("hello")],
                    temperature=None,
                    max_tokens=800,
                    usage_callback=on_usage,
                )
            ]

        self.assertEqual(chunks, [{"type": "content", "content": "done"}])
        self.assertEqual(usage_events, [{"prompt_tokens": 17, "completion_tokens": 9, "total_tokens": 26}])

    async def test_stream_reply_emits_thinking_events_before_content(self) -> None:
        lines = [
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":"","signature":""}}',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"reasoning summary"}}',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"signature_delta","signature":"opaque-signature"}}',
            'data: {"type":"content_block_stop","index":0}',
            'data: {"type":"content_block_start","index":1,"content_block":{"type":"text","text":""}}',
            'data: {"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"answer"}}',
            'data: {"type":"message_stop"}',
        ]

        class FakeStreamResponse:
            status_code = 200
            headers = {}
            request = httpx.Request("POST", "https://example.com/v1/messages")

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def aiter_lines(self):
                for line in lines:
                    yield line

            async def aread(self):
                return b""

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def stream(self, _method, _url, headers=None, json=None):
                return FakeStreamResponse()

        with (
            patch("app.providers.anthropic.decrypt_text", return_value="secret"),
            patch("app.providers.anthropic.httpx.AsyncClient", return_value=FakeClient()),
        ):
            chunks = [
                chunk
                async for chunk in stream_anthropic_reply(
                    api_key=ApiKey(provider="anthropic", key_encrypted="encrypted"),
                    model="claude-sonnet-4-20250514",
                    transcript=[user_text_item("hello")],
                    temperature=None,
                    max_tokens=800,
                )
            ]

        self.assertEqual(
            chunks,
            [
                {"type": "thinking_started", "thinking_id": "thinking-0-0", "text": ""},
                {"type": "thinking_delta", "thinking_id": "thinking-0-0", "text": "reasoning summary"},
                {"type": "thinking_completed", "thinking_id": "thinking-0-0"},
                {"type": "content", "content": "answer"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
