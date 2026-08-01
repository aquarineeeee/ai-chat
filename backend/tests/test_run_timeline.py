from __future__ import annotations

import unittest

from app.models.agent_run import AgentRun
from app.models.run_event import RunEvent
from app.models.tool_call import ToolCall
from app.services.run_timeline import project_run_view


class RunTimelineProjectionTests(unittest.TestCase):
    def test_project_run_view_groups_commentary_between_tool_steps(self) -> None:
        run = AgentRun(
            id=9,
            conversation_id=123,
            assistant_message_id=77,
            provider="openai",
            model="gpt-4.1-mini",
            status="running",
            last_sequence=8,
            metadata_json='{"phase":"running_tool"}',
        )
        tool_calls = [
            ToolCall(
                run_id=9,
                conversation_id=123,
                assistant_message_id=77,
                tool_call_id="tc_1",
                tool_name="memory_search",
                sequence_index=1,
                status="success",
                display_input_preview='{"query":"x"}',
                display_output_preview="result A",
            ),
            ToolCall(
                run_id=9,
                conversation_id=123,
                assistant_message_id=77,
                tool_call_id="tc_2",
                tool_name="web_search",
                sequence_index=5,
                status="running",
                display_input_preview='{"q":"y"}',
            ),
        ]
        events = [
            RunEvent(id=1, event_id="evt_1", run_id=9, assistant_message_id=77, step_id="step_1", tool_call_ref="tc_1", sequence=1, event_type="tool_call.created", payload_json='{"tool_name":"memory_search"}', schema_version=1),
            RunEvent(id=2, event_id="evt_2", run_id=9, assistant_message_id=77, step_id="step_1", tool_call_ref="tc_1", sequence=2, event_type="tool_call.completed", payload_json='{"tool_name":"memory_search","display_output_preview":"result A"}', schema_version=1),
            RunEvent(id=3, event_id="evt_3", run_id=9, assistant_message_id=77, step_id="step_1", tool_call_ref="tc_1", sequence=3, event_type="commentary.created", payload_json='{"commentary_id":"cm_1","source":"model","style":"progress","text":""}', schema_version=1),
            RunEvent(id=4, event_id="evt_4", run_id=9, assistant_message_id=77, step_id="step_1", tool_call_ref="tc_1", sequence=4, event_type="commentary.delta", payload_json='{"commentary_id":"cm_1","source":"model","style":"progress","text":"A"}', schema_version=1),
            RunEvent(id=5, event_id="evt_5", run_id=9, assistant_message_id=77, step_id="step_2", tool_call_ref="tc_2", sequence=5, event_type="tool_call.created", payload_json='{"tool_name":"web_search"}', schema_version=1),
            RunEvent(id=6, event_id="evt_6", run_id=9, assistant_message_id=77, step_id="step_2", tool_call_ref="tc_2", sequence=6, event_type="commentary.created", payload_json='{"commentary_id":"cm_2","source":"system","style":"status","text":""}', schema_version=1),
            RunEvent(id=7, event_id="evt_7", run_id=9, assistant_message_id=77, step_id="step_2", tool_call_ref="tc_2", sequence=7, event_type="commentary.delta", payload_json='{"commentary_id":"cm_2","source":"system","style":"status","text":"B"}', schema_version=1),
            RunEvent(id=8, event_id="evt_8", run_id=9, assistant_message_id=77, step_id=None, tool_call_ref=None, sequence=8, event_type="run.phase.changed", payload_json='{"phase":"running_tool"}', schema_version=1),
        ]

        view = project_run_view(run=run, events=events, tool_calls=tool_calls)

        self.assertEqual(view["phase"], "running_tool")
        self.assertEqual(
            [item["type"] for item in view["items"]],
            ["tool_step", "processed_group", "tool_step", "processed_group"],
        )
        self.assertEqual(view["items"][0]["tool_name"], "memory_search")
        self.assertEqual(view["items"][1]["commentary"][0]["text"], "A")
        self.assertEqual(view["items"][2]["tool_name"], "web_search")
        self.assertEqual(view["items"][3]["commentary"][0]["text"], "B")


if __name__ == "__main__":
    unittest.main()
