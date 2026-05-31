from __future__ import annotations

from datetime import datetime, timezone
import unittest

from app.models.branch import ConversationBranch
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole, MessageStatus
from app.services.branches import repair_branches_after_message_delete
from app.services.messages import _subtree_messages


class MessageTreeTests(unittest.TestCase):
    def test_subtree_messages_includes_descendants_but_not_siblings(self) -> None:
        messages = [
            self.make_message(id=1, parent_id=None),
            self.make_message(id=2, parent_id=1),
            self.make_message(id=3, parent_id=2),
            self.make_message(id=4, parent_id=1),
        ]

        subtree = _subtree_messages(messages, root_message_id=2)

        self.assertEqual([message.id for message in subtree], [2, 3])

    @staticmethod
    def make_message(*, id: int, parent_id: int | None) -> Message:
        timestamp = datetime(2026, 5, 29, 10, 0, id, tzinfo=timezone.utc)
        return Message(
            id=id,
            conversation_id=123,
            parent_id=parent_id,
            role=MessageRole.USER,
            content=f"message {id}",
            provider=None,
            model=None,
            temperature=None,
            max_tokens=None,
            status=MessageStatus.COMPLETED,
            error_message=None,
            created_at=timestamp,
            updated_at=timestamp,
        )


class BranchRepairTests(unittest.IsolatedAsyncioTestCase):
    async def test_repair_branches_after_message_delete_updates_affected_branches(self) -> None:
        conversation = Conversation(id=123, user_id=1, title="test", current_branch_id=10, current_leaf_message_id=3)
        branches = [
            self.make_branch(id=10, current_leaf_message_id=3, forked_from_message_id=None),
            self.make_branch(id=11, current_leaf_message_id=4, forked_from_message_id=4),
            self.make_branch(id=12, current_leaf_message_id=2, forked_from_message_id=2),
        ]
        session = FakeSession(branches)
        remaining_messages = [
            MessageTreeTests.make_message(id=1, parent_id=None),
            MessageTreeTests.make_message(id=4, parent_id=1),
        ]

        await repair_branches_after_message_delete(
            session=session,
            conversation=conversation,
            deleted_message_ids={2, 3},
            target_parent_id=1,
            remaining_messages=remaining_messages,
        )

        self.assertEqual(branches[0].current_leaf_message_id, 1)
        self.assertEqual(branches[1].current_leaf_message_id, 4)
        self.assertEqual(branches[2].current_leaf_message_id, 1)
        self.assertIsNone(branches[2].forked_from_message_id)
        self.assertEqual(conversation.current_leaf_message_id, 1)

    @staticmethod
    def make_branch(
        *,
        id: int,
        current_leaf_message_id: int | None,
        forked_from_message_id: int | None,
    ) -> ConversationBranch:
        return ConversationBranch(
            id=id,
            conversation_id=123,
            parent_branch_id=None,
            forked_from_message_id=forked_from_message_id,
            current_leaf_message_id=current_leaf_message_id,
            title=None,
            auto_title="branch",
            archived_at=None,
        )


class FakeScalarResult:
    def __init__(self, items):
        self.items = items

    def all(self):
        return self.items


class FakeSession:
    def __init__(self, branches):
        self.branches = branches

    async def scalars(self, _stmt):
        return FakeScalarResult(self.branches)


if __name__ == "__main__":
    unittest.main()
