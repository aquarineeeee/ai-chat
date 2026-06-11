from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.core.exceptions import AppError
from app.models.api_key import ApiKey
from app.providers.anthropic import _messages_payload, create_anthropic_reply, list_anthropic_models, stream_anthropic_reply
from app.services.memory_tools import MEMORY_SEARCH_TOOL_NAME, memory_search_tool_definition


class AnthropicProviderTests(unittest.TestCase):
    def test_messages_payload_moves_system_to_cacheable_system_block(self) -> None:
        payload = _messages_payload(
            model="claude-sonnet-4-20250514",
            messages=[
                {"role": "system", "content": "Stable system prompt"},
                {"role": "user", "content": "Hello"},
            ],
            temperature=None,
            max_tokens=1000,
            stream=False,
        )

        self.assertEqual(payload["model"], "claude-sonnet-4-20250514")
        self.assertEqual(payload["max_tokens"], 1000)
        self.assertEqual(payload["messages"], [{"role": "user", "content": "Hello"}])
        self.assertEqual(
            payload["system"],
            [
                {
                    "type": "text",
                    "text": "Stable system prompt",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        )

    def test_messages_payload_uses_default_max_tokens(self) -> None:
        payload = _messages_payload(
            model="claude-sonnet-4-20250514",
            messages=[{"role": "user", "content": "Hello"}],
            temperature=None,
            max_tokens=None,
            stream=True,
        )

        self.assertEqual(payload["max_tokens"], 2000)
        self.assertTrue(payload["stream"])
        self.assertNotIn("system", payload)

    def test_messages_payload_adapts_openai_function_tools(self) -> None:
        payload = _messages_payload(
            model="claude-sonnet-4-20250514",
            messages=[{"role": "user", "content": "Hello"}],
            temperature=None,
            max_tokens=1000,
            stream=False,
            tools=[memory_search_tool_definition()],
        )

        self.assertEqual(
            payload["tools"],
            [
                {
                    "name": MEMORY_SEARCH_TOOL_NAME,
                    "description": memory_search_tool_definition()["function"]["description"],
                    "input_schema": memory_search_tool_definition()["function"]["parameters"],
                }
            ],
        )


class AnthropicToolingTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_reply_handles_tool_round_trip(self) -> None:
        api_key = ApiKey(provider="anthropic", key_encrypted="encrypted")
        first_response = httpx.Response(
            status_code=200,
            json={
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu-1",
                        "name": MEMORY_SEARCH_TOOL_NAME,
                        "input": {"query": "项目约束"},
                    }
                ]
            },
        )
        second_response = httpx.Response(
            status_code=200,
            json={
                "content": [
                    {
                        "type": "text",
                        "text": "我查到了之前的项目约束，现在继续回答。",
                    }
                ]
            },
        )

        class FakeClient:
            def __init__(self, responses):
                self._responses = list(responses)
                self.requests: list[dict[str, object]] = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, _url, headers=None, json=None):
                self.requests.append({"headers": headers, "json": json})
                return self._responses.pop(0)

        fake_client = FakeClient([first_response, second_response])
        tool_executor = AsyncMock(return_value="以下是长期记忆检索结果，仅供参考。\n- 项目约束 A")

        with (
            patch("app.providers.anthropic.decrypt_text", return_value="secret"),
            patch("app.providers.anthropic.httpx.AsyncClient", return_value=fake_client),
        ):
            result = await create_anthropic_reply(
                api_key=api_key,
                model="claude-sonnet-4-20250514",
                messages=[{"role": "user", "content": "继续这个项目"}],
                temperature=None,
                max_tokens=800,
                tools=[memory_search_tool_definition()],
                tool_executor=tool_executor,
            )

        self.assertEqual(result, "我查到了之前的项目约束，现在继续回答。")
        tool_executor.assert_awaited_once_with(MEMORY_SEARCH_TOOL_NAME, '{"query":"项目约束"}')
        self.assertEqual(len(fake_client.requests), 2)

        second_messages = fake_client.requests[1]["json"]["messages"]
        self.assertEqual(second_messages[1]["role"], "assistant")
        self.assertEqual(second_messages[1]["content"][0]["id"], "toolu-1")
        self.assertEqual(second_messages[2]["role"], "user")
        self.assertEqual(second_messages[2]["content"][0]["tool_use_id"], "toolu-1")

    async def test_create_reply_rejects_tools_without_executor(self) -> None:
        api_key = ApiKey(provider="anthropic", key_encrypted="encrypted")

        with patch("app.providers.anthropic.decrypt_text", return_value="secret"):
            with self.assertRaises(AppError) as ctx:
                await create_anthropic_reply(
                    api_key=api_key,
                    model="claude-sonnet-4-20250514",
                    messages=[{"role": "user", "content": "hello"}],
                    temperature=None,
                    max_tokens=200,
                    tools=[memory_search_tool_definition()],
                )

        self.assertEqual(ctx.exception.code, "CONFIG_ERROR")

    async def test_stream_reply_handles_tool_round_trip(self) -> None:
        api_key = ApiKey(provider="anthropic", key_encrypted="encrypted")
        first_lines = [
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu-1","name":"memory_search","input":{}}}',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"query\\":\\"项目约束\\"}"}}',
            'data: {"type":"message_stop"}',
        ]
        second_lines = [
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"最终"}}',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"回答"}}',
            'data: {"type":"message_stop"}',
        ]

        class FakeStreamResponse:
            def __init__(self, lines):
                self.status_code = 200
                self.headers = {}
                self.request = httpx.Request("POST", "https://example.com/v1/messages")
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
                self.requests: list[dict[str, object]] = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def stream(self, _method, _url, headers=None, json=None):
                self.requests.append({"headers": headers, "json": json})
                return self._streams.pop(0)

        fake_client = FakeClient([FakeStreamResponse(first_lines), FakeStreamResponse(second_lines)])
        tool_executor = AsyncMock(return_value="以下是长期记忆检索结果，仅供参考。\n项目约束")

        with (
            patch("app.providers.anthropic.decrypt_text", return_value="secret"),
            patch("app.providers.anthropic.httpx.AsyncClient", return_value=fake_client),
        ):
            chunks = []
            tool_events = []

            async def on_tool_event(event):
                tool_events.append(event)

            async for chunk in stream_anthropic_reply(
                api_key=api_key,
                model="claude-sonnet-4-20250514",
                messages=[{"role": "user", "content": "继续"}],
                temperature=None,
                max_tokens=800,
                tools=[memory_search_tool_definition()],
                tool_executor=tool_executor,
                tool_event_callback=on_tool_event,
            ):
                chunks.append(chunk)

        self.assertEqual(chunks, ["最终", "回答"])
        tool_executor.assert_awaited_once_with(MEMORY_SEARCH_TOOL_NAME, '{"query":"项目约束"}')
        self.assertEqual(
            tool_events,
            [
                {"name": "memory_search", "status": "running", "arguments": '{"query":"项目约束"}'},
                {
                    "name": "memory_search",
                    "status": "completed",
                    "content": "以下是长期记忆检索结果，仅供参考。\n项目约束",
                },
            ],
        )
        self.assertEqual(len(fake_client.requests), 2)
        second_messages = fake_client.requests[1]["json"]["messages"]
        self.assertEqual(second_messages[1]["role"], "assistant")
        self.assertEqual(second_messages[1]["content"][0]["id"], "toolu-1")
        self.assertEqual(second_messages[2]["role"], "user")
        self.assertEqual(second_messages[2]["content"][0]["content"], "以下是长期记忆检索结果，仅供参考。\n项目约束")


class AnthropicModelListTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_models_accepts_top_level_array(self) -> None:
        api_key = ApiKey(provider="anthropic", key_encrypted="encrypted")
        response = httpx.Response(
            status_code=200,
            json=[
                {"id": "claude-sonnet", "display_name": "Claude Sonnet"},
                {"id": "claude-haiku"},
            ],
        )

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, _url, headers=None):
                return response

        with (
            patch("app.providers.anthropic.decrypt_text", return_value="secret"),
            patch("app.providers.anthropic.httpx.AsyncClient", return_value=FakeClient()),
        ):
            models = await list_anthropic_models(api_key=api_key)

        self.assertEqual(
            models,
            [
                {"id": "claude-haiku", "owned_by": "anthropic"},
                {"id": "claude-sonnet", "owned_by": "Claude Sonnet"},
            ],
        )

    async def test_list_models_surfaces_payload_shape_in_details(self) -> None:
        api_key = ApiKey(provider="anthropic", key_encrypted="encrypted")
        response = httpx.Response(status_code=200, json="unexpected")

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, _url, headers=None):
                return response

        with (
            patch("app.providers.anthropic.decrypt_text", return_value="secret"),
            patch("app.providers.anthropic.httpx.AsyncClient", return_value=FakeClient()),
        ):
            with self.assertRaises(AppError) as ctx:
                await list_anthropic_models(api_key=api_key)

        self.assertEqual(ctx.exception.message, "Anthropic 模型列表响应格式不正确")
        self.assertEqual(ctx.exception.details, "顶层类型: str")


if __name__ == "__main__":
    unittest.main()
