from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.core.exceptions import AppError
from app.models.api_key import ApiKey
from app.providers.openai_compatible import create_openai_compatible_reply, stream_openai_compatible_reply
from app.services.memory_tools import (
    MEMORY_SEARCH_TOOL_NAME,
    execute_memory_tool_call,
    memory_search_tool_definition,
)


class MemoryToolTests(unittest.IsolatedAsyncioTestCase):
    def test_memory_search_tool_definition_exposes_required_query(self) -> None:
        tool = memory_search_tool_definition()

        self.assertEqual(tool["type"], "function")
        self.assertEqual(tool["function"]["name"], MEMORY_SEARCH_TOOL_NAME)
        self.assertEqual(tool["function"]["parameters"]["required"], ["query"])
        self.assertFalse(tool["function"]["parameters"]["additionalProperties"])

    async def test_execute_memory_tool_call_returns_search_result(self) -> None:
        with patch(
            "app.services.memory_tools.search_memory",
            AsyncMock(return_value="以下是长期记忆检索结果，仅供参考。\n记忆命中"),
        ) as mock_search:
            result = await execute_memory_tool_call(MEMORY_SEARCH_TOOL_NAME, '{"query":"用户偏好"}')

        self.assertEqual(result, "以下是长期记忆检索结果，仅供参考。\n记忆命中")
        mock_search.assert_awaited_once_with(query="用户偏好")

    async def test_execute_memory_tool_call_rejects_invalid_arguments(self) -> None:
        result = await execute_memory_tool_call(MEMORY_SEARCH_TOOL_NAME, '{"query":""}')

        self.assertEqual(result, "工具执行失败：query 不能为空")


class OpenAICompatibleToolingTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_reply_handles_tool_round_trip(self) -> None:
        api_key = ApiKey(provider="openai", key_encrypted="encrypted")
        first_response = httpx.Response(
            status_code=200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": MEMORY_SEARCH_TOOL_NAME,
                                        "arguments": '{"query":"记住的项目约束"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )
        second_response = httpx.Response(
            status_code=200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "我查到了之前的项目约束，现在继续回答。",
                        }
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
            patch("app.providers.openai_compatible.decrypt_text", return_value="secret"),
            patch("app.providers.openai_compatible.httpx.AsyncClient", return_value=fake_client),
        ):
            result = await create_openai_compatible_reply(
                api_key=api_key,
                model="gpt-4.1-mini",
                messages=[{"role": "user", "content": "继续这个项目"}],
                temperature=None,
                max_tokens=800,
                tools=[memory_search_tool_definition()],
                tool_executor=tool_executor,
            )

        self.assertEqual(result, "我查到了之前的项目约束，现在继续回答。")
        tool_executor.assert_awaited_once_with(MEMORY_SEARCH_TOOL_NAME, '{"query":"记住的项目约束"}')
        self.assertEqual(len(fake_client.requests), 2)

        second_messages = fake_client.requests[1]["json"]["messages"]
        self.assertEqual(second_messages[1]["role"], "assistant")
        self.assertEqual(second_messages[1]["tool_calls"][0]["id"], "call-1")
        self.assertEqual(second_messages[2]["role"], "tool")
        self.assertEqual(second_messages[2]["tool_call_id"], "call-1")

    async def test_create_reply_rejects_tools_without_executor(self) -> None:
        api_key = ApiKey(provider="openai", key_encrypted="encrypted")

        with patch("app.providers.openai_compatible.decrypt_text", return_value="secret"):
            with self.assertRaises(AppError) as ctx:
                await create_openai_compatible_reply(
                    api_key=api_key,
                    model="gpt-4.1-mini",
                    messages=[{"role": "user", "content": "hello"}],
                    temperature=None,
                    max_tokens=200,
                    tools=[memory_search_tool_definition()],
                )

        self.assertEqual(ctx.exception.code, "CONFIG_ERROR")

    async def test_stream_reply_handles_tool_round_trip(self) -> None:
        api_key = ApiKey(provider="openai", key_encrypted="encrypted")
        first_lines = [
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","type":"function","function":{"name":"memory_search","arguments":"{\\"query\\":\\""}}]}}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"项目约束\\"}"}}]},"finish_reason":"tool_calls"}]}',
            "data: [DONE]",
        ]
        second_lines = [
            'data: {"choices":[{"delta":{"content":"最终"}}]}',
            'data: {"choices":[{"delta":{"content":"回答"}}]}',
            "data: [DONE]",
        ]

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
            patch("app.providers.openai_compatible.decrypt_text", return_value="secret"),
            patch("app.providers.openai_compatible.httpx.AsyncClient", return_value=fake_client),
        ):
            chunks = []
            async for chunk in stream_openai_compatible_reply(
                api_key=api_key,
                model="gpt-4.1-mini",
                messages=[{"role": "user", "content": "继续"}],
                temperature=None,
                max_tokens=800,
                tools=[memory_search_tool_definition()],
                tool_executor=tool_executor,
            ):
                chunks.append(chunk)

        self.assertEqual(chunks, ["最终", "回答"])
        tool_executor.assert_awaited_once_with(MEMORY_SEARCH_TOOL_NAME, '{"query":"项目约束"}')
        self.assertEqual(len(fake_client.requests), 2)
        second_messages = fake_client.requests[1]["json"]["messages"]
        self.assertEqual(second_messages[1]["role"], "assistant")
        self.assertEqual(second_messages[1]["tool_calls"][0]["id"], "call-1")
        self.assertEqual(second_messages[2]["role"], "tool")
        self.assertEqual(second_messages[2]["content"], "以下是长期记忆检索结果，仅供参考。\n项目约束")


if __name__ == "__main__":
    unittest.main()
