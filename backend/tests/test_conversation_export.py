from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
import unittest

from app.core.exceptions import AppError
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole, MessageStatus
from app.services.conversation_export import (
    ExportFormat,
    ExportScope,
    _build_export_filename,
    _lineage_messages,
    _render_json_export,
    _render_markdown_export,
    _select_messages_for_scope,
    _validate_export_capability,
)


class ConversationExportTests(unittest.TestCase):
    def test_validate_export_capability_rejects_unsupported_combination(self) -> None:
        with self.assertRaises(AppError) as exc_info:
            _validate_export_capability(
                export_format=ExportFormat.MARKDOWN,
                scope=ExportScope.ALL_BRANCHES,
            )

        self.assertEqual(exc_info.exception.code, "UNSUPPORTED_EXPORT_COMBINATION")

    def test_select_messages_for_current_branch_returns_lineage(self) -> None:
        messages = [
            self.make_message(id=1, role=MessageRole.USER, content="root", parent_id=None),
            self.make_message(id=2, role=MessageRole.ASSISTANT, content="branch a", parent_id=1),
            self.make_message(id=3, role=MessageRole.ASSISTANT, content="branch b", parent_id=1),
        ]

        selected, warnings = _select_messages_for_scope(
            messages=messages,
            scope=ExportScope.CURRENT_BRANCH,
            current_leaf_message_id=3,
        )

        self.assertEqual([item.id for item in selected], [1, 3])
        self.assertEqual(warnings, [])

    def test_select_messages_for_current_branch_requires_valid_leaf(self) -> None:
        messages = [self.make_message(id=1, role=MessageRole.USER, content="root", parent_id=None)]

        with self.assertRaises(AppError) as exc_info:
            _select_messages_for_scope(
                messages=messages,
                scope=ExportScope.CURRENT_BRANCH,
                current_leaf_message_id=None,
            )

        self.assertEqual(exc_info.exception.code, "INVALID_BRANCH_STATE")

    def test_lineage_messages_detects_cycles(self) -> None:
        messages = [
            self.make_message(id=1, role=MessageRole.USER, content="a", parent_id=2),
            self.make_message(id=2, role=MessageRole.ASSISTANT, content="b", parent_id=1),
        ]

        with self.assertRaises(AppError) as exc_info:
            _lineage_messages(messages=messages, leaf_id=2)

        self.assertEqual(exc_info.exception.code, "INVALID_BRANCH_STATE")

    def test_render_markdown_export_includes_only_exportable_messages(self) -> None:
        conversation = self.make_conversation(system_prompt="你是助手。")
        exported_at = datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)
        messages = [
            self.make_message(id=1, role=MessageRole.USER, content="你好", parent_id=None),
            self.make_message(
                id=2,
                role=MessageRole.ASSISTANT,
                content="半截回答",
                parent_id=1,
                status=MessageStatus.PARTIAL,
            ),
            self.make_message(
                id=3,
                role=MessageRole.ASSISTANT,
                content="失败回答",
                parent_id=2,
                status=MessageStatus.FAILED,
                error_message="provider timeout",
            ),
            self.make_message(
                id=4,
                role=MessageRole.ASSISTANT,
                content="正在生成",
                parent_id=3,
                status=MessageStatus.STREAMING,
            ),
        ]

        content = _render_markdown_export(
            conversation=conversation,
            messages=messages,
            exported_at=exported_at,
            scope=ExportScope.CURRENT_BRANCH,
        )

        self.assertIn("> Scope: current_branch", content)
        self.assertIn("## System", content)
        self.assertIn("> Status: partial", content)
        self.assertIn("> Status: failed", content)
        self.assertIn("> Error: provider timeout", content)
        self.assertNotIn("正在生成", content)

    def test_render_json_export_keeps_tree_metadata(self) -> None:
        conversation = self.make_conversation(current_leaf_message_id=2, temperature=Decimal("0.70"))
        exported_at = datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)
        messages = [
            self.make_message(id=1, role=MessageRole.USER, content="你好", parent_id=None),
            self.make_message(
                id=2,
                role=MessageRole.ASSISTANT,
                content="你好，有什么可以帮你？",
                parent_id=1,
                temperature=Decimal("0.50"),
            ),
        ]

        content = _render_json_export(
            conversation=conversation,
            messages=messages,
            exported_at=exported_at,
            scope=ExportScope.ALL_BRANCHES,
            warnings=["current_leaf_message_id does not reference an existing message"],
        )
        payload = json.loads(content)

        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["type"], "ai-chat.conversation_export")
        self.assertEqual(payload["scope"], "all_branches")
        self.assertEqual(payload["conversation"]["temperature"], "0.70")
        self.assertEqual(payload["messages"][1]["parent_id"], 1)
        self.assertEqual(payload["messages"][1]["temperature"], "0.50")
        self.assertEqual(payload["warnings"], ["current_leaf_message_id does not reference an existing message"])

    def test_build_export_filename_sanitizes_title(self) -> None:
        filename = _build_export_filename(
            title='  错误:/标题?*  ',
            conversation_id=9,
            scope=ExportScope.CURRENT_BRANCH,
            export_format=ExportFormat.JSON,
            exported_at=datetime(2026, 5, 23, 12, 34, 56, tzinfo=timezone.utc),
        )

        self.assertEqual(filename, "错误 标题-current-branch-20260523-123456.json")

    @staticmethod
    def make_conversation(
        *,
        title: str = "示例会话",
        system_prompt: str | None = None,
        current_leaf_message_id: int | None = None,
        temperature: Decimal | None = Decimal("0.70"),
    ) -> Conversation:
        return Conversation(
            id=123,
            user_id=1,
            title=title,
            system_prompt=system_prompt,
            provider="openai",
            model="gpt-4.1-mini",
            temperature=temperature,
            max_tokens=4096,
            current_leaf_message_id=current_leaf_message_id,
            created_at=datetime(2026, 5, 23, 10, 0, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 5, 23, 10, 30, 0, tzinfo=timezone.utc),
        )

    @staticmethod
    def make_message(
        *,
        id: int,
        role: MessageRole,
        content: str,
        parent_id: int | None,
        status: MessageStatus = MessageStatus.COMPLETED,
        error_message: str | None = None,
        temperature: Decimal | None = None,
    ) -> Message:
        return Message(
            id=id,
            conversation_id=123,
            parent_id=parent_id,
            role=role,
            content=content,
            provider=None,
            model=None,
            temperature=temperature,
            max_tokens=None,
            status=status,
            error_message=error_message,
            created_at=datetime(2026, 5, 23, 10, 0, id, tzinfo=timezone.utc),
            updated_at=datetime(2026, 5, 23, 10, 0, id, tzinfo=timezone.utc),
        )


if __name__ == "__main__":
    unittest.main()
