from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import AsyncMock, patch

from app.models.branch import ConversationBranch
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole, MessageStatus
from app.services.branches import (
    _auto_title_for_branch,
    _branch_message_subtree_root_id,
    _descendant_branch_ids,
    _replacement_branch_after_delete,
    delete_conversation_branch,
    repair_branches_after_message_delete,
)
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

    async def test_branch_message_subtree_root_starts_after_fork_point(self) -> None:
        messages = [
            MessageTreeTests.make_message(id=1, parent_id=None),
            MessageTreeTests.make_message(id=2, parent_id=1),
            MessageTreeTests.make_message(id=3, parent_id=2),
            MessageTreeTests.make_message(id=4, parent_id=3),
        ]
        by_id = {message.id: message for message in messages}
        branch = self.make_branch(id=10, current_leaf_message_id=4, forked_from_message_id=2)

        subtree_root_id = _branch_message_subtree_root_id(branch=branch, by_id=by_id)

        self.assertEqual(subtree_root_id, 3)

    async def test_descendant_branch_ids_includes_nested_children(self) -> None:
        branches = [
            self.make_branch(id=10, current_leaf_message_id=1, forked_from_message_id=None),
            self.make_branch(id=11, current_leaf_message_id=2, forked_from_message_id=1, parent_branch_id=10),
            self.make_branch(id=12, current_leaf_message_id=3, forked_from_message_id=2, parent_branch_id=11),
            self.make_branch(id=13, current_leaf_message_id=4, forked_from_message_id=1, parent_branch_id=10),
        ]

        branch_ids = _descendant_branch_ids(branches=branches, root_branch_id=11)

        self.assertEqual(branch_ids, {11, 12})

    async def test_replacement_branch_after_delete_prefers_next_then_previous_then_main(self) -> None:
        branches = [
            self.make_branch(id=10, current_leaf_message_id=1, forked_from_message_id=None),
            self.make_branch(id=11, current_leaf_message_id=2, forked_from_message_id=1, parent_branch_id=10),
            self.make_branch(id=12, current_leaf_message_id=3, forked_from_message_id=1, parent_branch_id=10),
            self.make_branch(id=13, current_leaf_message_id=4, forked_from_message_id=1, parent_branch_id=10),
        ]

        next_branch = _replacement_branch_after_delete(
            branches=branches,
            deleted_branch_ids={12},
            target_branch_id=12,
        )
        previous_branch = _replacement_branch_after_delete(
            branches=branches,
            deleted_branch_ids={12, 13},
            target_branch_id=12,
        )
        main_branch = _replacement_branch_after_delete(
            branches=branches,
            deleted_branch_ids={11, 12, 13},
            target_branch_id=12,
        )

        self.assertEqual(next_branch.id, 13)
        self.assertEqual(previous_branch.id, 11)
        self.assertEqual(main_branch.id, 10)

    async def test_delete_conversation_branch_switches_current_branch_to_replacement(self) -> None:
        conversation = Conversation(id=123, user_id=1, title="test", current_branch_id=11, current_leaf_message_id=2)
        target_branch = self.make_branch(
            id=11,
            current_leaf_message_id=2,
            forked_from_message_id=2,
            parent_branch_id=10,
        )
        next_branch = self.make_branch(
            id=12,
            current_leaf_message_id=3,
            forked_from_message_id=1,
            parent_branch_id=10,
        )
        main_branch = self.make_branch(
            id=10,
            current_leaf_message_id=1,
            forked_from_message_id=None,
        )
        session = FakeDeleteSession(branches=[main_branch, target_branch, next_branch], messages=[])

        with (
            patch("app.services.branches._get_user_conversation", AsyncMock(return_value=conversation)),
            patch("app.services.branches.get_conversation_branch", AsyncMock(return_value=target_branch)),
            patch("app.services.branches._load_conversation_branches", AsyncMock(return_value=[main_branch, target_branch, next_branch])),
            patch("app.services.branches._load_conversation_messages", AsyncMock(return_value=[])),
            patch("app.services.branches.repair_branches_after_message_delete", AsyncMock()),
        ):
            await delete_conversation_branch(
                session=session,
                user_id=1,
                conversation_id=123,
                branch_id=11,
            )

        self.assertEqual(conversation.current_branch_id, 12)
        self.assertEqual(conversation.current_leaf_message_id, 3)
        self.assertIn(target_branch, session.deleted)
        self.assertNotIn(next_branch, session.deleted)

    async def test_auto_title_for_branch_uses_fork_message_content(self) -> None:
        fork_message = MessageTreeTests.make_message(id=2, parent_id=1)
        fork_message.role = MessageRole.ASSISTANT
        fork_message.content = "assistant answer should become the branch title"

        title = _auto_title_for_branch(fork_message=fork_message)

        self.assertEqual(title, "assistant answer should become the branc")

    @staticmethod
    def make_branch(
        *,
        id: int,
        current_leaf_message_id: int | None,
        forked_from_message_id: int | None,
        parent_branch_id: int | None = None,
    ) -> ConversationBranch:
        return ConversationBranch(
            id=id,
            conversation_id=123,
            parent_branch_id=parent_branch_id,
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


class FakeDeleteSession:
    def __init__(self, branches, messages):
        self.branches = branches
        self.messages = messages
        self.deleted = []

    async def delete(self, item):
        self.deleted.append(item)

    async def commit(self):
        return None

    async def refresh(self, _item):
        return None


if __name__ == "__main__":
    unittest.main()
