from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.exc import PendingRollbackError

from app.canonical_transcript import user_text_item
from app.models.agent_run import AgentRun
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole, MessageStatus
from app.services import messages
from app.services.agent_runner import InProcessAgentRunner


def rows():
    run = AgentRun(id=10, conversation_id=2, user_message_id=3, assistant_message_id=4,
                   provider="openai", model="test", status="running", last_sequence=0,
                   metadata_json=json.dumps({"attachment_ids": [8, 9]}))
    conversation = Conversation(id=2, user_id=1, provider="openai")
    assistant = Message(id=4, conversation_id=2, parent_id=3, role=MessageRole.ASSISTANT,
                        content="", status=MessageStatus.STREAMING)
    return run, conversation, assistant


def session_for(run, conversation, assistant):
    session = MagicMock()
    for name in ("commit", "rollback", "flush", "refresh"):
        setattr(session, name, AsyncMock())
    session.scalar = AsyncMock(return_value=run)
    async def get(model, id, **kwargs):
        return {AgentRun: run, Conversation: conversation, Message: assistant}[model]
    session.get = AsyncMock(side_effect=get)
    return session


def payload():
    return dict(run_id=10, user_id=1, conversation_id=2, assistant_message_id=4,
                provider="openai", adapter_id="openai_chat_completions", model="test",
                prompt_transcript=[user_text_item("hello")], activate_branch=True,
                failure_leaf_message_id=3)


class VisionRunLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_execution_uses_fresh_session_after_poisoned_transaction_closes(self):
        run, conversation, assistant = rows()
        session = session_for(run, conversation, assistant)
        terminal_session = session_for(run, conversation, assistant)
        execution_closed = asyncio.Event()
        factory = MagicMock()
        execution_scope = MagicMock()
        execution_scope.__aenter__ = AsyncMock(return_value=session)
        async def exit_execution(*args):
            execution_closed.set()
        execution_scope.__aexit__ = AsyncMock(side_effect=exit_execution)
        terminal_scope = MagicMock()
        terminal_scope.__aenter__ = AsyncMock(return_value=terminal_session)
        terminal_scope.__aexit__ = AsyncMock(return_value=False)
        factory.side_effect = [execution_scope, terminal_scope]
        poisoned = False
        async def collect(**kwargs):
            nonlocal poisoned
            poisoned = True
            raise RuntimeError("database write was interrupted")
        async def current_run(statement):
            if poisoned:
                raise PendingRollbackError("Transaction must be rolled back")
            return run
        async def fresh_current_run(statement):
            self.assertTrue(execution_closed.is_set())
            return run
        session.scalar.side_effect = current_run
        terminal_session.scalar.side_effect = fresh_current_run
        with patch.object(messages, "AsyncSessionLocal", factory), patch.object(messages, "agent_runner", InProcessAgentRunner()), \
             patch.object(messages, "_collect_reply_from_stream", collect), \
             patch.object(messages, "lock_owned_conversation", AsyncMock()), \
             patch.object(messages, "close_runtime_sessions", AsyncMock()):
            await messages._execute_background_run(payload())
        self.assertEqual(run.status, "failed")
        session.scalar.assert_not_awaited()
        self.assertTrue(execution_closed.is_set())
        terminal_session.commit.assert_awaited_once()

    async def test_cancel_flag_before_registration_prevents_provider_start(self):
        run, conversation, assistant = rows()
        run.metadata_json = json.dumps({"attachment_ids": [8, 9], "cancel_requested": True})
        session = session_for(run, conversation, assistant)
        factory = MagicMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)
        runner = InProcessAgentRunner()
        provider = AsyncMock(return_value=("unused", None))
        terminal = AsyncMock()
        with patch.object(messages, "AsyncSessionLocal", factory), patch.object(messages, "agent_runner", runner), \
             patch.object(messages, "_collect_reply_from_stream", provider), patch.object(messages, "_mark_cancelled", terminal), \
             patch.object(messages, "close_runtime_sessions", AsyncMock()):
            runner.start(10, messages._execute_background_run(payload()))
            await runner.wait(10)
        provider.assert_not_awaited()
        terminal.assert_awaited_once()
        self.assertEqual(json.loads(run.metadata_json)["attachment_ids"], [8, 9])

    async def test_cancel_while_preparing_is_cooperative_and_enters_cleanup(self):
        runner = InProcessAgentRunner()
        prepared = asyncio.Event()
        continue_preparing = asyncio.Event()
        cleanup = asyncio.Event()
        async def job():
            prepared.set()
            await continue_preparing.wait()
            try:
                if not runner.execution_ready(10):
                    raise asyncio.CancelledError()
                self.fail("Cancelled task must not execute provider")
            except asyncio.CancelledError:
                cleanup.set()
            finally:
                runner.execution_stopping(10)
        runner.start(10, job())
        await prepared.wait()
        self.assertTrue(await runner.cancel(10))
        self.assertFalse(cleanup.is_set())
        continue_preparing.set()
        await runner.wait(10)
        self.assertTrue(cleanup.is_set())

    async def test_waiter_disconnect_preserves_executing_task(self):
        runner = InProcessAgentRunner()
        started, finish, complete = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def job():
            started.set()
            await finish.wait()
            complete.set()
        runner.start(10, job())
        await started.wait()
        waiter = asyncio.create_task(runner.wait(10))
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertTrue(runner.is_running(10))
        finish.set()
        await runner.wait(10)
        self.assertTrue(complete.is_set())

    async def test_cancel_terminal_waits_for_provider_generator_cleanup(self):
        run, conversation, assistant = rows()
        session = session_for(run, conversation, assistant)
        factory = MagicMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)
        runner = InProcessAgentRunner()
        cleanup_started, cleanup_allowed, resources_freed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        terminal_called = asyncio.Event()
        async def provider_stream(**kwargs):
            try:
                yield {"type": "content", "content": "partial"}
                await asyncio.Event().wait()
            finally:
                cleanup_started.set()
                await cleanup_allowed.wait()
                resources_freed.set()
        async def record_delta(**kwargs):
            raise asyncio.CancelledError()
        async def terminal(**kwargs):
            self.assertTrue(resources_freed.is_set())
            self.assertFalse(kwargs["context"]["execution_active"])
            run.status = "cancelled"
            terminal_called.set()
        with patch.object(messages, "AsyncSessionLocal", factory), patch.object(messages, "agent_runner", runner), \
             patch.object(messages, "_stream_reply", provider_stream), patch.object(messages, "_record_text_delta", record_delta), \
             patch.object(messages, "_mark_cancelled", terminal), patch.object(messages, "close_runtime_sessions", AsyncMock()):
            runner.start(10, messages._execute_background_run(payload()))
            await cleanup_started.wait()
            self.assertEqual(run.status, "running")
            self.assertFalse(terminal_called.is_set())
            self.assertEqual(json.loads(run.metadata_json)["attachment_ids"], [8, 9])
            cleanup_allowed.set()
            await runner.wait(10)
        self.assertTrue(terminal_called.is_set())
        self.assertEqual(run.status, "cancelled")

    async def test_success_and_failure_wait_for_provider_cleanup_before_terminal(self):
        for outcome in ("completed", "failed"):
            with self.subTest(outcome=outcome):
                run, conversation, assistant = rows()
                session = session_for(run, conversation, assistant)
                factory = MagicMock()
                factory.return_value.__aenter__ = AsyncMock(return_value=session)
                factory.return_value.__aexit__ = AsyncMock(return_value=False)
                runner = InProcessAgentRunner()
                cleanup_started, cleanup_allowed, resources_freed = asyncio.Event(), asyncio.Event(), asyncio.Event()
                terminal_called = asyncio.Event()
                async def provider_stream(**kwargs):
                    try:
                        yield {"type": "content", "content": "done"}
                        if outcome == "failed":
                            raise RuntimeError("provider failed")
                    finally:
                        cleanup_started.set()
                        await cleanup_allowed.wait()
                        resources_freed.set()
                async def terminal(**kwargs):
                    self.assertTrue(resources_freed.is_set())
                    run.status = outcome
                    terminal_called.set()
                terminal_name = "_finalize_success" if outcome == "completed" else "_mark_failed"
                with patch.object(messages, "AsyncSessionLocal", factory), patch.object(messages, "agent_runner", runner), \
                     patch.object(messages, "_stream_reply", provider_stream), patch.object(messages, "_record_text_delta", AsyncMock()), \
                     patch.object(messages, terminal_name, terminal), patch.object(messages, "close_runtime_sessions", AsyncMock()):
                    runner.start(10, messages._execute_background_run(payload()))
                    await cleanup_started.wait()
                    self.assertEqual(run.status, "running")
                    self.assertFalse(terminal_called.is_set())
                    cleanup_allowed.set()
                    await runner.wait(10)
                self.assertTrue(terminal_called.is_set())

    async def test_late_event_observes_current_cancel_flag_without_appending_event(self):
        stale, conversation, assistant = rows()
        current, _, _ = rows()
        current.metadata_json = json.dumps({"attachment_ids": [8, 9], "cancel_requested": True})
        session = session_for(current, conversation, assistant)
        context = {"agent_run": stale, "conversation": conversation, "assistant_message": assistant, "execution_active": True}
        with self.assertRaises(asyncio.CancelledError):
            await messages._record_run_event(session=session, context=context, event_type="message.text.delta", payload={"text": "late"})
        session.add.assert_not_called()
        self.assertEqual(current.last_sequence, 0)

    async def test_current_cancel_state_overrides_late_success_and_preserves_protection_ids(self):
        stale, conversation, assistant = rows()
        current, _, _ = rows()
        current.metadata_json = json.dumps({"attachment_ids": [8, 9], "cancel_requested": True})
        session = session_for(current, conversation, assistant)
        context = {"agent_run": stale, "failure_leaf_message_id": 3}
        with patch.object(messages, "lock_owned_conversation", AsyncMock()), \
             patch.object(messages, "_record_run_event", AsyncMock()) as event:
            await messages._commit_terminal(session=session, context=context, conversation=conversation, branch=None,
                 assistant_message=assistant, run_status="completed", content="late success", usage=None,
                 error_message=None, leaf_message_id=4, activate_branch=True)
        self.assertEqual(current.status, "cancelled")
        self.assertEqual(assistant.content, "")
        self.assertEqual(conversation.current_leaf_message_id, 3)
        self.assertEqual(json.loads(current.metadata_json)["attachment_ids"], [8, 9])
        self.assertTrue(json.loads(current.metadata_json)["cancel_requested"])
        self.assertEqual(event.await_args.kwargs["event_type"], "run.cancelled")
        statement = session.scalar.await_args.args[0]
        self.assertIsNotNone(statement._for_update_arg)
        self.assertTrue(statement.get_execution_options()["populate_existing"])

    async def test_existing_terminal_cannot_be_overwritten_by_late_success_or_failure(self):
        for attempted in ("completed", "failed"):
            with self.subTest(attempted=attempted):
                stale, conversation, assistant = rows()
                current, _, _ = rows()
                current.status = "cancelled"
                session = session_for(current, conversation, assistant)
                with patch.object(messages, "lock_owned_conversation", AsyncMock()):
                    await messages._commit_terminal(session=session, context={"agent_run": stale},
                         conversation=conversation, branch=None, assistant_message=assistant,
                         run_status=attempted, content="late", usage=None, error_message="late",
                         leaf_message_id=4, activate_branch=True)
                self.assertEqual(current.status, "cancelled")
                self.assertEqual(assistant.content, "")
                session.commit.assert_not_awaited()
                session.rollback.assert_awaited_once()

    async def test_cancel_endpoint_commits_flag_before_notifying_and_keeps_run_nonterminal(self):
        stale, conversation, assistant = rows()
        current, _, _ = rows()
        session = session_for(current, conversation, assistant)
        order = []
        async def commit():
            order.append("commit")
        async def notify(id):
            self.assertEqual(order, ["commit"])
            self.assertTrue(json.loads(current.metadata_json)["cancel_requested"])
            order.append("notify")
            return False  # Registration is allowed to happen later.
        session.commit.side_effect = commit
        runner = MagicMock(cancel=AsyncMock(side_effect=notify))
        with patch.object(messages, "get_agent_run_for_conversation", AsyncMock(return_value=stale)), \
             patch.object(messages, "agent_runner", runner):
            result = await messages.cancel_agent_run(session=session, user_id=1, conversation_id=2, run_id=10)
        self.assertIs(result, current)
        self.assertEqual(current.status, "running")
        self.assertEqual(json.loads(current.metadata_json)["attachment_ids"], [8, 9])
        self.assertEqual(order, ["commit", "notify"])

    async def test_event_metadata_update_uses_current_row_and_preserves_cancel_and_images(self):
        stale, conversation, assistant = rows()
        current, _, _ = rows()
        current.metadata_json = json.dumps({"attachment_ids": [8, 9], "cancel_requested": True})
        session = session_for(current, conversation, assistant)
        context = {"agent_run": stale, "conversation": conversation, "assistant_message": assistant}
        await messages._record_run_event(session=session, context=context, event_type="run.phase.changed", payload={"phase": "starting"})
        metadata = json.loads(current.metadata_json)
        self.assertEqual(metadata["attachment_ids"], [8, 9])
        self.assertTrue(metadata["cancel_requested"])
        self.assertEqual(metadata["phase"], "starting")
        self.assertIs(context["agent_run"], current)


if __name__ == "__main__":
    unittest.main()
