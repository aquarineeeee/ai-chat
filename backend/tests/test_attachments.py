from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from PIL import Image
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # Register all referenced tables.
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.db.base import Base
from app.models.attachment import Attachment, AttachmentStatus, MessageAttachment
from app.models.agent_run import AgentRun
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole
from app.models.user import User
from app.services.attachments import (
    DatabaseAttachmentReader, bind_attachments, delete_attachment,
    load_message_attachments, mark_message_attachments_deleting,
    process_deletions, protect_run_attachments, replace_attachments, upload_attachment, utcnow,
)
from app.services.attachment_cleanup import cleanup_attachments, scan_untracked_files
from app.services.image_validation import ValidatedImage, validate_image
from app.services.storage import LocalFileStorage


def image_bytes(fmt="PNG", size=(12, 9)):
    stream = io.BytesIO()
    Image.new("RGB", size, "blue").save(stream, format=fmt)
    return stream.getvalue()


class ImageValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_jpeg_png_and_file_header_mismatch(self):
        for fmt, filename, mime in (("PNG", "photo.png", "image/png"), ("JPEG", "photo.jpg", "image/jpeg")):
            row = await validate_image(image_bytes(fmt), filename, mime)
            self.assertEqual((row.media_type, row.width, row.height), (mime, 12, 9))
        with self.assertRaises(AppError) as raised:
            await validate_image(image_bytes(), "photo.jpg", "image/jpeg")
        self.assertEqual(raised.exception.status_code, 415)

    async def test_corrupt_empty_and_mime(self):
        for payload, filename, mime, status in ((b"", "x.png", "image/png", 400), (b"corrupt", "x.png", "image/png", 400), (image_bytes(), "x.png", "image/jpeg", 415), (image_bytes(), "x.svg", "image/svg+xml", 415), (image_bytes()[:-12], "x.png", "image/png", 400)):
            with self.assertRaises(AppError) as raised:
                await validate_image(payload, filename, mime)
            self.assertEqual(raised.exception.status_code, status)

    async def test_byte_pixel_and_memory_limits(self):
        settings = replace(get_settings(), image_max_bytes=len(image_bytes()))
        with patch("app.services.image_validation.get_settings", return_value=settings):
            await validate_image(image_bytes(), "x.png", "image/png")
            with self.assertRaises(AppError) as raised:
                await validate_image(image_bytes() + b"x", "x.png", "image/png")
            self.assertEqual(raised.exception.status_code, 413)
        for override in ({"image_max_pixels": 100}, {"image_decode_max_memory_bytes": 1}):
            with patch("app.services.image_validation.get_settings", return_value=replace(get_settings(), **override)):
                with self.assertRaises(AppError) as raised:
                    await validate_image(image_bytes(), "x.png", "image/png")
                self.assertEqual(raised.exception.status_code, 413)

    async def test_decode_concurrency_and_timeout(self):
        with patch("app.services.image_validation._decoding", get_settings().image_decode_concurrency):
            with self.assertRaises(AppError) as raised:
                await validate_image(image_bytes(), "x.png", "image/png")
            self.assertEqual(raised.exception.status_code, 429)
        with patch("app.services.image_validation.get_settings", return_value=replace(get_settings(), image_decode_timeout_seconds=0.00001)):
            with self.assertRaises(AppError) as raised:
                await validate_image(image_bytes(), "x.png", "image/png")
            self.assertEqual(raised.exception.code, "IMAGE_DECODE_TIMEOUT")
        # A timed-out process does not retain its slot.
        await validate_image(image_bytes(), "x.png", "image/png")


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = LocalFileStorage(self.temp.name, min_free_bytes=0)
        self.storage.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def test_atomic_private_random_path_and_idempotent_delete(self):
        first = self.storage.put_file(1, "image/png", b"a")
        second = self.storage.put_file(1, "image/png", b"a")
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("users/1/"))
        with self.storage.open_file(first) as handle:
            self.assertEqual(handle.read(), b"a")
        self.assertEqual(list((self.storage.root / ".tmp").iterdir()), [])
        self.storage.delete_file(first)
        self.storage.delete_file(first)
        with self.assertRaises(AppError) as raised:
            self.storage.open_file(first)
        self.assertEqual(raised.exception.code, "ATTACHMENT_FILE_MISSING")

    def test_paths_cannot_escape(self):
        for path in ("../x", "/etc/a", "C:/a", "users\\..\\a", "users/1/../../../a", ""):
            with self.assertRaises(AppError):
                self.storage.resolve_path(path)

    def test_disk_full_and_write_error_are_distinct(self):
        with patch("app.services.storage.shutil.disk_usage") as disk:
            disk.return_value.free = 0
            with self.assertRaises(AppError) as raised:
                self.storage.put_file(1, "image/png", b"x")
            self.assertEqual(raised.exception.status_code, 507)
        with patch("app.services.storage.os.replace", side_effect=OSError("D:/secret/private/path")):
            with self.assertRaises(AppError) as raised:
                self.storage.put_file(1, "image/png", b"x")
            self.assertEqual(raised.exception.status_code, 500)
            self.assertNotIn("secret", raised.exception.message)
        self.assertEqual(list((self.storage.root / ".tmp").iterdir()), [])


class AttachmentDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = LocalFileStorage(Path(self.temp.name) / "uploads", min_free_bytes=0)
        self.storage.initialize()
        self.storage_patch = patch("app.services.attachments.get_storage", return_value=self.storage)
        self.cleanup_storage_patch = patch("app.services.attachment_cleanup.get_storage", return_value=self.storage)
        self.storage_patch.start()
        self.cleanup_storage_patch.start()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        @event.listens_for(self.engine.sync_engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.session = self.factory()
        self.session.add_all([User(id=1, username="first", password_hash="hash"), User(id=2, username="second", password_hash="hash")])
        await self.session.flush()
        self.session.add(Conversation(id=1, user_id=1, title="images"))
        await self.session.flush()
        self.message = Message(id=1, conversation_id=1, role=MessageRole.USER, content="")
        self.session.add(self.message)
        await self.session.commit()

    async def asyncTearDown(self):
        await self.session.close()
        await self.engine.dispose()
        self.storage_patch.stop()
        self.cleanup_storage_patch.stop()
        self.temp.cleanup()

    async def attachment(self, user_id=1, age_hours=0):
        data = image_bytes()
        path = self.storage.put_file(user_id, "image/png", data)
        row = Attachment(user_id=user_id, storage_path=path, original_filename="photo.png", media_type="image/png", size_bytes=len(data), width=12, height=9, sha256=hashlib.sha256(data).hexdigest(), status=AttachmentStatus.READY, created_at=utcnow() - timedelta(hours=age_hours))
        self.session.add(row)
        await self.session.commit()
        return row

    async def test_bind_order_and_duplicate_conflict_details(self):
        first, second = await self.attachment(), await self.attachment()
        await bind_attachments(self.session, 1, self.message, [second.id, first.id])
        await self.session.commit()
        metadata = await load_message_attachments(self.session, [1])
        self.assertEqual([r.id for r in metadata[1]], [second.id, first.id])
        with self.assertRaises(AppError) as raised:
            await bind_attachments(self.session, 1, self.message, [first.id])
        self.assertEqual(raised.exception.code, "ATTACHMENT_ALREADY_BOUND")
        self.assertEqual(raised.exception.details, {"message_id": 1, "conversation_id": 1})

    async def test_ownership_and_duplicate_ids(self):
        other = await self.attachment(user_id=2)
        with self.assertRaises(AppError) as raised:
            await bind_attachments(self.session, 1, self.message, [other.id])
        self.assertEqual(raised.exception.status_code, 404)
        self.assertIsNone(raised.exception.details)
        with self.assertRaises(AppError) as raised:
            await bind_attachments(self.session, 1, self.message, [other.id, other.id])
        self.assertEqual(raised.exception.status_code, 422)

    async def test_precommit_rollback_keeps_file_and_binding_available(self):
        row = await self.attachment()
        path = row.storage_path
        await bind_attachments(self.session, 1, self.message, [row.id])
        await self.session.rollback()
        bindings = await self.session.scalars(select(MessageAttachment))
        self.assertEqual(list(bindings), [])
        self.assertTrue(self.storage.resolve_path(path).exists())

    async def test_replacement_preserve_and_remove_after_commit(self):
        first, second = await self.attachment(), await self.attachment()
        await bind_attachments(self.session, 1, self.message, [first.id])
        await self.session.commit()
        self.assertEqual(await replace_attachments(self.session, 1, self.message, None, ""), [])
        removed = await replace_attachments(self.session, 1, self.message, [second.id], "")
        self.assertEqual(removed, [first.id])
        self.assertTrue(self.storage.resolve_path(first.storage_path).exists())
        await self.session.commit()
        await process_deletions(self.session, removed)
        await self.session.refresh(first)
        self.assertEqual(first.status, AttachmentStatus.DELETED)
        self.assertFalse(self.storage.resolve_path(first.storage_path).exists())
        self.assertEqual([r.id for r in (await load_message_attachments(self.session, [1]))[1]], [second.id])
        with self.assertRaises(AppError):
            await replace_attachments(self.session, 1, self.message, [], "  ")

    async def test_explicit_failure_manual_retry_and_tombstone(self):
        row = await self.attachment()
        await bind_attachments(self.session, 1, self.message, [row.id])
        await self.session.commit()
        with patch.object(self.storage, "delete_file", side_effect=OSError("D:/private")):
            failed = await delete_attachment(self.session, 1, row.id)
        self.assertEqual(failed.status, AttachmentStatus.FAILED)
        self.assertIsNone(failed.delete_next_attempt_at)
        self.assertNotIn("private", failed.delete_error)
        await cleanup_attachments(self.session)
        await self.session.refresh(row)
        self.assertEqual(row.status, AttachmentStatus.FAILED)
        deleted = await delete_attachment(self.session, 1, row.id)
        self.assertEqual(deleted.status, AttachmentStatus.DELETED)
        self.assertEqual(len((await load_message_attachments(self.session, [1]))[1]), 1)

    async def test_run_protection_persists_until_terminal(self):
        row = await self.attachment()
        aid = row.id
        run = AgentRun(conversation_id=1, user_message_id=1, provider="openai", model="vision", status="running", metadata_json='{"cancel_requested":true}')
        self.session.add(run)
        await protect_run_attachments(self.session, 1, run, [row.id])
        await self.session.commit()
        self.assertTrue(json.loads(run.metadata_json)["cancel_requested"])
        with self.assertRaises(AppError) as raised:
            await delete_attachment(self.session, 1, row.id)
        self.assertEqual(raised.exception.code, "ATTACHMENT_IN_USE")
        await self.session.rollback()
        run = await self.session.get(AgentRun, 1)
        run.status = "cancelled"
        await self.session.commit()
        deleted = await delete_attachment(self.session, 1, aid)
        self.assertEqual(deleted.status, AttachmentStatus.DELETED)

    async def test_ttl_keeps_bound_history_and_cleans_old_orphans(self):
        bound = await self.attachment(age_hours=25)
        orphan = await self.attachment(age_hours=25)
        recent = await self.attachment()
        await bind_attachments(self.session, 1, self.message, [bound.id])
        await self.session.commit()
        await cleanup_attachments(self.session)
        for row in (bound, orphan, recent):
            await self.session.refresh(row)
        self.assertEqual([bound.status, orphan.status, recent.status], [AttachmentStatus.READY, AttachmentStatus.DELETED, AttachmentStatus.READY])

    async def test_message_delete_rollback_never_removes_file(self):
        row = await self.attachment()
        aid, path = row.id, row.storage_path
        await bind_attachments(self.session, 1, self.message, [aid])
        await self.session.commit()
        self.assertEqual(await mark_message_attachments_deleting(self.session, [1]), [aid])
        await self.session.rollback()
        await self.session.refresh(row)
        self.assertEqual(row.status, AttachmentStatus.READY)
        self.assertTrue(self.storage.resolve_path(path).exists())

    async def test_untracked_files_and_temp_files_not_historical_records(self):
        row = await self.attachment(age_hours=25)
        recorded_path = row.storage_path
        unknown = self.storage.put_file(1, "image/png", b"uncommitted")
        temporary = self.storage.root / ".tmp" / "stale"
        temporary.write_bytes(b"interrupted")
        for path in (self.storage.resolve_path(row.storage_path), self.storage.resolve_path(unknown), temporary):
            os.utime(path, (time.time() - 90000, time.time() - 90000))
        await scan_untracked_files(self.session, time.time() - 86400)
        self.assertTrue(self.storage.resolve_path(recorded_path).exists())
        self.assertFalse(self.storage.resolve_path(unknown).exists())
        self.assertFalse(temporary.exists())

    async def test_reader_missing_and_metadata_mismatch(self):
        row = await self.attachment()
        reader = DatabaseAttachmentReader(self.session, 1)
        self.assertEqual(await reader.read(row.id), image_bytes())
        self.storage.resolve_path(row.storage_path).write_bytes(b"wrong")
        with self.assertRaises(AppError) as raised:
            await reader.read(row.id)
        self.assertEqual(raised.exception.code, "ATTACHMENT_INVALID")
        self.storage.delete_file(row.storage_path)
        with self.assertRaises(AppError) as raised:
            await reader.read(row.id)
        self.assertEqual(raised.exception.code, "ATTACHMENT_FILE_MISSING")

    async def test_reader_cancellation_waits_for_actual_file_read(self):
        row = await self.attachment()
        reader = DatabaseAttachmentReader(self.session, 1)
        began, release = threading.Event(), threading.Event()
        class BlockingFile:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self, _):
                began.set()
                release.wait(timeout=5)
                return image_bytes()
        with patch.object(self.storage, "open_file", return_value=BlockingFile()):
            task = asyncio.create_task(reader.read(row.id))
            await asyncio.to_thread(began.wait, 5)
            task.cancel()
            await asyncio.sleep(0.02)
            self.assertFalse(task.done())
            task.cancel()
            await asyncio.sleep(0.02)
            self.assertFalse(task.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_cancelled_upload_waits_for_write_and_removes_uncommitted_file(self):
        began, release = threading.Event(), threading.Event()
        original_put = self.storage.put_file
        paths = []
        def blocking_put(*args):
            began.set()
            release.wait(timeout=5)
            path = original_put(*args)
            paths.append(path)
            return path
        validation = ValidatedImage("image/png", 12, 9, hashlib.sha256(image_bytes()).hexdigest())
        with patch("app.services.attachments.validate_image", AsyncMock(return_value=validation)), patch.object(self.storage, "put_file", side_effect=blocking_put):
            task = asyncio.create_task(upload_attachment(self.session, 1, image_bytes(), "x.png", "image/png"))
            await asyncio.to_thread(began.wait, 5)
            task.cancel()
            await asyncio.sleep(0.02)
            self.assertFalse(task.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(len(paths), 1)
        self.assertFalse(self.storage.resolve_path(paths[0]).exists())
        self.assertEqual(list((await self.session.scalars(select(Attachment))).all()), [])

    async def test_system_delete_failure_retries_after_backoff(self):
        row = await self.attachment()
        await bind_attachments(self.session, 1, self.message, [row.id])
        await self.session.commit()
        ids = await mark_message_attachments_deleting(self.session, [1])
        await self.session.commit()
        with patch.object(self.storage, "delete_file", side_effect=OSError("private")):
            await process_deletions(self.session, ids)
        await self.session.refresh(row)
        self.assertEqual(row.status, AttachmentStatus.FAILED)
        self.assertIsNotNone(row.delete_next_attempt_at)
        row.delete_next_attempt_at = utcnow() - timedelta(seconds=1)
        await self.session.commit()
        await cleanup_attachments(self.session)
        await self.session.refresh(row)
        self.assertEqual(row.status, AttachmentStatus.DELETED)

    async def test_api_upload_preview_delete_and_access_control(self):
        import httpx
        from fastapi import FastAPI
        from app.api.deps import db_session, get_current_user
        from app.api.routes.attachments import router
        from app.core.exceptions import register_exception_handlers
        app = FastAPI()
        app.include_router(router, prefix="/api/attachments")
        register_exception_handlers(app)
        async def session_dependency():
            async with self.factory() as session:
                yield session
        app.dependency_overrides[db_session] = session_dependency
        app.dependency_overrides[get_current_user] = lambda: SimpleUser(1)
        with patch("app.api.routes.attachments.get_storage", return_value=self.storage):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                result = await client.post("/api/attachments", files={"file": ("../../photo.png", image_bytes(), "image/png")})
                self.assertEqual(result.status_code, 201, result.text)
                attachment = result.json()
                self.assertEqual(attachment["filename"], "photo.png")
                url = attachment["preview_url"]
                preview = await client.get(url)
                self.assertEqual(preview.status_code, 200)
                self.assertEqual(preview.content, image_bytes())
                self.assertEqual(preview.headers["cache-control"], "private, no-store")
                self.assertEqual(preview.headers["content-disposition"], "inline")
                app.dependency_overrides[get_current_user] = lambda: SimpleUser(2)
                self.assertEqual((await client.get(url)).status_code, 404)
                self.assertEqual((await client.delete(f"/api/attachments/{attachment['id']}")).status_code, 404)
                app.dependency_overrides[get_current_user] = lambda: SimpleUser(1)
                deleted = await client.delete(f"/api/attachments/{attachment['id']}")
                self.assertEqual(deleted.json()["status"], "deleted")
                self.assertEqual((await client.get(url)).status_code, 410)
                invalid = await client.post("/api/attachments", files={"file": ("x.png", b"x" * (1048576 + 1), "image/png")})
                self.assertEqual(invalid.status_code, 413)


class SimpleUser:
    def __init__(self, user_id):
        self.id = user_id


if __name__ == "__main__":
    unittest.main()
