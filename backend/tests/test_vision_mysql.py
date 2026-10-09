"""Opt-in integration suite; creates and removes its own MySQL database.

RUN_VISION_MYSQL_TESTS=1 enables this suite. Existing application data is never
used. The configured database account needs CREATE/DROP DATABASE permissions.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from PIL import Image
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.canonical_transcript import ImagePart
from app.core.config import BASE_DIR, get_settings
from app.core.exceptions import AppError
from app.models.agent_run import AgentRun
from app.models.attachment import Attachment, AttachmentStatus, MessageAttachment
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole, MessageStatus
from app.models.provider import ProviderInstance
from app.models.user import User
from app.schemas.message import MessageCreateRequest, MessageEditRequest, MessageRegenerateRequest
from app.services import messages
from app.services.agent_runner import InProcessAgentRunner
from app.services.attachment_transactions import ensure_not_in_use
from app.services.attachments import delete_attachment, upload_attachment
from app.services.storage import LocalFileStorage


@unittest.skipUnless(os.getenv("RUN_VISION_MYSQL_TESTS") == "1", "opt-in real MySQL tests")
class VisionMySQLTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.database = "ai_chat_vision_test_" + uuid4().hex[:16]
        cls.settings = replace(get_settings(), db_name=cls.database, memory_enabled=False)
        cls.admin_engine = create_engine(get_settings().sync_database_url)
        with cls.admin_engine.connect() as connection:
            connection.execute(text(f"CREATE DATABASE `{cls.database}` CHARACTER SET utf8mb4"))
            connection.commit()
        try:
            cls.sync_engine = create_engine(cls.settings.sync_database_url)
            config = Config(str(BASE_DIR / "alembic.ini"))
            config.set_main_option("script_location", str(BASE_DIR / "alembic"))
            with patch("app.core.config.get_settings", return_value=cls.settings):
                command.upgrade(config, "0012_projects")
                with cls.sync_engine.connect() as connection:
                    cls.original_types = dict(connection.execute(text("SELECT CONCAT(TABLE_NAME,'.',COLUMN_NAME), COLUMN_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN ('users','messages') AND COLUMN_NAME='id'")).all())
                command.upgrade(config, "head")
                with cls.sync_engine.connect() as connection:
                    columns = dict(connection.execute(text("SELECT CONCAT(TABLE_NAME,'.',COLUMN_NAME), COLUMN_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN ('attachments','message_attachments')")).all())
                    assert columns["attachments.user_id"] == cls.original_types["users.id"]
                    assert columns["message_attachments.message_id"] == cls.original_types["messages.id"]
                    assert columns["attachments.id"] == columns["message_attachments.attachment_id"] == "bigint"
                    assert len(inspect(connection).get_foreign_keys("message_attachments")) == 2
                command.downgrade(config, "0012_projects")
                with cls.sync_engine.connect() as connection:
                    assert "attachments" not in inspect(connection).get_table_names()
                    assert "messages" in inspect(connection).get_table_names()
                command.upgrade(config, "head")
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "sync_engine"):
            cls.sync_engine.dispose()
        with cls.admin_engine.connect() as connection:
            connection.execute(text(f"DROP DATABASE `{cls.database}`"))
            connection.commit()
        cls.admin_engine.dispose()

    async def asyncSetUp(self):
        self.engine = create_async_engine(self.settings.async_database_url)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.runner = InProcessAgentRunner()
        self.directory = tempfile.TemporaryDirectory(prefix="vision-mysql-", dir=BASE_DIR.parent / ".tmp")
        self.storage = LocalFileStorage(self.directory.name, min_free_bytes=0)
        self.storage.initialize()
        self.patches = [
            patch("app.services.messages.AsyncSessionLocal", self.sessions),
            patch("app.services.messages.agent_runner", self.runner),
            patch("app.services.attachments.get_storage", return_value=self.storage),
            patch("app.services.attachment_cleanup.get_storage", return_value=self.storage),
            patch("app.services.messages.search_memory", AsyncMock(return_value=None)),
            patch("app.services.messages.runtime_snapshot", AsyncMock(return_value=[])),
        ]
        for item in self.patches:
            item.start()
        async with self.sessions() as session:
            self.user = User(username="test_" + uuid4().hex, password_hash="test")
            session.add(self.user)
            await session.flush()
            self.provider = ProviderInstance(user_id=self.user.id, preset_id="openai", display_name="test", default_adapter_id="openai_responses", default_model_id="test-vision", enabled=True)
            session.add(self.provider)
            await session.flush()
            self.conversation = Conversation(user_id=self.user.id, title="vision test", provider="test", provider_instance_id=self.provider.id, model="test-vision")
            session.add(self.conversation)
            await session.commit()
        buffer = io.BytesIO()
        Image.new("RGB", (8, 8), "red").save(buffer, "PNG")
        self.png = buffer.getvalue()

    async def asyncTearDown(self):
        await self.runner.shutdown()
        for item in reversed(self.patches):
            item.stop()
        await self.engine.dispose()
        self.directory.cleanup()

    async def upload(self):
        async with self.sessions() as session:
            return await upload_attachment(session, self.user.id, self.png, "photo.png", "image/png")

    async def pair(self, ids, content=""):
        async with self.sessions() as session:
            return await messages.create_message_pair(session, self.user.id, self.conversation.id, MessageCreateRequest(content=content, attachment_ids=ids))

    async def fake_reply(self, *, context, session, **kwargs):
        self.transcript = context["prompt_transcript"]
        await messages._record_text_delta(session=session, context=context, text="vision answer")
        return "vision answer", None

    async def test_upload_image_only_pair_pagination_and_deleted_placeholder(self):
        attachment = await self.upload()
        with patch("app.services.messages._collect_reply_from_stream", self.fake_reply):
            response = await self.pair([attachment.id])
        self.assertEqual(response.user_message.content, "")
        self.assertEqual([row.id for row in response.user_message.attachments], [attachment.id])
        self.assertEqual(len([part for item in self.transcript for part in item.parts if isinstance(part, ImagePart)]), 1)
        async with self.sessions() as session:
            result = await messages.list_conversation_messages(session, self.user.id, self.conversation.id, limit=40)
            self.assertEqual(result.items[0].attachments[0].id, attachment.id)
            tree = await messages.get_conversation_message_tree(session, self.user.id, self.conversation.id)
            self.assertEqual(tree.nodes[0].preview, "图片消息")
            self.assertEqual(tree.nodes[0].attachment_count, 1)
        async with self.sessions() as session:
            deleted = await delete_attachment(session, self.user.id, attachment.id)
            self.assertEqual(deleted.status, AttachmentStatus.DELETED)
        with patch("app.services.messages._collect_reply_from_stream", self.fake_reply):
            await self.pair([], "继续")
        self.assertFalse(any(isinstance(part, ImagePart) for item in self.transcript for part in item.parts))
        self.assertTrue(any("已移除" in getattr(part, "text", "") for item in self.transcript for part in item.parts))

    async def test_two_concurrent_bindings_only_one_commits(self):
        attachment = await self.upload()
        with patch("app.services.messages._collect_reply_from_stream", self.fake_reply):
            results = await asyncio.gather(self.pair([attachment.id]), self.pair([attachment.id]), return_exceptions=True)
        errors = [r for r in results if isinstance(r, Exception)]
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], AppError)
        self.assertEqual(errors[0].code, "ATTACHMENT_ALREADY_BOUND")
        self.assertIn("message_id", errors[0].details)
        async with self.sessions() as session:
            bindings = list((await session.scalars(select(MessageAttachment).where(MessageAttachment.attachment_id == attachment.id))).all())
            self.assertEqual(len(bindings), 1)

    async def test_cancellation_protects_until_execution_and_cleanup_finish(self):
        attachment = await self.upload()
        started, release, cleaning = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def slow_reply(**kwargs):
            started.set()
            try:
                await asyncio.Future()
            finally:
                cleaning.set()
                await release.wait()
        with patch("app.services.messages._collect_reply_from_stream", slow_reply):
            waiter = asyncio.create_task(self.pair([attachment.id]))
            await asyncio.wait_for(started.wait(), 10)
            async with self.sessions() as session:
                run = await session.scalar(select(AgentRun).where(AgentRun.conversation_id == self.conversation.id))
                run_id = run.id
                await messages.cancel_agent_run(session=session, user_id=self.user.id, conversation_id=self.conversation.id, run_id=run_id)
            await asyncio.wait_for(cleaning.wait(), 10)
            try:
                async with self.sessions() as session:
                    with self.assertRaises(AppError) as raised:
                        await delete_attachment(session, self.user.id, attachment.id)
                    self.assertEqual(raised.exception.code, "ATTACHMENT_IN_USE")
            finally:
                release.set()
            with self.assertRaises(AppError) as raised:
                await asyncio.wait_for(waiter, 10)
            self.assertEqual(raised.exception.code, "RUN_CANCELLED")
        async with self.sessions() as session:
            run = await session.get(AgentRun, run_id)
            self.assertEqual(run.status, "cancelled")
            self.assertEqual(json.loads(run.metadata_json)["attachment_ids"], [attachment.id])
            deleted = await delete_attachment(session, self.user.id, attachment.id)
            self.assertEqual(deleted.status, AttachmentStatus.DELETED)

    async def test_current_read_finds_protection_after_old_snapshot(self):
        attachment = await self.upload()
        async with self.sessions() as stale:
            await stale.scalar(select(AgentRun.id).where(AgentRun.conversation_id == self.conversation.id))
            async with self.sessions() as writer:
                run = AgentRun(conversation_id=self.conversation.id, provider="test", model="test", status="running", metadata_json=json.dumps({"attachment_ids": [attachment.id]}))
                writer.add(run)
                await writer.commit()
            with self.assertRaises(AppError) as raised:
                await ensure_not_in_use(stale, [attachment.id])
            self.assertEqual(raised.exception.code, "ATTACHMENT_IN_USE")
        async with self.sessions() as session:
            run = await session.get(AgentRun, run.id)
            run.status = "cancelled"
            await session.commit()

    async def test_regeneration_crop_always_appends_target_once(self):
        attachment = await self.upload()
        with patch("app.services.messages._collect_reply_from_stream", self.fake_reply):
            response = await self.pair([attachment.id])
            for mode in ("root_only", "last_n"):
                async with self.sessions() as session:
                    await messages.regenerate_message(session, self.user.id, self.conversation.id, response.assistant_message.id, MessageRegenerateRequest(context_mode=mode))
                images = [part for item in self.transcript for part in item.parts if isinstance(part, ImagePart)]
                self.assertEqual([part.attachment_id for part in images], [attachment.id])
                self.assertFalse(any(item.kind == "assistant_text" and item.text == "vision answer" for item in self.transcript))

    async def test_edit_keep_then_remove_and_delete_message_cleanup(self):
        attachment = await self.upload()
        with patch("app.services.messages._collect_reply_from_stream", self.fake_reply):
            response = await self.pair([attachment.id])
        async with self.sessions() as session:
            await messages.edit_message(session, self.user.id, self.conversation.id, response.user_message.id, MessageEditRequest(content="", mode="update"))
            self.assertEqual((await session.get(Attachment, attachment.id)).status, AttachmentStatus.READY)
        async with self.sessions() as session:
            await messages.edit_message(session, self.user.id, self.conversation.id, response.user_message.id, MessageEditRequest(content="removed", attachment_ids=[]))
            self.assertEqual((await session.get(Attachment, attachment.id)).status, AttachmentStatus.DELETED)
        other = await self.upload()
        with patch("app.services.messages._collect_reply_from_stream", self.fake_reply):
            response = await self.pair([other.id])
        async with self.sessions() as session:
            await messages.delete_message(session, self.user.id, self.conversation.id, response.user_message.id)
            self.assertEqual((await session.get(Attachment, other.id)).status, AttachmentStatus.DELETED)
            self.assertIsNone(await session.get(Message, response.user_message.id))
