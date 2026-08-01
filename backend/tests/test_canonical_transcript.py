from __future__ import annotations

import unittest

from app.canonical_transcript import build_assistant_transcript_from_trace
from app.models.message import Message, MessageRole, MessageStatus
from app.models.run_event import RunEvent
from app.models.tool_call import ToolCall


class CanonicalTranscriptTests(unittest.TestCase):
    def test_build_assistant_transcript_replays_text_tool_text_in_order(self) -> None:
        message = Message(
            id=10,
            conversation_id=1,
            parent_id=9,
            role=MessageRole.ASSISTANT,
            content="before after",
            status=MessageStatus.COMPLETED,
        )
        events = [
            RunEvent(
                assistant_message_id=10,
                event_type="message.text.delta",
                payload_json='{"text":"before "}',
                tool_call_ref=None,
                run_id=1,
                sequence=1,
                event_id="evt_1",
                schema_version=1,
            ),
            RunEvent(
                assistant_message_id=10,
                event_type="tool_call.started",
                payload_json='{"tool_name":"memory_search","input_for_model_json":"{\\"query\\":\\"x\\"}"}',
                tool_call_ref="tc_1",
                run_id=1,
                sequence=2,
                event_id="evt_2",
                schema_version=1,
            ),
            RunEvent(
                assistant_message_id=10,
                event_type="tool_call.completed",
                payload_json='{"tool_name":"memory_search","output_for_model_json":"result"}',
                tool_call_ref="tc_1",
                run_id=1,
                sequence=3,
                event_id="evt_3",
                schema_version=1,
            ),
            RunEvent(
                assistant_message_id=10,
                event_type="message.text.delta",
                payload_json='{"text":"after"}',
                tool_call_ref=None,
                run_id=1,
                sequence=4,
                event_id="evt_4",
                schema_version=1,
            ),
        ]
        tool_call = ToolCall(
            assistant_message_id=10,
            conversation_id=1,
            run_id=1,
            tool_call_id="tc_1",
            tool_name="memory_search",
            sequence_index=2,
            status="success",
            input_for_model_json='{"query":"x"}',
            output_for_model_json='{"result":"ok"}',
        )

        transcript = build_assistant_transcript_from_trace(
            message=message,
            events=events,
            tool_calls_by_ref={"tc_1": tool_call},
        )

        self.assertEqual(
            [(item.kind, item.text, item.tool_name, item.arguments, item.result) for item in transcript],
            [
                ("assistant_text", "before ", "", "", ""),
                ("assistant_tool_call", "", "memory_search", '{"query":"x"}', ""),
                ("tool_result", "", "memory_search", "", '{"result":"ok"}'),
                ("assistant_text", "after", "", "", ""),
            ],
        )

    def test_build_assistant_transcript_ignores_retracted_tool_round_text(self) -> None:
        message = Message(
            id=11,
            conversation_id=1,
            parent_id=10,
            role=MessageRole.ASSISTANT,
            content="final",
            status=MessageStatus.COMPLETED,
        )
        events = [
            RunEvent(
                assistant_message_id=11,
                event_type="message.text.delta",
                payload_json='{"text":"tool preface"}',
                tool_call_ref=None,
                run_id=1,
                sequence=1,
                event_id="evt_1",
                schema_version=1,
            ),
            RunEvent(
                assistant_message_id=11,
                event_type="message.text.retracted",
                payload_json='{"text":"tool preface"}',
                tool_call_ref=None,
                run_id=1,
                sequence=2,
                event_id="evt_2",
                schema_version=1,
            ),
            RunEvent(
                assistant_message_id=11,
                event_type="tool_call.started",
                payload_json='{"tool_name":"memory_search","input_for_model_json":"{\\"query\\":\\"x\\"}"}',
                tool_call_ref="tc_1",
                run_id=1,
                sequence=3,
                event_id="evt_3",
                schema_version=1,
            ),
            RunEvent(
                assistant_message_id=11,
                event_type="tool_call.completed",
                payload_json='{"tool_name":"memory_search","output_for_model_json":"result"}',
                tool_call_ref="tc_1",
                run_id=1,
                sequence=4,
                event_id="evt_4",
                schema_version=1,
            ),
            RunEvent(
                assistant_message_id=11,
                event_type="message.text.delta",
                payload_json='{"text":"final"}',
                tool_call_ref=None,
                run_id=1,
                sequence=5,
                event_id="evt_5",
                schema_version=1,
            ),
        ]

        transcript = build_assistant_transcript_from_trace(
            message=message,
            events=events,
            tool_calls_by_ref={},
        )

        self.assertEqual([item.text for item in transcript if item.kind == "assistant_text"], ["final"])


if __name__ == "__main__":
    unittest.main()
