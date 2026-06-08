from __future__ import annotations

import asyncio
import types
import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import httpx

from app.models.conversation import Conversation
from app.models.message import Message, MessageRole, MessageStatus
from app.services import memory_mcp, messages


class MemoryMcpTests(unittest.IsolatedAsyncioTestCase):
    def test_normalize_memory_text_strips_control_characters(self) -> None:
        self.assertEqual(memory_mcp._normalize_memory_text("a\x00b\r\nc\t"), "ab\nc")

    def test_preview_escapes_newlines(self) -> None:
        self.assertEqual(memory_mcp._preview("a\nb"), "a\\nb")

    def test_http_status_summary_includes_response_body_preview(self) -> None:
        request = httpx.Request("POST", "http://127.0.0.1:8001/mcp")
        response = httpx.Response(
            400,
            request=request,
            headers={"Content-Type": "application/json"},
            text='{"error":"bad session"}',
        )
        exc = httpx.HTTPStatusError("bad request", request=request, response=response)

        summary = memory_mcp._exception_summary(exc)

        self.assertIn("HTTPStatusError", summary)
        self.assertIn("status=400", summary)
        self.assertIn("bad session", summary)

    def test_extracts_sse_payload(self) -> None:
        payload = memory_mcp._sse_message_payload(
            'event: message\n'
            'data: {"jsonrpc":"2.0","id":1,"result":{"structuredContent":{"result":"ok"}}}\n\n'
        )
        self.assertEqual(payload["result"]["structuredContent"]["result"], "ok")

    async def test_search_memory_returns_none_when_disabled(self) -> None:
        settings = types.SimpleNamespace(
            memory_enabled=False,
            memory_timeout_seconds=5.0,
            memory_max_context_chars=3000,
            memory_write_max_chars=6000,
            memory_mcp_url="http://127.0.0.1:8001/mcp",
        )

        with patch("app.services.memory_mcp.get_settings", return_value=settings):
            result = await memory_mcp.search_memory(query="hello")

        self.assertIsNone(result)

    async def test_search_memory_formats_tool_text(self) -> None:
        settings = types.SimpleNamespace(
            memory_enabled=True,
            memory_timeout_seconds=5.0,
            memory_max_context_chars=3000,
            memory_write_max_chars=6000,
            memory_mcp_url="http://127.0.0.1:8001/mcp",
        )
        result = {
            "content": [
                {"text": "记忆一"},
                {"text": "记忆二"},
            ]
        }

        with (
            patch("app.services.memory_mcp.get_settings", return_value=settings),
            patch("app.services.memory_mcp._call_mcp_tool", AsyncMock(return_value=result)),
        ):
            formatted = await memory_mcp.search_memory(query="  睡眠  ")

        self.assertEqual(
            formatted,
            "以下是长期记忆检索结果，仅供参考，可能相关，但不保证是当前最新事实。\n记忆一\n记忆二",
        )

    async def test_search_memory_returns_none_for_no_results_text(self) -> None:
        settings = types.SimpleNamespace(
            memory_enabled=True,
            memory_timeout_seconds=5.0,
            memory_max_context_chars=3000,
            memory_write_max_chars=6000,
            memory_mcp_url="http://127.0.0.1:8001/mcp",
        )

        with (
            patch("app.services.memory_mcp.get_settings", return_value=settings),
            patch(
                "app.services.memory_mcp._call_mcp_tool",
                AsyncMock(return_value={"content": [{"text": "未找到相关记忆。"}]}),
            ),
        ):
            formatted = await memory_mcp.search_memory(query="  不存在  ")

        self.assertIsNone(formatted)

    async def test_write_memory_swallows_transport_errors(self) -> None:
        settings = types.SimpleNamespace(
            memory_enabled=True,
            memory_timeout_seconds=5.0,
            memory_max_context_chars=3000,
            memory_write_max_chars=6000,
            memory_mcp_url="http://127.0.0.1:8001/mcp",
        )

        with (
            patch("app.services.memory_mcp.get_settings", return_value=settings),
            patch("app.services.memory_mcp._call_mcp_tool", AsyncMock(side_effect=RuntimeError("offline"))),
            patch("app.services.memory_mcp.logger.error"),
        ):
            await memory_mcp.write_memory(user_content="u", assistant_content="a")

    async def test_write_memory_retries_once_before_logging(self) -> None:
        settings = types.SimpleNamespace(
            memory_enabled=True,
            memory_timeout_seconds=5.0,
            memory_max_context_chars=3000,
            memory_write_max_chars=6000,
            memory_mcp_url="http://127.0.0.1:8001/mcp",
        )
        attempts = 0

        async def fake_call(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("temporary")
            return {"structuredContent": {"result": "ok"}}

        with (
            patch("app.services.memory_mcp.get_settings", return_value=settings),
            patch("app.services.memory_mcp._call_mcp_tool", AsyncMock(side_effect=fake_call)),
            patch("app.services.memory_mcp.logger.error") as mock_error,
        ):
            await memory_mcp.write_memory(user_content="u", assistant_content="a")

        self.assertEqual(attempts, 2)
        mock_error.assert_not_called()

    async def test_search_and_write_use_shared_lock(self) -> None:
        self.assertTrue(hasattr(memory_mcp, "_MCP_SESSION_LOCK"))
        self.assertIsInstance(memory_mcp._MCP_SESSION_LOCK, asyncio.Lock)


class MessageMemoryIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_prompt_messages_inserts_memory_context_after_system(self) -> None:
        conversation = Conversation(user_id=1, system_prompt="系统提示")
        now = datetime.now(UTC)
        history = [
            Message(
                id=1,
                conversation_id=1,
                parent_id=None,
                role=MessageRole.USER,
                content="之前的问题",
                status=MessageStatus.COMPLETED,
                created_at=now,
                updated_at=now,
            ),
            Message(
                id=2,
                conversation_id=1,
                parent_id=1,
                role=MessageRole.ASSISTANT,
                content="之前的回答",
                status=MessageStatus.COMPLETED,
                created_at=now,
                updated_at=now,
            ),
        ]

        with patch("app.services.messages.search_memory", AsyncMock(return_value="记忆上下文")):
            prompt_messages = await messages._build_prompt_messages(
                session=AsyncMock(),
                conversation=conversation,
                parent_id=2,
                history=history,
            )

        self.assertEqual(
            prompt_messages,
            [
                {"role": "system", "content": "系统提示"},
                {"role": "system", "content": "记忆上下文"},
                {"role": "user", "content": "之前的问题"},
                {"role": "assistant", "content": "之前的回答"},
            ],
        )

    async def test_finalize_success_writes_memory_when_user_content_present(self) -> None:
        session = AsyncMock()
        conversation = Conversation(user_id=1)
        now = datetime.now(UTC)
        assistant_message = Message(
            id=2,
            conversation_id=1,
            parent_id=1,
            role=MessageRole.ASSISTANT,
            content="",
            status=MessageStatus.STREAMING,
            created_at=now,
            updated_at=now,
        )

        with patch("app.services.messages.write_memory", AsyncMock()) as mock_write_memory:
            await messages._finalize_success(
                session=session,
                conversation=conversation,
                branch=None,
                assistant_message=assistant_message,
                reply_content="新的回答",
                activate_branch=True,
                memory_user_content="原始问题",
            )

        mock_write_memory.assert_awaited_once_with(user_content="原始问题", assistant_content="新的回答")


if __name__ == "__main__":
    unittest.main()
