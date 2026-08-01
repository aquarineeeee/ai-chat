from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.agent_run import AgentRun
from app.models.conversation import Conversation
from app.models.run_event import RunEvent
from app.services.messages import (
    list_agent_runs_for_conversation,
    list_run_events_for_conversation_run,
    serialize_agent_run,
    serialize_run_event,
)


class _FakeScalarResult:
    def __init__(self, items):
        self._items = list(items)

    def all(self):
        return list(self._items)


class RunRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_agent_runs_for_conversation_filters_by_owner_conversation(self) -> None:
        conversation = Conversation(id=123, user_id=1, title="test")
        runs = [
            AgentRun(id=2, conversation_id=123, provider="openai", model="gpt-4.1-mini", status="running", last_sequence=4),
            AgentRun(id=1, conversation_id=123, provider="openai", model="gpt-4.1-mini", status="completed", last_sequence=9),
        ]
        session = AsyncMock()
        session.scalars.return_value = _FakeScalarResult(runs)

        with patch("app.services.messages.get_conversation", AsyncMock(return_value=conversation)):
            result = await list_agent_runs_for_conversation(
                session=session,
                user_id=1,
                conversation_id=123,
                status="running",
            )

        self.assertEqual([item.id for item in result], [2, 1])
        session.scalars.assert_awaited_once()

    async def test_list_run_events_for_conversation_run_requires_run_to_belong_to_conversation(self) -> None:
        conversation = Conversation(id=123, user_id=1, title="test")
        run = AgentRun(id=9, conversation_id=123, provider="openai", model="gpt-4.1-mini", status="running", last_sequence=8)
        events = [
            RunEvent(
                id=1,
                event_id="evt_1",
                run_id=9,
                sequence=7,
                event_type="tool_call.started",
                payload_json='{"tool_name":"memory_search"}',
                schema_version=1,
            ),
            RunEvent(
                id=2,
                event_id="evt_2",
                run_id=9,
                sequence=8,
                event_type="tool_call.completed",
                payload_json='{"tool_name":"memory_search"}',
                schema_version=1,
            ),
        ]
        session = AsyncMock()
        session.scalar.return_value = run
        session.scalars.return_value = _FakeScalarResult(events)

        with patch("app.services.messages.get_conversation", AsyncMock(return_value=conversation)):
            result = await list_run_events_for_conversation_run(
                session=session,
                user_id=1,
                conversation_id=123,
                run_id=9,
                after_sequence=6,
            )

        self.assertEqual([item.sequence for item in result], [7, 8])
        session.scalar.assert_awaited_once()
        session.scalars.assert_awaited_once()

    def test_serialize_run_event_restores_payload_dict(self) -> None:
        event = RunEvent(
            id=1,
            event_id="evt_1",
            run_id=9,
            assistant_message_id=77,
            tool_call_ref="tc_1",
            sequence=8,
            event_type="tool_call.completed",
            payload_json='{"tool_name":"memory_search","display_output_preview":"done"}',
            schema_version=1,
        )

        serialized = serialize_run_event(event)

        self.assertEqual(serialized["type"], "tool_call.completed")
        self.assertEqual(serialized["payload"]["tool_name"], "memory_search")
        self.assertEqual(serialized["tool_call_ref"], "tc_1")
        self.assertIsNone(serialized["step_id"])

    def test_serialize_agent_run_includes_resume_fields(self) -> None:
        run = AgentRun(
            id=3,
            conversation_id=123,
            user_message_id=10,
            assistant_message_id=11,
            provider="openai",
            model="gpt-4.1-mini",
            status="running",
            last_sequence=5,
            resume_token="run_123",
            error_message=None,
            metadata_json='{"pending_approval":{"tool_call_ref":"tc_1"}}',
        )

        serialized = serialize_agent_run(run)

        self.assertEqual(serialized["id"], 3)
        self.assertEqual(serialized["resume_token"], "run_123")
        self.assertEqual(serialized["last_sequence"], 5)
        self.assertEqual(serialized["metadata"]["pending_approval"]["tool_call_ref"], "tc_1")


if __name__ == "__main__":
    unittest.main()
