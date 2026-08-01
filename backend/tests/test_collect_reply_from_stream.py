from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.models.agent_run import AgentRun
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole, MessageStatus
from app.models.run_event import RunEvent
from app.services import messages
from app.services.agent_trace import parts_from_message


def _make_context() -> dict[str, object]:
    conversation = Conversation(id=1, user_id=1)
    agent_run = AgentRun(
        id=1,
        conversation_id=1,
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        status="running",
        last_sequence=0,
    )
    assistant_message = Message(
        id=2,
        conversation_id=1,
        parent_id=1,
        role=MessageRole.ASSISTANT,
        content="",
        status=MessageStatus.STREAMING,
        parts_json=None,
    )
    return {
        "agent_run": agent_run,
        "assistant_message": assistant_message,
        "conversation": conversation,
        "provider": "anthropic",
        "model": "claude-sonnet-4-20250514",
        "temperature": None,
        "max_tokens": None,
        "prompt_transcript": [],
    }


def _make_session() -> AsyncMock:
    session = AsyncMock()
    session.add = Mock()
    session.scalar = AsyncMock(return_value=None)
    return session


def _recorded_events(session: AsyncMock) -> list:
    return [
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], RunEvent)
    ]


def _payload(event) -> dict[str, object]:
    return json.loads(event.payload_json) if event.payload_json else {}


class CollectReplyFromStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_round_preamble_is_retracted_and_kept_as_commentary_and_model_text(self) -> None:
        context = _make_context()
        session = _make_session()

        async def fake_stream_reply(**_kwargs):
            yield {"type": "content", "content": "先看看记忆"}
            yield {"type": "content_retracted", "content": "先看看记忆"}
            yield {"type": "round_commentary", "text": "先看看记忆"}
            yield {"type": "tool", "tool": {"name": "memory_search", "status": "running", "arguments": "{}"}}
            yield {"type": "tool", "tool": {"name": "memory_search", "status": "completed", "content": "ok"}}
            yield {"type": "content", "content": "最终答案"}

        with patch("app.services.messages._stream_reply", fake_stream_reply):
            accumulated, usage = await messages._collect_reply_from_stream(
                session=session,
                context=context,
                user_id=1,
            )

        self.assertEqual(accumulated, "最终答案")
        self.assertIsNone(usage)

        events = _recorded_events(session)
        event_types = [event.event_type for event in events]
        self.assertIn("tool_call.created", event_types)
        self.assertIn("message.text.retracted", event_types)
        self.assertIn("assistant.model_text", event_types)
        self.assertIn("commentary.created", event_types)
        self.assertIn("commentary.delta", event_types)
        self.assertIn("commentary.completed", event_types)

        tool_created = next(event for event in events if event.event_type == "tool_call.created")
        step_id = tool_created.step_id
        self.assertTrue(step_id)

        retracted_event = next(event for event in events if event.event_type == "message.text.retracted")
        self.assertEqual(_payload(retracted_event).get("text"), "先看看记忆")

        model_text_event = next(event for event in events if event.event_type == "assistant.model_text")
        self.assertEqual(model_text_event.step_id, step_id)
        self.assertEqual(_payload(model_text_event).get("text"), "先看看记忆")

        commentary_delta = next(event for event in events if event.event_type == "commentary.delta")
        self.assertEqual(commentary_delta.step_id, step_id)
        self.assertEqual(_payload(commentary_delta).get("text"), "先看看记忆")

        # Tool call/result parts are reconstructed from run_events via
        # project_run_view, not written into parts_json directly; only the
        # final answer text should land there.
        assistant_message = context["assistant_message"]
        assert isinstance(assistant_message, Message)
        parts = parts_from_message(assistant_message.parts_json)
        self.assertEqual(parts, [{"type": "text", "text": "最终答案"}])

    async def test_pure_text_round_records_no_retraction_or_commentary(self) -> None:
        context = _make_context()
        session = _make_session()

        async def fake_stream_reply(**_kwargs):
            yield {"type": "content", "content": "直接回答"}

        with patch("app.services.messages._stream_reply", fake_stream_reply):
            accumulated, usage = await messages._collect_reply_from_stream(
                session=session,
                context=context,
                user_id=1,
            )

        self.assertEqual(accumulated, "直接回答")

        events = _recorded_events(session)
        event_types = {event.event_type for event in events}
        self.assertNotIn("message.text.retracted", event_types)
        self.assertNotIn("assistant.model_text", event_types)
        self.assertNotIn("commentary.created", event_types)

        assistant_message = context["assistant_message"]
        assert isinstance(assistant_message, Message)
        parts = parts_from_message(assistant_message.parts_json)
        self.assertEqual(parts, [{"type": "text", "text": "直接回答"}])

    async def test_tool_only_round_with_no_final_answer_leaves_empty_text(self) -> None:
        context = _make_context()
        session = _make_session()

        async def fake_stream_reply(**_kwargs):
            yield {"type": "content", "content": "准备调用工具"}
            yield {"type": "content_retracted", "content": "准备调用工具"}
            yield {"type": "round_commentary", "text": "准备调用工具"}
            yield {"type": "tool", "tool": {"name": "memory_search", "status": "running", "arguments": "{}"}}
            yield {"type": "tool", "tool": {"name": "memory_search", "status": "completed", "content": "ok"}}

        with patch("app.services.messages._stream_reply", fake_stream_reply):
            accumulated, usage = await messages._collect_reply_from_stream(
                session=session,
                context=context,
                user_id=1,
            )

        self.assertEqual(accumulated, "")

        assistant_message = context["assistant_message"]
        assert isinstance(assistant_message, Message)
        parts = parts_from_message(assistant_message.parts_json)
        self.assertEqual(parts, [])

        events = _recorded_events(session)
        event_types = [event.event_type for event in events]
        self.assertIn("assistant.model_text", event_types)
        self.assertIn("message.text.retracted", event_types)


if __name__ == "__main__":
    unittest.main()
