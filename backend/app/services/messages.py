from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import Message
from app.schemas.message import ConversationMessagesResponse, MessageNodeResponse
from app.services.conversations import get_conversation


async def list_conversation_messages(
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
) -> ConversationMessagesResponse:
    conversation = await get_conversation(session=session, user_id=user_id, conversation_id=conversation_id)
    result = await session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.asc(), Message.id.asc())
    )
    messages = [MessageNodeResponse.model_validate(item) for item in result.all()]
    return ConversationMessagesResponse(
        conversation_id=conversation.id,
        current_leaf_message_id=conversation.current_leaf_message_id,
        items=messages,
    )
