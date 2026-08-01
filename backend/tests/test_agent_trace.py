from __future__ import annotations

import unittest

from app.services.agent_trace import (
    aggregate_text_from_parts,
    apply_run_event_to_parts,
    build_preview,
    sanitize_tool_input_for_display,
    sanitize_tool_output_for_display,
)


class AgentTraceProjectionTests(unittest.TestCase):
    def test_apply_events_reconstructs_text_tool_text_order(self) -> None:
        parts: list[dict[str, object]] = []
        parts = apply_run_event_to_parts(
            parts,
            event_type="message.text.delta",
            payload={"text": "先解释一下"},
            tool_call_ref=None,
        )
        parts = apply_run_event_to_parts(
            parts,
            event_type="tool_call.created",
            payload={"tool_name": "memory_search", "display_input_preview": '{"query":"项目约束"}'},
            tool_call_ref="tc_1",
        )
        parts = apply_run_event_to_parts(
            parts,
            event_type="tool_call.started",
            payload={"tool_name": "memory_search"},
            tool_call_ref="tc_1",
        )
        parts = apply_run_event_to_parts(
            parts,
            event_type="tool_call.completed",
            payload={"tool_name": "memory_search", "display_output_preview": "找到两条相关记忆"},
            tool_call_ref="tc_1",
        )
        parts = apply_run_event_to_parts(
            parts,
            event_type="message.text.delta",
            payload={"text": "，现在继续回答"},
            tool_call_ref=None,
        )

        self.assertEqual(
            [part["type"] for part in parts],
            ["text", "tool_call", "tool_result", "text"],
        )
        self.assertEqual(parts[1]["status"], "completed")
        self.assertEqual(parts[2]["content"], "找到两条相关记忆")
        self.assertEqual(aggregate_text_from_parts(parts), "先解释一下，现在继续回答")

    def test_tool_failure_appends_error_part(self) -> None:
        parts = apply_run_event_to_parts(
            [],
            event_type="tool_call.created",
            payload={"tool_name": "memory_write", "display_input_preview": '{"content":"foo"}'},
            tool_call_ref="tc_2",
        )
        parts = apply_run_event_to_parts(
            parts,
            event_type="tool_call.failed",
            payload={"tool_name": "memory_write", "error_message": "权限不足"},
            tool_call_ref="tc_2",
        )

        self.assertEqual(parts[-1]["type"], "error")
        self.assertEqual(parts[-1]["message"], "权限不足")

    def test_text_retraction_removes_previously_streamed_text(self) -> None:
        parts: list[dict[str, object]] = []
        parts = apply_run_event_to_parts(
            parts,
            event_type="message.text.delta",
            payload={"text": "工具前说明"},
            tool_call_ref=None,
        )
        parts = apply_run_event_to_parts(
            parts,
            event_type="message.text.retracted",
            payload={"text": "工具前说明"},
            tool_call_ref=None,
        )
        parts = apply_run_event_to_parts(
            parts,
            event_type="message.text.delta",
            payload={"text": "最终回答"},
            tool_call_ref=None,
        )

        self.assertEqual([part["type"] for part in parts], ["text"])
        self.assertEqual(aggregate_text_from_parts(parts), "最终回答")


class AgentTraceSanitizeTests(unittest.TestCase):
    def test_sanitize_redacts_sensitive_keys_and_bearer_tokens(self) -> None:
        value = {
            "authorization": "Bearer secret-token",
            "nested": {"api_key": "abc123"},
            "normal": "kept",
        }

        sanitized_input = sanitize_tool_input_for_display(value)
        sanitized_output = sanitize_tool_output_for_display(value)

        self.assertEqual(sanitized_input["authorization"], "[REDACTED]")
        self.assertEqual(sanitized_input["nested"]["api_key"], "[REDACTED]")
        self.assertEqual(sanitized_input["normal"], "kept")
        self.assertEqual(sanitized_output["authorization"], "[REDACTED]")

    def test_build_preview_truncates_long_strings(self) -> None:
        preview = build_preview("x" * 1300, max_chars=20)
        self.assertEqual(preview, ("x" * 20) + "...")


class AgentTraceApprovalTests(unittest.TestCase):
    def test_tool_approval_updates_parts(self) -> None:
        parts = apply_run_event_to_parts(
            [],
            event_type="tool_call.created",
            payload={"tool_name": "memory_write", "display_input_preview": '{"content":"foo"}'},
            tool_call_ref="tc_3",
        )
        parts = apply_run_event_to_parts(
            parts,
            event_type="tool_call.approval.requested",
            payload={"tool_name": "memory_write"},
            tool_call_ref="tc_3",
        )
        parts = apply_run_event_to_parts(
            parts,
            event_type="tool_call.approval.denied",
            payload={"tool_name": "memory_write", "comment": "manual reject"},
            tool_call_ref="tc_3",
        )

        self.assertEqual(parts[0]["status"], "denied")
        self.assertEqual(parts[1]["type"], "approval")
        self.assertEqual(parts[1]["status"], "denied")
        self.assertEqual(parts[1]["message"], "manual reject")


if __name__ == "__main__":
    unittest.main()
