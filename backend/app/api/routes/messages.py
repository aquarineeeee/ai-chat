from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session, get_current_user
from app.models.user import User
from app.schemas.message import ConversationMessagesResponse, MessageCreateRequest, MessageSendResponse
from app.services.messages import create_message_pair, list_conversation_messages


router = APIRouter()


@router.get("/conversations/{conversation_id}/messages", response_model=ConversationMessagesResponse)
async def messages_index(
    conversation_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> ConversationMessagesResponse:
    return await list_conversation_messages(
        session=session,
        user_id=current_user.id,
        conversation_id=conversation_id,
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
