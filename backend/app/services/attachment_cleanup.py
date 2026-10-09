from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import timedelta, timezone

from sqlalchemy import exists, or_, select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.models.attachment import Attachment, AttachmentStatus, MessageAttachment
from app.services.attachment_transactions import ensure_not_in_use
from app.services.attachments import _mark_deleting, drain_task, lock_attachments, process_deletions, utcnow
from app.services.storage import get_storage

logger = logging.getLogger(__name__)
_local_cleanup_lock = asyncio.Lock()
_local_delete_locks: dict[int, asyncio.Lock] = {}


@asynccontextmanager
async def _advisory_guard(session, name, local_lock):
    if local_lock.locked():
        yield False
        return
    async with local_lock:
        connection = None
        acquired = True
        try:
            if session.bind.dialect.name == "mysql":
                engine = session.bind.engine if isinstance(session.bind, AsyncConnection) else session.bind
                connection = await engine.connect()
                acquired = bool(await connection.scalar(text("SELECT GET_LOCK(:name, 0)"), {"name": name}))
            yield acquired
        finally:
            if connection is not None:
                async def release_connection():
                    try:
                        if acquired:
                            await connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": name})
                    finally:
                        await connection.close()
                release_task = asyncio.create_task(release_connection())
                try:
                    await asyncio.shield(release_task)
                except asyncio.CancelledError:
                    await drain_task(release_task)
                    raise


@asynccontextmanager
async def cleanup_guard(session):
    async with _advisory_guard(session, "ai_chat_attachment_cleanup", _local_cleanup_lock) as acquired:
        yield acquired


@asynccontextmanager
async def deletion_guard(session, attachment_id):
    local_lock = _local_delete_locks.setdefault(attachment_id, asyncio.Lock())
    try:
        async with _advisory_guard(session, f"ai_chat_attachment_delete_{attachment_id}", local_lock) as acquired:
            yield acquired
    finally:
        if not local_lock.locked():
            _local_delete_locks.pop(attachment_id, None)


async def cleanup_attachments(session) -> None:
    async with cleanup_guard(session) as acquired:
        if acquired:
            await _cleanup_pass(session)


async def _cleanup_pass(session) -> None:
    settings = get_settings()
    now = utcnow()
    cutoff = now - timedelta(hours=settings.unattached_attachment_ttl_hours)
    associated = exists().where(MessageAttachment.attachment_id == Attachment.id)
    # Candidate discovery may use a snapshot; all state decisions happen under current locks.
    ids = list((await session.scalars(select(Attachment.id).where(or_(
        (Attachment.status == AttachmentStatus.READY) & (Attachment.created_at < cutoff) & ~associated,
        Attachment.status == AttachmentStatus.DELETING,
        (Attachment.status == AttachmentStatus.FAILED) & (Attachment.delete_reason != "explicit") & (Attachment.delete_next_attempt_at <= now),
    )).order_by(Attachment.id).limit(200))).all())
    await session.rollback()
    pending = []
    for aid in ids:
        try:
            row = (await lock_attachments(session, None, [aid]))[0]
            if row.status == AttachmentStatus.READY:
                bound = await session.scalar(select(MessageAttachment.attachment_id).where(MessageAttachment.attachment_id == aid).with_for_update().execution_options(populate_existing=True))
                if bound or row.created_at >= cutoff:
                    await session.rollback()
                    continue
                await ensure_not_in_use(session, [aid])
                _mark_deleting(row, "orphan_cleanup")
            elif row.status == AttachmentStatus.FAILED:
                if row.delete_reason == "explicit" or row.delete_next_attempt_at is None or row.delete_next_attempt_at > now:
                    await session.rollback()
                    continue
                await ensure_not_in_use(session, [aid])
                _mark_deleting(row, row.delete_reason or "orphan_cleanup")
            elif row.status != AttachmentStatus.DELETING:
                await session.rollback()
                continue
            else:
                await ensure_not_in_use(session, [aid])
            pending.append(aid)
            await session.commit()
        except AppError:
            await session.rollback()
    await process_deletions(session, pending)
    await scan_untracked_files(session, cutoff.replace(tzinfo=timezone.utc).timestamp(), guarded=True)


async def scan_untracked_files(session, older_than: float, guarded: bool = False) -> None:
    if not guarded:
        async with cleanup_guard(session) as acquired:
            if acquired:
                await scan_untracked_files(session, older_than, guarded=True)
        return
    storage = get_storage()
    # Always leave newly written files alone, including commit-in-progress uploads.
    def candidates():
        found = []
        for subtree in (storage.root / ".tmp", storage.root / "users"):
            if not subtree.exists():
                continue
            for path in subtree.rglob("*"):
                if path.is_file() and not path.is_symlink() and path.stat().st_mtime < older_than:
                    found.append(path.relative_to(storage.root).as_posix())
                    if len(found) >= 200:
                        return found
        return found
    from app.services.attachments import file_io
    for path in await file_io(candidates):
        row_id = await session.scalar(select(Attachment.id).where(Attachment.storage_path == path).with_for_update())
        await session.rollback()
        if row_id is None:
            try:
                await file_io(storage.delete_file, path)
            except AppError:
                logger.error("Untracked image cleanup failed")


class AttachmentCleanup:
    def __init__(self):
        self.task = None

    async def start(self):
        get_storage().initialize()
        if self.task is None:
            self.task = asyncio.create_task(self._loop(), name="attachment-cleanup")

    async def _loop(self):
        from app.db.session import AsyncSessionLocal
        while True:
            try:
                async with AsyncSessionLocal() as session:
                    await cleanup_attachments(session)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("Attachment cleanup pass failed")
            await asyncio.sleep(get_settings().attachment_cleanup_interval_seconds)

    async def shutdown(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None


attachment_cleanup = AttachmentCleanup()
