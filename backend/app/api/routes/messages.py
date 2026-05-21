from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session, get_current_user
from app.models.user import User
from app.schemas.message import (
    ConversationMessagesResponse,
    MessageCreateRequest,
    MessageRegenerateRequest,
    MessageRegenerateResponse,
    MessageSendResponse,
)
from app.services.messages import (
    activate_message_branch,
    create_message_pair,
    create_message_stream,
    list_conversation_messages,
    regenerate_message,
    regenerate_message_stream,
)


router = APIRouter()


@router.get("/conversations/{conversation_id}/messages", response_model=ConversationMessagesResponse)
async def messages_index(
    conversation_id: int,
    leaf_message_id: int | None = Query(default=None, ge=1),
    root_message_id: int | None = Query(default=None, ge=1),
    expand_leaf_descendants: bool = False,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> ConversationMessagesResponse:
    return await list_conversation_messages(
        session=session,
        user_id=current_user.id,
        conversation_id=conversation_id,
        leaf_message_id=leaf_message_id,
        root_message_id=root_message_id,
        expand_leaf_descendants=expand_leaf_descendants,
    )


@router.post("/conversations/{conversation_id}/messages", response_model=MessageSendResponse)
async def messages_create(
    conversation_id: int,
    payload: MessageCreateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> MessageSendResponse:
    return await create_message_pair(
        session=session,
        user_id=current_user.id,
        conversation_id=conversation_id,
        payload=payload,
    )


@router.post("/conversations/{conversation_id}/messages/stream")
async def messages_create_stream(
    conversation_id: int,
    payload: MessageCreateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> StreamingResponse:
    stream = await create_message_stream(
        session=session,
        user_id=current_user.id,
        conversation_id=conversation_id,
        payload=payload,
    )

    async def event_stream():
        async for chunk in stream:
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/regenerate",
    response_model=MessageRegenerateResponse,
)
async def messages_regenerate(
    conversation_id: int,
    message_id: int,
    payload: MessageRegenerateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> MessageRegenerateResponse:
    return await regenerate_message(
        session=session,
        user_id=current_user.id,
        conversation_id=conversation_id,
        message_id=message_id,
        payload=payload,
    )


@router.post("/conversations/{conversation_id}/messages/{message_id}/regenerate/stream")
async def messages_regenerate_stream(
    conversation_id: int,
    message_id: int,
    payload: MessageRegenerateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> StreamingResponse:
    stream = await regenerate_message_stream(
        session=session,
        user_id=current_user.id,
        conversation_id=conversation_id,
        message_id=message_id,
        payload=payload,
    )

    async def event_stream():
        async for chunk in stream:
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/conversations/{conversation_id}/messages/{message_id}/activate", response_model=ConversationMessagesResponse)
async def messages_activate_branch(
    conversation_id: int,
    message_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> ConversationMessagesResponse:
    return await activate_message_branch(
        session=session,
        user_id=current_user.id,
        conversation_id=conversation_id,
        message_id=message_id,
    )
