from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.models.conversation import Conversation
from app.models.project import ConversationMcpTool
from app.schemas.conversation import ConversationCreate, ConversationUpdate
from app.services.branches import create_main_branch_for_conversation
from app.services.markdown_import import import_markdown_conversation
from app.services.providers import get_preferred_provider_instance, get_provider
from app.services.projects import get_project


settings = get_settings()


def _provider_name_for_instance(instance) -> str:
    """Return the configured provider name, keeping transport protocol separate."""
    return instance.display_name


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
    project = await get_project(session, user_id, payload.project_id) if payload.project_id is not None else None
    provider_instance_id = payload.provider_id
    provider_name = payload.provider or settings.default_provider
    provider_model = payload.model or (project.default_model_id if project and project.default_model_id else settings.default_model)
    if provider_instance_id is None:
        instance = await get_preferred_provider_instance(session, user_id, provider_name)
        if instance is not None:
            provider_instance_id = instance.id
            provider_name = _provider_name_for_instance(instance)
            provider_model = payload.model or (project.default_model_id if project and project.default_model_id else instance.default_model_id or provider_model)
    if provider_instance_id is not None:
        instance = await get_provider(session, user_id, provider_instance_id)
        provider_name = _provider_name_for_instance(instance)
        provider_model = payload.model or (project.default_model_id if project and project.default_model_id else instance.default_model_id)
    conversation = Conversation(
        user_id=user_id,
        project_id=project.id if project else None,
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
    if project is not None:
        for item in project.tools:
            tool = item.mcp_tool
            if tool is not None and tool.enabled and tool.remote_available:
                session.add(ConversationMcpTool(conversation_id=conversation.id, mcp_tool_id=tool.id, requires_approval=item.requires_approval))
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
    if "project_id" in update_data:
        target_project_id = update_data.pop("project_id")
        if target_project_id is not None:
            raise AppError(status_code=409, code="PROJECT_SWITCH_FORBIDDEN", message="项目会话不能切换到其他项目")
        if conversation.project_id is not None:
            project = await get_project(session, user_id, conversation.project_id)
            if project.system_prompt and project.system_prompt.strip():
                current = (conversation.system_prompt or "").strip()
                conversation.system_prompt = f"{project.system_prompt.strip()}\n\n{current}" if current else project.system_prompt.strip()
            conversation.project_id = None
    provider_id = update_data.pop("provider_id", None)
    if provider_id is not None:
        instance = await get_provider(session, user_id, provider_id)
        conversation.provider_instance_id = provider_id
        conversation.provider = _provider_name_for_instance(instance)
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
