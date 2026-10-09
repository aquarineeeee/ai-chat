from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException

from app.api.deps import db_session, get_current_user
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.models.attachment import AttachmentStatus
from app.models.user import User
from app.schemas.attachment import AttachmentResponse
from app.services.attachments import attachment_response, delete_attachment, get_owned_attachment, upload_attachment
from app.services.storage import get_storage

router = APIRouter()


@router.post("", response_model=AttachmentResponse, status_code=201)
async def upload(request: Request, user: User = Depends(get_current_user), session: AsyncSession = Depends(db_session)):
    try:
        async with request.form(max_files=1, max_fields=0, max_part_size=get_settings().upload_max_request_bytes) as form:
            file = form.get("file")
            if len(form) != 1 or not isinstance(file, UploadFile):
                raise AppError(400, "INVALID_UPLOAD", "请选择一张图片")
            data = await file.read(get_settings().image_max_bytes + 1)
            row = await upload_attachment(session, user.id, data, file.filename or "", file.content_type)
            return attachment_response(row)
    except MultiPartException as exc:
        raise AppError(400, "INVALID_UPLOAD", "图片上传格式无效") from exc


@router.get("/{attachment_id}/preview")
async def preview(attachment_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(db_session)):
    row = await get_owned_attachment(session, user.id, attachment_id)
    if row.status != AttachmentStatus.READY:
        raise AppError(410, "ATTACHMENT_DELETED", "图片已移除或正在删除")
    handle = get_storage().open_file(row.storage_path)
    def chunks():
        try:
            while chunk := handle.read(64 * 1024):
                yield chunk
        finally:
            handle.close()
    return StreamingResponse(chunks(), media_type=row.media_type, headers={"Content-Disposition": "inline", "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.delete("/{attachment_id}", response_model=AttachmentResponse)
async def remove(attachment_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(db_session)):
    return attachment_response(await delete_attachment(session, user.id, attachment_id))
