from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.models.conversation import Conversation
from app.schemas.conversation import ConversationCreate, ConversationUpdate
from app.services.branches import create_main_branch_for_conversation
from app.services.markdown_import import import_markdown_conversation
from app.services.providers import get_preferred_provider_instance, get_provider


settings = get_settings()


def _runtime_provider_for_instance(instance) -> str:
    if instance.default_adapter_id == "anthropic_messages":
        return "anthropic"
    if instance.default_adapter_id in {"openai_chat_completions", "openai_responses"}:
        return "openai"
    return instance.preset_id


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
    provider_instance_id = payload.provider_id
    provider_name = payload.provider or settings.default_provider
    provider_model = payload.model or settings.default_model
    if provider_instance_id is None:
        instance = await get_preferred_provider_instance(session, user_id, provider_name)
        if instance is not None:
            provider_instance_id = instance.id
            provider_name = _runtime_provider_for_instance(instance)
            provider_model = payload.model or instance.default_model_id or provider_model
    if provider_instance_id is not None:
        instance = await get_provider(session, user_id, provider_instance_id)
        provider_name = _runtime_provider_for_instance(instance)
        provider_model = payload.model or instance.default_model_id
    conversation = Conversation(
        user_id=user_id,
        title=payload.title,
        system_prompt=payload.system_prompt,
        provider=provider_name,
        provider_instance_id=provider_instance_id,
        model=provider_model,
        temperature=payload.temperature if payload.temperature is not None else settings.default_temperature,
        max_tokens=payload.max_tokens if payload.max_tokens is not None else settings.default_max_tokens,
    )
    session.add(conversation)
    await session.flush()
    await create_main_branch_for_conversation(session=session, conversation=conversation)
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
    provider_id = update_data.pop("provider_id", None)
    if provider_id is not None:
        instance = await get_provider(session, user_id, provider_id)
        conversation.provider_instance_id = provider_id
        conversation.provider = _runtime_provider_for_instance(instance)
        if "model" not in update_data and instance.default_model_id:
            conversation.model = instance.default_model_id

    if provider_id is None and "provider" in update_data:
        instance = await get_preferred_provider_instance(session, user_id, str(update_data["provider"]))
        conversation.provider_instance_id = instance.id if instance is not None else None
        if instance is not None and "model" not in update_data and instance.default_model_id:
            conversation.model = instance.default_model_id

    for field, value in update_data.items():
        setattr(conversation, field, value)

    if "current_leaf_message_id" in update_data and conversation.current_branch_id is not None:
        from app.models.branch import ConversationBranch

        branch = await session.get(ConversationBranch, conversation.current_branch_id)
        if branch is not None and branch.conversation_id == conversation.id:
            branch.current_leaf_message_id = conversation.current_leaf_message_id

    await session.commit()
    await session.refresh(conversation)
    return conversation


async def delete_conversation(session: AsyncSession, user_id: int, conversation_id: int) -> None:
    conversation = await get_conversation(session=session, user_id=user_id, conversation_id=conversation_id)
    await session.delete(conversation)
    await session.commit()
