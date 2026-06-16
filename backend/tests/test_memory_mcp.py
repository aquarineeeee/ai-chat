from __future__ import annotations

import types
import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import httpx

from app.canonical_transcript import user_text_item
from app.models.agent_run import AgentRun
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
            memory_write_timeout_seconds=15.0,
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

    async def test_search_memory_forwards_full_breath_payload(self) -> None:
        settings = types.SimpleNamespace(
            memory_enabled=True,
            memory_timeout_seconds=5.0,
            memory_write_timeout_seconds=15.0,
            memory_max_context_chars=3000,
            memory_write_max_chars=6000,
            memory_mcp_url="http://127.0.0.1:8001/mcp",
        )

        with (
            patch("app.services.memory_mcp.get_settings", return_value=settings),
            patch(
                "app.services.memory_mcp._call_mcp_tool",
                AsyncMock(return_value={"content": [{"text": "记忆一"}]}),
            ) as mock_call,
        ):
            await memory_mcp.search_memory(
                query="  项目约束  ",
                max_tokens=2000,
                domain="  编程,项目  ",
                valence=0.6,
                arousal=0.4,
                max_results=6,
                importance_min=3,
            )

        mock_call.assert_awaited_once_with(
            "breath",
            {
                "query": "项目约束",
                "max_tokens": 2000,
                "domain": "编程,项目",
                "valence": 0.6,
                "arousal": 0.4,
                "max_results": 6,
                "importance_min": 3,
            },
        )

    async def test_search_memory_returns_none_for_no_results_text(self) -> None:
        settings = types.SimpleNamespace(
            memory_enabled=True,
            memory_timeout_seconds=5.0,
            memory_write_timeout_seconds=15.0,
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

    async def test_pulse_memory_forwards_full_payload(self) -> None:
        settings = types.SimpleNamespace(
            memory_enabled=True,
            memory_timeout_seconds=5.0,
            memory_write_timeout_seconds=15.0,
            memory_max_context_chars=3000,
            memory_write_max_chars=6000,
            memory_mcp_url="http://127.0.0.1:8001/mcp",
        )

        with (
            patch("app.services.memory_mcp.get_settings", return_value=settings),
            patch(
                "app.services.memory_mcp._call_mcp_tool",
                AsyncMock(return_value={"content": [{"text": "status"}]}),
            ) as mock_call,
        ):
            result = await memory_mcp.pulse_memory(include_archive=True)

        self.assertEqual(result, "status")
        mock_call.assert_awaited_once_with(
            "pulse",
            {
                "include_archive": True,
            },
            timeout=5.0,
        )

    async def test_dream_memory_forwards_empty_payload(self) -> None:
        settings = types.SimpleNamespace(
            memory_enabled=True,
            memory_timeout_seconds=5.0,
            memory_write_timeout_seconds=15.0,
            memory_max_context_chars=3000,
            memory_write_max_chars=6000,
            memory_mcp_url="http://127.0.0.1:8001/mcp",
        )

        with (
            patch("app.services.memory_mcp.get_settings", return_value=settings),
            patch(
                "app.services.memory_mcp._call_mcp_tool",
                AsyncMock(return_value={"content": [{"text": "recent"}]}),
            ) as mock_call,
        ):
            result = await memory_mcp.dream_memory()

        self.assertEqual(result, "recent")
        mock_call.assert_awaited_once_with(
            "dream",
            {},
            timeout=5.0,
        )

    async def test_write_memory_logs_and_raises_after_retries_fail(self) -> None:
        settings = types.SimpleNamespace(
            memory_enabled=True,
            memory_timeout_seconds=5.0,
            memory_write_timeout_seconds=15.0,
            memory_max_context_chars=3000,
            memory_write_max_chars=6000,
            memory_mcp_url="http://127.0.0.1:8001/mcp",
        )

        with (
            patch("app.services.memory_mcp.get_settings", return_value=settings),
            patch("app.services.memory_mcp._call_mcp_tool", AsyncMock(side_effect=RuntimeError("offline"))),
            patch("app.services.memory_mcp.logger.error"),
        ):
            with self.assertRaises(RuntimeError):
                await memory_mcp.write_memory(content="u")

    async def test_write_memory_retries_once_before_logging(self) -> None:
        settings = types.SimpleNamespace(
            memory_enabled=True,
            memory_timeout_seconds=5.0,
            memory_write_timeout_seconds=15.0,
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
            result = await memory_mcp.write_memory(content="u")

        self.assertEqual(attempts, 2)
        mock_error.assert_not_called()
        self.assertEqual(result, '{"result": "ok"}')

    async def test_grow_memory_forwards_full_payload(self) -> None:
        settings = types.SimpleNamespace(
            memory_enabled=True,
            memory_timeout_seconds=5.0,
            memory_write_timeout_seconds=15.0,
            memory_max_context_chars=3000,
            memory_write_max_chars=6000,
            memory_mcp_url="http://127.0.0.1:8001/mcp",
        )

        with (
            patch("app.services.memory_mcp.get_settings", return_value=settings),
            patch(
                "app.services.memory_mcp._call_mcp_tool",
                AsyncMock(return_value={"content": [{"text": "imported"}]}),
            ) as mock_call,
        ):
            result = await memory_mcp.grow_memory(content="  long memory text  ")

        self.assertEqual(result, "imported")
        mock_call.assert_awaited_once_with(
            "grow",
            {
                "content": "long memory text",
            },
            timeout=15.0,
        )

    async def test_update_memory_forwards_full_trace_payload(self) -> None:
        settings = types.SimpleNamespace(
            memory_enabled=True,
            memory_timeout_seconds=5.0,
            memory_write_timeout_seconds=15.0,
            memory_max_context_chars=3000,
            memory_write_max_chars=6000,
            memory_mcp_url="http://127.0.0.1:8001/mcp",
        )

        with (
            patch("app.services.memory_mcp.get_settings", return_value=settings),
            patch(
                "app.services.memory_mcp._call_mcp_tool",
                AsyncMock(return_value={"content": [{"text": "已更新"}]}),
            ) as mock_call,
        ):
            result = await memory_mcp.update_memory(
                bucket_id="  bucket-1  ",
                name="  项目约束  ",
                domain="  编程,项目  ",
                valence=0.6,
                arousal=0.4,
                importance=8,
                tags="  调试,已完成  ",
                resolved=1,
                pinned=0,
                digested=-1,
                content="  已修正正文  ",
                delete=False,
            )

        self.assertEqual(result, "已更新")
        mock_call.assert_awaited_once_with(
            "trace",
            {
                "bucket_id": "bucket-1",
                "name": "项目约束",
                "domain": "编程,项目",
                "valence": 0.6,
                "arousal": 0.4,
                "importance": 8,
                "tags": "调试,已完成",
                "resolved": 1,
                "pinned": 0,
                "digested": -1,
                "content": "已修正正文",
                "delete": False,
            },
            timeout=15.0,
        )

    async def test_search_and_write_do_not_use_shared_lock(self) -> None:
        self.assertFalse(hasattr(memory_mcp, "_MCP_SESSION_LOCK"))


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

    async def test_build_prompt_messages_skips_memory_context_when_disabled(self) -> None:
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

        with patch("app.services.messages.search_memory", AsyncMock(return_value="记忆上下文")) as mock_search:
            prompt_messages = await messages._build_prompt_messages(
                session=AsyncMock(),
                conversation=conversation,
                parent_id=2,
                history=history,
                include_memory_context=False,
            )

        mock_search.assert_not_called()
        self.assertEqual(
            prompt_messages,
            [
                {"role": "system", "content": "系统提示"},
                {"role": "user", "content": "之前的问题"},
                {"role": "assistant", "content": "之前的回答"},
            ],
        )

    async def test_generate_reply_for_openai_uses_memory_tools(self) -> None:
        session = AsyncMock()
        conversation = Conversation(user_id=1)

        with (
            patch("app.services.messages.get_preferred_api_key", AsyncMock(return_value="api-key")) as mock_get_key,
            patch("app.services.messages.create_openai_compatible_reply", AsyncMock(return_value="final answer")) as mock_reply,
        ):
            result = await messages._generate_reply(
                session=session,
                user_id=1,
                conversation=conversation,
                provider="openai",
                model="gpt-4.1-mini",
                temperature=None,
                max_tokens=1000,
                prompt_transcript=[user_text_item("hello")],
            )

        self.assertEqual(result["content"], "final answer")
        mock_get_key.assert_awaited_once_with(session=session, user_id=1, provider="openai")
        _, kwargs = mock_reply.await_args
        self.assertEqual(
            [tool["function"]["name"] for tool in kwargs["tools"]],
            ["memory_search", "memory_pulse", "memory_dream", "memory_write", "memory_grow", "memory_update"],
        )
        self.assertEqual(kwargs["transcript"], [user_text_item("hello")])
        self.assertTrue(callable(kwargs["tool_executor"]))

    async def test_generate_reply_for_anthropic_uses_memory_tools(self) -> None:
        session = AsyncMock()
        conversation = Conversation(user_id=1)

        with (
            patch("app.services.messages.get_preferred_api_key", AsyncMock(return_value="api-key")) as mock_get_key,
            patch("app.services.messages.create_anthropic_reply", AsyncMock(return_value="final answer")) as mock_reply,
        ):
            result = await messages._generate_reply(
                session=session,
                user_id=1,
                conversation=conversation,
                provider="anthropic",
                model="claude-sonnet-4-20250514",
                temperature=None,
                max_tokens=1000,
                prompt_transcript=[user_text_item("hello")],
            )

        self.assertEqual(result["content"], "final answer")
        mock_get_key.assert_awaited_once_with(session=session, user_id=1, provider="anthropic")
        _, kwargs = mock_reply.await_args
        self.assertEqual(
            [tool["function"]["name"] for tool in kwargs["tools"]],
            ["memory_search", "memory_pulse", "memory_dream", "memory_write", "memory_grow", "memory_update"],
        )
        self.assertEqual(kwargs["transcript"], [user_text_item("hello")])
        self.assertTrue(callable(kwargs["tool_executor"]))

    async def test_build_prompt_messages_includes_memory_tool_guidance_for_openai(self) -> None:
        session = AsyncMock()
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
        ]

        prompt_messages = await messages._build_prompt_messages(
            session=session,
            conversation=conversation,
            parent_id=1,
            history=history,
            include_memory_context=False,
            include_memory_tool_guidance=True,
        )

        self.assertEqual(prompt_messages[0], {"role": "system", "content": "系统提示"})
        self.assertEqual(prompt_messages[1], {"role": "system", "content": messages.MEMORY_TOOL_GUIDANCE})
        self.assertEqual(prompt_messages[2], {"role": "user", "content": "之前的问题"})
        self.assertIn("domain", messages.MEMORY_TOOL_GUIDANCE)
        self.assertIn("importance_min", messages.MEMORY_TOOL_GUIDANCE)
        self.assertIn("query 为空", messages.MEMORY_TOOL_GUIDANCE)
        self.assertIn("memory_pulse", messages.MEMORY_TOOL_GUIDANCE)
        self.assertIn("memory_dream", messages.MEMORY_TOOL_GUIDANCE)
        self.assertIn("memory_grow", messages.MEMORY_TOOL_GUIDANCE)
        self.assertIn("memory_update", messages.MEMORY_TOOL_GUIDANCE)
        self.assertIn("resolved", messages.MEMORY_TOOL_GUIDANCE)

    async def test_finalize_success_marks_message_completed_without_memory_write(self) -> None:
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

        await messages._finalize_success(
            session=session,
            conversation=conversation,
            branch=None,
            assistant_message=assistant_message,
            reply_content="新的回答",
            activate_branch=True,
        )

        self.assertEqual(assistant_message.content, "新的回答")
        self.assertEqual(assistant_message.status, MessageStatus.COMPLETED)
        session.commit.assert_awaited_once()
    async def test_finalize_success_marks_message_completed_without_memory_write(self) -> None:
        session = AsyncMock()
        conversation = Conversation(user_id=1)
        run = AgentRun(id=1, conversation_id=1, provider="openai", model="gpt-4.1-mini", status="running")
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

        with patch("app.services.messages._record_run_event", AsyncMock()):
            await messages._finalize_success(
                session=session,
                context={
                    "agent_run": run,
                    "assistant_message": assistant_message,
                    "conversation": conversation,
                },
                conversation=conversation,
                branch=None,
                assistant_message=assistant_message,
                reply_content="鏂扮殑鍥炵瓟",
                usage=None,
                activate_branch=True,
            )

        self.assertEqual(assistant_message.content, "鏂扮殑鍥炵瓟")
        self.assertEqual(assistant_message.status, MessageStatus.COMPLETED)
        self.assertEqual(run.status, messages.RUN_STATUS_COMPLETED)
        session.commit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
