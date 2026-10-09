from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.agent_run import AgentRun
from app.models.conversation import Conversation
from app.models.project import Project

TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled", "interrupted"}


async def current_scalar(session: AsyncSession, statement):
    try:
        return await session.scalar(statement.with_for_update().execution_options(populate_existing=True))
    except OperationalError as exc:
        await session.rollback()
        if getattr(exc.orig, "args", (None,))[0] in {1205, 1213}:
            raise AppError(409, "RESOURCE_BUSY", "操作发生并发冲突，请稍后重试") from exc
        raise


async def lock_owned_project(session: AsyncSession, user_id: int, project_id: int) -> Project:
    project = await current_scalar(session, select(Project).where(Project.id == project_id, Project.user_id == user_id))
    if project is None:
        raise AppError(404, "PROJECT_NOT_FOUND", "项目不存在")
    return project


async def lock_owned_conversation(session: AsyncSession, user_id: int, conversation_id: int) -> Conversation:
    # The hint is followed by locked current reads and ownership revalidation.
    hint = await session.scalar(select(Conversation.project_id).where(Conversation.id == conversation_id, Conversation.user_id == user_id))
    if hint is not None:
        await lock_owned_project(session, user_id, hint)
    conversation = await current_scalar(session, select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user_id))
    if conversation is None:
        raise AppError(404, "CONVERSATION_NOT_FOUND", "对话不存在")
    if conversation.project_id != hint:
        await session.rollback()
        raise AppError(409, "RESOURCE_BUSY", "对话所属项目已改变，请重试")
    return conversation


async def lock_project_conversations(session: AsyncSession, user_id: int, project_id: int) -> list[Conversation]:
    await lock_owned_project(session, user_id, project_id)
    result = await session.scalars(select(Conversation).where(Conversation.project_id == project_id, Conversation.user_id == user_id).order_by(Conversation.id).with_for_update().execution_options(populate_existing=True))
    return list(result.all())


async def ensure_not_in_use(session: AsyncSession, attachment_ids: list[int]) -> None:
    if not attachment_ids:
        return
    # Attachment locks acquired by caller serialize protection registration and deletion.
    # Scan all active runs because context may contain ancestor images across branches.
    runs = await session.scalars(select(AgentRun).where(AgentRun.status.not_in(TERMINAL_RUN_STATUSES)).order_by(AgentRun.id).with_for_update().execution_options(populate_existing=True))
    wanted = set(attachment_ids)
    for run in runs.all():
        try:
            metadata = json.loads(run.metadata_json or "{}")
        except (ValueError, TypeError):
            metadata = {}
        if wanted.intersection(metadata.get("attachment_ids", [])):
            raise AppError(409, "ATTACHMENT_IN_USE", "图片正在被模型使用，请等待运行结束后重试")
