from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.agent_run import AgentRun
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole, MessageStatus
from app.services.messages import RUN_STATUS_CANCELLED, cancel_agent_run, reconcile_interrupted_runs


class _ScalarResult:
    def __init__(self, items: list[AgentRun]) -> None:
        self._items = items

    def all(self) -> list[AgentRun]:
        return self._items


class _RestartSession:
    def __init__(self, runs: list[AgentRun], values: dict[tuple[type[object], int], object]) -> None:
        self.runs = runs
        self.values = values
        self.committed = False

    async def __aenter__(self) -> "_RestartSession":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def scalars(self, *_: object) -> _ScalarResult:
        return _ScalarResult(self.runs)

    async def get(self, model: type[object], identifier: int) -> object | None:
        return self.values.get((model, identifier))

    async def commit(self) -> None:
        self.committed = True


class RunCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_marks_a_persisted_run_when_no_worker_owns_it(self) -> None:
        run = AgentRun(id=7, conversation_id=3, assistant_message_id=11, provider="openai", model="test", status="waiting_approval")
        conversation = Conversation(id=3, user_id=1, title="test")
        message = Message(id=11, conversation_id=3, role=MessageRole.ASSISTANT, content="", status=MessageStatus.STREAMING)
        session = AsyncMock()
        session.get.return_value = message

        with (
            patch("app.services.messages.get_agent_run_for_conversation", AsyncMock(return_value=run)),
            patch("app.services.messages.get_conversation", AsyncMock(return_value=conversation)),
            patch("app.services.messages.agent_runner.cancel", AsyncMock(return_value=False)),
            patch("app.services.messages._cancel_loaded_run", AsyncMock()) as cancel_loaded,
        ):
            result = await cancel_agent_run(session=session, user_id=1, conversation_id=3, run_id=7)

        self.assertIs(result, run)
        cancel_loaded.assert_awaited_once()
        self.assertEqual(cancel_loaded.await_args.kwargs["message"], "Run cancelled by the user.")

    async def test_startup_cancels_runs_left_active_by_a_previous_process(self) -> None:
        run = AgentRun(id=7, conversation_id=3, assistant_message_id=11, provider="openai", model="test", status="waiting_approval")
        conversation = Conversation(id=3, user_id=1, title="test")
        message = Message(id=11, conversation_id=3, role=MessageRole.ASSISTANT, content="", status=MessageStatus.STREAMING)
        session = _RestartSession([run], {(Conversation, 3): conversation, (Message, 11): message})

        with (
            patch("app.services.messages.AsyncSessionLocal", return_value=session),
            patch("app.services.messages._cancel_loaded_run", AsyncMock()) as cancel_loaded,
        ):
            await reconcile_interrupted_runs()

        self.assertTrue(session.committed)
        cancel_loaded.assert_awaited_once()
        self.assertEqual(cancel_loaded.await_args.kwargs["message"], "Run cancelled because the backend restarted.")

    async def test_startup_cancels_run_without_a_recoverable_message(self) -> None:
        run = AgentRun(id=7, conversation_id=3, assistant_message_id=None, provider="openai", model="test", status="running")
        session = _RestartSession([run], {})

        with patch("app.services.messages.AsyncSessionLocal", return_value=session):
            await reconcile_interrupted_runs()

        self.assertTrue(session.committed)
        self.assertEqual(run.status, RUN_STATUS_CANCELLED)


if __name__ == "__main__":
    unittest.main()
