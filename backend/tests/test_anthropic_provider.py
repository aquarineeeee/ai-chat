from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.canonical_transcript import user_text_item
from app.core.exceptions import AppError
from app.models.api_key import ApiKey
from app.providers.anthropic import _messages_payload, create_anthropic_reply, list_anthropic_models, stream_anthropic_reply
from app.services.memory_tools import (
    MEMORY_DREAM_TOOL_NAME,
    MEMORY_GROW_TOOL_NAME,
    MEMORY_PULSE_TOOL_NAME,
    MEMORY_SEARCH_TOOL_NAME,
    memory_dream_tool_definition,
    memory_grow_tool_definition,
    memory_pulse_tool_definition,
    memory_search_tool_definition,
)


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
        self.assertEqual(payload["cache_control"], {"type": "ephemeral"})
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
        self.assertEqual(payload["cache_control"], {"type": "ephemeral"})
        self.assertNotIn("system", payload)

    def test_messages_payload_adapts_openai_function_tools(self) -> None:
        tool = memory_search_tool_definition()
        payload = _messages_payload(
            model="claude-sonnet-4-20250514",
            messages=[{"role": "user", "content": "Hello"}],
            temperature=None,
            max_tokens=1000,
            stream=False,
            tools=[tool],
        )

        self.assertEqual(
            payload["tools"],
            [
                {
                    "name": MEMORY_SEARCH_TOOL_NAME,
                    "description": tool["function"]["description"],
                    "input_schema": tool["function"]["parameters"],
                }
            ],
        )
        self.assertIn("importance_min", payload["tools"][0]["input_schema"]["properties"])

    def test_messages_payload_adapts_memory_grow_tool(self) -> None:
        tool = memory_grow_tool_definition()
        payload = _messages_payload(
            model="claude-sonnet-4-20250514",
            messages=[{"role": "user", "content": "Hello"}],
            temperature=None,
            max_tokens=1000,
            stream=False,
            tools=[tool],
        )

        self.assertEqual(
            payload["tools"],
            [
                {
                    "name": MEMORY_GROW_TOOL_NAME,
                    "description": tool["function"]["description"],
                    "input_schema": tool["function"]["parameters"],
                }
            ],
        )
        self.assertEqual(payload["tools"][0]["input_schema"]["required"], ["content"])

    def test_messages_payload_adapts_memory_pulse_tool(self) -> None:
        tool = memory_pulse_tool_definition()
        payload = _messages_payload(
            model="claude-sonnet-4-20250514",
            messages=[{"role": "user", "content": "Hello"}],
            temperature=None,
            max_tokens=1000,
            stream=False,
            tools=[tool],
        )

        self.assertEqual(
            payload["tools"],
            [
                {
                    "name": MEMORY_PULSE_TOOL_NAME,
                    "description": tool["function"]["description"],
                    "input_schema": tool["function"]["parameters"],
                }
            ],
        )
        self.assertIn("include_archive", payload["tools"][0]["input_schema"]["properties"])

    def test_messages_payload_adapts_memory_dream_tool(self) -> None:
        tool = memory_dream_tool_definition()
        payload = _messages_payload(
            model="claude-sonnet-4-20250514",
            messages=[{"role": "user", "content": "Hello"}],
            temperature=None,
            max_tokens=1000,
            stream=False,
            tools=[tool],
        )

        self.assertEqual(
            payload["tools"],
            [
                {
                    "name": MEMORY_DREAM_TOOL_NAME,
                    "description": tool["function"]["description"],
                    "input_schema": tool["function"]["parameters"],
                }
            ],
        )
        self.assertEqual(payload["tools"][0]["input_schema"]["properties"], {})


class AnthropicToolingTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_reply_handles_tool_round_trip_with_full_breath_arguments(self) -> None:
        api_key = ApiKey(provider="anthropic", key_encrypted="encrypted")
        first_response = httpx.Response(
            status_code=200,
            json={
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu-1",
                        "name": MEMORY_SEARCH_TOOL_NAME,
                        "input": {
                            "query": "项目约束",
                            "max_tokens": 2000,
                            "domain": "编程,项目",
                            "valence": 0.6,
                            "arousal": 0.4,
                            "max_results": 6,
                            "importance_min": 3,
                        },
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
                transcript=[user_text_item("继续这个项目")],
                temperature=None,
                max_tokens=800,
                tools=[memory_search_tool_definition()],
                tool_executor=tool_executor,
            )

        self.assertEqual(result, "我查到了之前的项目约束，现在继续回答。")
        tool_executor.assert_awaited_once_with(
            MEMORY_SEARCH_TOOL_NAME,
            '{"query":"项目约束","max_tokens":2000,"domain":"编程,项目","valence":0.6,"arousal":0.4,"max_results":6,"importance_min":3}',
        )
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
                    transcript=[user_text_item("hello")],
                    temperature=None,
                    max_tokens=200,
                    tools=[memory_search_tool_definition()],
                )

        self.assertEqual(ctx.exception.code, "CONFIG_ERROR")

    async def test_stream_reply_handles_tool_round_trip(self) -> None:
        api_key = ApiKey(provider="anthropic", key_encrypted="encrypted")
        first_lines = [
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu-1","name":"memory_search","input":{}}}',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"query\\":\\"项目约束\\",\\"max_tokens\\":2000,\\"domain\\":\\"编程,项目\\",\\"valence\\":0.6,\\"arousal\\":0.4,\\"max_results\\":6,\\"importance_min\\":3}"}}',
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
                transcript=[user_text_item("继续")],
                temperature=None,
                max_tokens=800,
                tools=[memory_search_tool_definition()],
                tool_executor=tool_executor,
                tool_event_callback=on_tool_event,
            ):
                chunks.append(chunk)

        self.assertEqual(
            chunks,
            [
                {"type": "content", "content": "最终"},
                {"type": "content", "content": "回答"},
            ],
        )
        self.assertEqual(
            tool_events,
            [
                {
                    "name": "memory_search",
                    "status": "running",
                    "arguments": '{"query":"项目约束","max_tokens":2000,"domain":"编程,项目","valence":0.6,"arousal":0.4,"max_results":6,"importance_min":3}',
                },
                {
                    "name": "memory_search",
                    "status": "completed",
                    "content": "以下是长期记忆检索结果，仅供参考。\n项目约束",
                },
            ],
        )
        tool_executor.assert_awaited_once_with(
            MEMORY_SEARCH_TOOL_NAME,
            '{"query":"项目约束","max_tokens":2000,"domain":"编程,项目","valence":0.6,"arousal":0.4,"max_results":6,"importance_min":3}',
        )
        self.assertEqual(len(fake_client.requests), 2)
        second_messages = fake_client.requests[1]["json"]["messages"]
        self.assertEqual(second_messages[1]["role"], "assistant")
        self.assertEqual(second_messages[1]["content"][0]["id"], "toolu-1")
        self.assertEqual(second_messages[2]["role"], "user")
        self.assertEqual(second_messages[2]["content"][0]["content"], "以下是长期记忆检索结果，仅供参考。\n项目约束")

    async def test_stream_reply_retracts_and_records_commentary_for_tool_round_preamble(self) -> None:
        api_key = ApiKey(provider="anthropic", key_encrypted="encrypted")
        first_lines = [
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"让我先查一下记忆"}}',
            'data: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"toolu-1","name":"memory_search","input":{}}}',
            'data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"query\\":\\"x\\"}"}}',
            'data: {"type":"message_stop"}',
        ]
        second_lines = [
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"最终回答"}}',
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
        tool_executor = AsyncMock(return_value="ok")

        with (
            patch("app.providers.anthropic.decrypt_text", return_value="secret"),
            patch("app.providers.anthropic.httpx.AsyncClient", return_value=fake_client),
        ):
            chunks = []
            async for chunk in stream_anthropic_reply(
                api_key=api_key,
                model="claude-sonnet-4-20250514",
                transcript=[user_text_item("继续")],
                temperature=None,
                max_tokens=800,
                tools=[memory_search_tool_definition()],
                tool_executor=tool_executor,
            ):
                chunks.append(chunk)

        self.assertEqual(
            chunks,
            [
                {"type": "content", "content": "让我先查一下记忆"},
                {"type": "content_retracted", "content": "让我先查一下记忆"},
                {"type": "round_commentary", "text": "让我先查一下记忆"},
                {"type": "content", "content": "最终回答"},
            ],
        )

    async def test_stream_reply_pure_text_round_emits_no_retraction(self) -> None:
        api_key = ApiKey(provider="anthropic", key_encrypted="encrypted")
        lines = [
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"直接回答"}}',
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

        fake_client = FakeClient([FakeStreamResponse(lines)])

        with (
            patch("app.providers.anthropic.decrypt_text", return_value="secret"),
            patch("app.providers.anthropic.httpx.AsyncClient", return_value=fake_client),
        ):
            chunks = []
            async for chunk in stream_anthropic_reply(
                api_key=api_key,
                model="claude-sonnet-4-20250514",
                transcript=[user_text_item("你好")],
                temperature=None,
                max_tokens=800,
            ):
                chunks.append(chunk)

        self.assertEqual(chunks, [{"type": "content", "content": "直接回答"}])
        self.assertTrue(all(chunk["type"] == "content" for chunk in chunks))

    async def test_stream_reply_multi_tool_single_round_retracts_preamble_once(self) -> None:
        api_key = ApiKey(provider="anthropic", key_encrypted="encrypted")
        first_lines = [
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"我需要用两个工具"}}',
            'data: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"toolu-1","name":"memory_search","input":{}}}',
            'data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"query\\":\\"a\\"}"}}',
            'data: {"type":"content_block_start","index":2,"content_block":{"type":"tool_use","id":"toolu-2","name":"memory_search","input":{}}}',
            'data: {"type":"content_block_delta","index":2,"delta":{"type":"input_json_delta","partial_json":"{\\"query\\":\\"b\\"}"}}',
            'data: {"type":"message_stop"}',
        ]
        second_lines = [
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"最终"}}',
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
        tool_executor = AsyncMock(side_effect=["result-a", "result-b"])

        with (
            patch("app.providers.anthropic.decrypt_text", return_value="secret"),
            patch("app.providers.anthropic.httpx.AsyncClient", return_value=fake_client),
        ):
            chunks = []
            async for chunk in stream_anthropic_reply(
                api_key=api_key,
                model="claude-sonnet-4-20250514",
                transcript=[user_text_item("继续")],
                temperature=None,
                max_tokens=800,
                tools=[memory_search_tool_definition()],
                tool_executor=tool_executor,
            ):
                chunks.append(chunk)

        retracted_chunks = [chunk for chunk in chunks if chunk["type"] == "content_retracted"]
        commentary_chunks = [chunk for chunk in chunks if chunk["type"] == "round_commentary"]
        self.assertEqual(retracted_chunks, [{"type": "content_retracted", "content": "我需要用两个工具"}])
        self.assertEqual(commentary_chunks, [{"type": "round_commentary", "text": "我需要用两个工具"}])
        self.assertEqual(tool_executor.await_count, 2)


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
