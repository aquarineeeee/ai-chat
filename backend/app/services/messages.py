from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.message import Message, MessageRole, MessageStatus
from app.providers import generate_mock_reply
from app.schemas.message import (
    ConversationMessagesResponse,
    MessageCreateRequest,
    MessageNodeResponse,
    MessageSendResponse,
)
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


async def create_message_pair(
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    payload: MessageCreateRequest,
) -> MessageSendResponse:
    conversation = await get_conversation(session=session, user_id=user_id, conversation_id=conversation_id)
    parent_id = payload.parent_id if payload.parent_id is not None else conversation.current_leaf_message_id
    if parent_id is not None:
        await _ensure_message_belongs_to_conversation(
            session=session,
            conversation_id=conversation.id,
            message_id=parent_id,
        )

    provider = payload.provider or conversation.provider or "mock"
    model = payload.model or conversation.model or "mock-model"
    temperature = payload.temperature if payload.temperature is not None else conversation.temperature
    max_tokens = payload.max_tokens if payload.max_tokens is not None else conversation.max_tokens

    user_message = Message(
        conversation_id=conversation.id,
        parent_id=parent_id,
        role=MessageRole.USER,
        content=payload.content,
        status=MessageStatus.COMPLETED,
    )
    session.add(user_message)
    await session.flush()

    assistant_message = Message(
        conversation_id=conversation.id,
        parent_id=user_message.id,
        role=MessageRole.ASSISTANT,
        content="",
        provider=provider,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        status=MessageStatus.STREAMING,
    )
    session.add(assistant_message)
    await session.flush()

    try:
        reply_content = _generate_reply(
            conversation=conversation,
            content=payload.content,
            provider=provider,
            model=model,
        )
    except AppError as exc:
        assistant_message.status = MessageStatus.FAILED
        assistant_message.error_message = exc.message
        conversation.current_leaf_message_id = assistant_message.id
        await session.commit()
        await session.refresh(user_message)
        await session.refresh(assistant_message)
        raise

    assistant_message.content = reply_content
    assistant_message.status = MessageStatus.COMPLETED
    assistant_message.error_message = None
    conversation.current_leaf_message_id = assistant_message.id
    await session.commit()
    await session.refresh(user_message)
    await session.refresh(assistant_message)
    await session.refresh(conversation)

    return MessageSendResponse(
        conversation_id=conversation.id,
        current_leaf_message_id=conversation.current_leaf_message_id,
        user_message=MessageNodeResponse.model_validate(user_message),
        assistant_message=MessageNodeResponse.model_validate(assistant_message),
    )


async def _ensure_message_belongs_to_conversation(
    *,
    session: AsyncSession,
    conversation_id: int,
    message_id: int,
) -> Message:
    message = await session.scalar(
        select(Message).where(
            Message.id == message_id,
            Message.conversation_id == conversation_id,
        )
    )
    if message is None:
        raise AppError(status_code=400, code="VALIDATION_ERROR", message="parent_id 不属于当前会话")
    return message


def _generate_reply(*, conversation, content: str, provider: str, model: str) -> str:
    if provider != "mock":
        raise AppError(status_code=501, code="MODEL_ERROR", message=f"Provider '{provider}' 尚未实现，请先使用 mock")
    return generate_mock_reply(conversation=conversation, content=content, model=model)
