from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.models.conversation import Conversation
from app.schemas.conversation import ConversationCreate, ConversationUpdate
from app.services.markdown_import import import_markdown_conversation


settings = get_settings()


async def list_conversations(session: AsyncSession, user_id: int) -> list[Conversation]:
    result = await session.scalars(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
    )
    return list(result.all())


async def get_conversation(session: AsyncSession, user_id: int, conversation_id: int) -> Conversation:
    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
    )
    if conversation is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="会话不存在")
    return conversation


async def create_conversation(session: AsyncSession, user_id: int, payload: ConversationCreate) -> Conversation:
    conversation = Conversation(
        user_id=user_id,
        title=payload.title,
        system_prompt=payload.system_prompt,
        provider=payload.provider or settings.default_provider,
        model=payload.model or settings.default_model,
        temperature=payload.temperature if payload.temperature is not None else settings.default_temperature,
        max_tokens=payload.max_tokens if payload.max_tokens is not None else settings.default_max_tokens,
    )
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return conversation


async def update_conversation(
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    payload: ConversationUpdate,
) -> Conversation:
    conversation = await get_conversation(session=session, user_id=user_id, conversation_id=conversation_id)
    update_data = payload.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(conversation, field, value)

    await session.commit()
    await session.refresh(conversation)
    return conversation


async def delete_conversation(session: AsyncSession, user_id: int, conversation_id: int) -> None:
    conversation = await get_conversation(session=session, user_id=user_id, conversation_id=conversation_id)
    await session.delete(conversation)
    await session.commit()
