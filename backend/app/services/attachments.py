from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.models.attachment import Attachment, AttachmentStatus, MessageAttachment
from app.models.message import Message
from app.models.conversation import Conversation
from app.services.attachment_transactions import ensure_not_in_use
from app.services.image_validation import validate_image
from app.services.storage import get_storage

logger = logging.getLogger(__name__)


async def drain_task(task):
    """Do not let repeated cancellation abandon an executor owning image buffers."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
        except Exception:
            break
    return task.result()


async def file_io(function, *args):
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await drain_task(task)
        except Exception:
            pass
        raise


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def attachment_response(row: Attachment) -> dict:
    return {"id": row.id, "filename": row.original_filename, "media_type": row.media_type,
            "size_bytes": row.size_bytes, "width": row.width, "height": row.height,
            "status": row.status.value if isinstance(row.status, AttachmentStatus) else row.status,
            "preview_url": f"/api/attachments/{row.id}/preview"}


async def load_message_attachments(session: AsyncSession, message_ids: list[int], current_read: bool = False) -> dict[int, list[Attachment]]:
    result = {mid: [] for mid in message_ids}
    if not message_ids:
        return result
    statement = select(MessageAttachment.message_id, Attachment, MessageAttachment.sequence_index).join(Attachment, Attachment.id == MessageAttachment.attachment_id).where(MessageAttachment.message_id.in_(message_ids)).order_by(Attachment.id).execution_options(populate_existing=True)
    if current_read or session.info.get("attachment_current_read", False):
        statement = statement.with_for_update()
    rows = await session.execute(statement)
    # Lock order follows attachment IDs; output follows explicit message sequence.
    sequence_map = {}
    for mid, attachment, sequence in rows.all():
        sequence_map[(mid, attachment.id)] = sequence
        result[mid].append(attachment)
    for mid in result:
        result[mid].sort(key=lambda row: sequence_map[(mid, row.id)])
    return result


async def lock_attachments(session: AsyncSession, user_id: int | None, ids: list[int]) -> list[Attachment]:
    if len(ids) != len(set(ids)) or any(not isinstance(i, int) or i <= 0 for i in ids):
        raise AppError(422, "INVALID_ATTACHMENT_IDS", "图片 ID 重复或无效")
    if not ids:
        return []
    statement = select(Attachment).where(Attachment.id.in_(ids)).order_by(Attachment.id).with_for_update().execution_options(populate_existing=True)
    rows = list((await session.scalars(statement)).all())
    if len(rows) != len(ids) or (user_id is not None and any(row.user_id != user_id for row in rows)):
        raise AppError(404, "ATTACHMENT_NOT_FOUND", "图片不存在或无权使用")
    by_id = {row.id: row for row in rows}
    return [by_id[aid] for aid in ids]


def check_message_image_budget(rows: list[Attachment]) -> None:
    settings = get_settings()
    if sum(r.size_bytes for r in rows) > settings.message_max_image_bytes or sum(r.width * r.height for r in rows) > settings.message_max_image_pixels:
        raise AppError(413, "MESSAGE_IMAGE_LIMIT", "消息图片总大小或总像素超出上限")


async def _check_binding(session: AsyncSession, user_id: int, rows: list[Attachment], allowed_message_id: int | None = None) -> None:
    if not rows:
        return
    bindings = await session.scalars(select(MessageAttachment).where(MessageAttachment.attachment_id.in_([r.id for r in rows])).with_for_update().execution_options(populate_existing=True))
    for binding in bindings.all():
        if binding.message_id != allowed_message_id:
            # The owner has already been checked under the attachment lock. Do not
            # take another conversation lock after acquiring attachment locks.
            statement = select(Message.conversation_id, Conversation.user_id).join(Conversation, Conversation.id == Message.conversation_id).where(Message.id == binding.message_id)
            if session.bind.dialect.name == "mysql":
                # The current binding may have been committed after the caller's
                # repeatable-read snapshot. A fresh connection sees that commit
                # without taking a second conversation lock in reverse order.
                engine = session.bind.engine if isinstance(session.bind, AsyncConnection) else session.bind
                async with AsyncSession(bind=engine) as lookup_session:
                    info = (await lookup_session.execute(statement)).first()
            else:
                info = (await session.execute(statement)).first()
            details = {"message_id": binding.message_id, "conversation_id": info[0]} if info is not None and info[1] == user_id else None
            raise AppError(409, "ATTACHMENT_ALREADY_BOUND", "图片已属于另一条消息，请恢复该消息后重试", details)
    for row in rows:
        if row.status != AttachmentStatus.READY:
            raise AppError(410, "ATTACHMENT_DELETED", "图片已移除，不能再次发送")
    check_message_image_budget(rows)


async def bind_attachments(session: AsyncSession, user_id: int, message: Message, ids: list[int]) -> list[Attachment]:
    rows = await lock_attachments(session, user_id, ids)
    await _check_binding(session, user_id, rows)
    for sequence, row in enumerate(rows):
        session.add(MessageAttachment(message_id=message.id, attachment_id=row.id, sequence_index=sequence))
    await session.flush()
    return rows


def _mark_deleting(row: Attachment, reason: str) -> None:
    if row.status == AttachmentStatus.DELETED:
        return
    row.status = AttachmentStatus.DELETING
    row.delete_reason = reason
    row.delete_error = None
    row.delete_next_attempt_at = None


async def replace_attachments(session: AsyncSession, user_id: int, message: Message, ids: list[int] | None, content: str) -> list[int]:
    existing = list((await session.scalars(select(MessageAttachment).where(MessageAttachment.message_id == message.id).order_by(MessageAttachment.sequence_index).with_for_update().execution_options(populate_existing=True))).all())
    old_ids = [b.attachment_id for b in existing]
    requested = old_ids if ids is None else ids
    if not content.strip() and not requested:
        raise AppError(422, "EMPTY_MESSAGE", "消息需要文字或图片")
    all_rows = await lock_attachments(session, user_id, sorted(set(old_ids + requested)))
    by_id = {r.id: r for r in all_rows}
    if len(requested) != len(set(requested)):
        raise AppError(422, "INVALID_ATTACHMENT_IDS", "图片 ID 重复")
    # Retained tombstones remain available to historical regeneration, but new bindings must be ready.
    new_rows = [by_id[i] for i in requested if i not in old_ids]
    await _check_binding(session, user_id, new_rows, message.id)
    check_message_image_budget([by_id[i] for i in requested])
    removed = sorted(set(old_ids) - set(requested))
    await ensure_not_in_use(session, old_ids)
    for aid in removed:
        _mark_deleting(by_id[aid], "replacement")
    if ids is not None:
        await session.execute(delete(MessageAttachment).where(MessageAttachment.message_id == message.id))
        for sequence, aid in enumerate(requested):
            session.add(MessageAttachment(message_id=message.id, attachment_id=aid, sequence_index=sequence))
    await session.flush()
    return removed


async def mark_message_attachments_deleting(session: AsyncSession, message_ids: list[int], reason: str = "message_delete") -> list[int]:
    if not message_ids:
        return []
    ids = list((await session.scalars(select(MessageAttachment.attachment_id).where(MessageAttachment.message_id.in_(message_ids)).order_by(MessageAttachment.attachment_id).with_for_update().execution_options(populate_existing=True))).all())
    rows = await lock_attachments(session, None, ids)
    await ensure_not_in_use(session, ids)
    for row in rows:
        _mark_deleting(row, reason)
    await session.flush()
    return ids


async def protect_run_attachments(session: AsyncSession, user_id: int, run, ids: list[int]) -> list[Attachment]:
    rows = await lock_attachments(session, user_id, sorted(set(ids)))
    ready = [r for r in rows if r.status == AttachmentStatus.READY]
    metadata = json.loads(run.metadata_json or "{}")
    metadata["attachment_ids"] = [r.id for r in ready]
    run.metadata_json = json.dumps(metadata, ensure_ascii=False)
    await session.flush()
    return ready


async def upload_attachment(session: AsyncSession, user_id: int, data: bytes, filename: str, media_type: str | None) -> Attachment:
    validated = await validate_image(data, filename, media_type)
    # Future virus scanner hook belongs here, before making the object ready.
    storage = get_storage()
    write_task = asyncio.create_task(asyncio.to_thread(storage.put_file, user_id, validated.media_type, data))
    try:
        path = await asyncio.shield(write_task)
    except asyncio.CancelledError:
        try:
            path = await drain_task(write_task)
            cleanup_task = asyncio.create_task(asyncio.to_thread(storage.delete_file, path))
            await drain_task(cleanup_task)
        except Exception:
            logger.error("Cancelled image upload compensation failed")
        raise
    row = Attachment(user_id=user_id, storage_path=path, original_filename=filename.replace("\\", "/").split("/")[-1][:255], media_type=validated.media_type, size_bytes=len(data), width=validated.width, height=validated.height, sha256=validated.sha256, status=AttachmentStatus.READY)
    committed = False
    try:
        session.add(row)
        commit_task = asyncio.create_task(session.commit())
        try:
            await asyncio.shield(commit_task)
        except asyncio.CancelledError:
            await drain_task(commit_task)
            committed = True
            raise
        committed = True
        await session.refresh(row)
        return row
    except BaseException:
        await session.rollback()
        if not committed:
            try:
                await file_io(storage.delete_file, path)
            except Exception:
                logger.error("Compensation for uncommitted image failed")
        raise


async def get_owned_attachment(session: AsyncSession, user_id: int, attachment_id: int) -> Attachment:
    row = await session.scalar(select(Attachment).where(Attachment.id == attachment_id, Attachment.user_id == user_id).execution_options(populate_existing=True))
    if row is None:
        raise AppError(404, "ATTACHMENT_NOT_FOUND", "图片不存在")
    return row


async def process_deletions(session: AsyncSession, ids: list[int]) -> None:
    """Caller must commit mark-deleting first; never hold row locks during file I/O."""
    from app.services.attachment_cleanup import deletion_guard
    for aid in sorted(set(ids)):
        async with deletion_guard(session, aid) as acquired:
            if not acquired:
                continue
            row = await session.scalar(select(Attachment).where(Attachment.id == aid).execution_options(populate_existing=True))
            if row is None or row.status != AttachmentStatus.DELETING:
                continue
            path, attempts = row.storage_path, row.delete_attempts or 0
            await session.commit()
            error = None
            try:
                await file_io(get_storage().delete_file, path)
            except Exception:
                error = "图片文件删除失败，请稍后重试"
                logger.error("Attachment deletion failed id=%s", aid)
            row = await session.scalar(select(Attachment).where(Attachment.id == aid).with_for_update().execution_options(populate_existing=True))
            if row is None or row.status != AttachmentStatus.DELETING:
                await session.rollback()
                continue
            row.delete_attempts = attempts + 1
            row.delete_error = error
            if error:
                row.status = AttachmentStatus.FAILED
                row.delete_next_attempt_at = None if row.delete_reason == "explicit" else utcnow() + timedelta(seconds=min(3600, 30 * 2 ** min(attempts, 7)))
            else:
                row.status = AttachmentStatus.DELETED
                row.deleted_at = utcnow()
                row.delete_next_attempt_at = None
            await session.commit()


async def delete_attachment(session: AsyncSession, user_id: int, attachment_id: int) -> Attachment:
    rows = await lock_attachments(session, user_id, [attachment_id])
    row = rows[0]
    await ensure_not_in_use(session, [attachment_id])
    _mark_deleting(row, "explicit")
    await session.commit()
    await process_deletions(session, [attachment_id])
    return await get_owned_attachment(session, user_id, attachment_id)


class DatabaseAttachmentReader:
    def __init__(self, session: AsyncSession, user_id: int):
        self.session = session
        self.user_id = user_id

    async def _row(self, attachment_id: int) -> Attachment:
        row = (await lock_attachments(self.session, self.user_id, [attachment_id]))[0]
        if row.status != AttachmentStatus.READY:
            raise AppError(410, "ATTACHMENT_DELETED", "图片已移除")
        # Registration of attachment IDs on the persistent run protects subsequent I/O.
        # Never keep the current-read lock while encoding files or awaiting an upstream.
        await self.session.commit()
        return row

    async def metadata(self, attachment_id: int):
        from app.services.vision_budget import AttachmentMetadata
        row = await self._row(attachment_id)
        return AttachmentMetadata(id=row.id, media_type=row.media_type, size_bytes=row.size_bytes, status="ready", width=row.width, height=row.height)

    async def read(self, attachment_id: int) -> bytes:
        row = await self._row(attachment_id)
        path, size, digest, media = row.storage_path, row.size_bytes, row.sha256, row.media_type
        def read_bounded():
            with get_storage().open_file(path) as handle:
                return handle.read(size + 1)
        task = asyncio.create_task(asyncio.to_thread(read_bounded))
        try:
            data = await asyncio.shield(task)
        except asyncio.CancelledError:
            # The executor thread owns raw image buffers; protect them until it exits.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if task.done() and not task.cancelled():
                task.exception()
            raise
        detected = "image/png" if data.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg" if data.startswith(b"\xff\xd8\xff") else None
        if len(data) != size or hashlib.sha256(data).hexdigest() != digest or media != detected:
            raise AppError(422, "ATTACHMENT_INVALID", "图片文件元数据不一致，请移除引用后重试")
        return data


AttachmentReader = DatabaseAttachmentReader
