from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.branch import ConversationBranch
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole
from app.schemas.branch import BranchCreate, BranchUpdate


MAIN_BRANCH_AUTO_TITLE = "主分支"
UNTITLED_BRANCH_AUTO_TITLE = "未命名分支"


async def list_conversation_branches(
    *,
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    include_archived: bool = False,
) -> list[ConversationBranch]:
    await _get_user_conversation(session=session, user_id=user_id, conversation_id=conversation_id)
    stmt = select(ConversationBranch).where(ConversationBranch.conversation_id == conversation_id)
    if not include_archived:
        stmt = stmt.where(ConversationBranch.archived_at.is_(None))
    result = await session.scalars(
        stmt.order_by(
            ConversationBranch.parent_branch_id.asc(),
            ConversationBranch.created_at.asc(),
            ConversationBranch.id.asc(),
        )
    )
    return list(result.all())


async def create_conversation_branch(
    *,
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    payload: BranchCreate,
) -> ConversationBranch:
    conversation = await _get_user_conversation(session=session, user_id=user_id, conversation_id=conversation_id)
    parent_branch = (
        await get_conversation_branch(
            session=session,
            user_id=user_id,
            conversation_id=conversation_id,
            branch_id=payload.parent_branch_id,
        )
        if payload.parent_branch_id is not None
        else await ensure_current_branch(session=session, conversation=conversation)
    )
    if parent_branch.archived_at is not None:
        raise AppError(status_code=400, code="VALIDATION_ERROR", message="不能从已归档分支创建子分支")

    history = await _load_conversation_messages(session=session, conversation_id=conversation_id)
    by_id = {message.id: message for message in history}
    fork_message = by_id.get(payload.forked_from_message_id)
    if fork_message is None:
        raise AppError(status_code=400, code="VALIDATION_ERROR", message="forked_from_message_id 不属于当前会话")
    if parent_branch.current_leaf_message_id is None:
        raise AppError(status_code=400, code="VALIDATION_ERROR", message="父分支还没有可分叉的消息")

    lineage_ids = _lineage_ids(by_id, parent_branch.current_leaf_message_id)
    if payload.forked_from_message_id not in lineage_ids:
        raise AppError(status_code=400, code="VALIDATION_ERROR", message="只能从父分支的消息路径创建子分支")

    title = payload.title.strip() if payload.title else None
    branch = ConversationBranch(
        conversation_id=conversation_id,
        parent_branch_id=parent_branch.id,
        forked_from_message_id=fork_message.id,
        current_leaf_message_id=fork_message.id,
        title=title,
        auto_title=_auto_title_for_branch(messages=history, leaf_message_id=fork_message.id),
    )
    session.add(branch)
    await session.commit()
    await session.refresh(branch)
    return branch


async def update_conversation_branch(
    *,
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    branch_id: int,
    payload: BranchUpdate,
) -> ConversationBranch:
    branch = await get_conversation_branch(
        session=session,
        user_id=user_id,
        conversation_id=conversation_id,
        branch_id=branch_id,
    )
    update_data = payload.model_dump(exclude_unset=True)
    if "title" in update_data:
        branch.title = update_data["title"].strip() if update_data["title"] else None
    await session.commit()
    await session.refresh(branch)
    return branch


async def activate_conversation_branch(
    *,
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    branch_id: int,
) -> ConversationBranch:
    conversation = await _get_user_conversation(session=session, user_id=user_id, conversation_id=conversation_id)
    branch = await get_conversation_branch(
        session=session,
        user_id=user_id,
        conversation_id=conversation_id,
        branch_id=branch_id,
    )
    if branch.archived_at is not None:
        raise AppError(status_code=400, code="VALIDATION_ERROR", message="不能激活已归档分支")
    conversation.current_branch_id = branch.id
    conversation.current_leaf_message_id = branch.current_leaf_message_id
    await session.commit()
    await session.refresh(branch)
    await session.refresh(conversation)
    return branch


async def archive_conversation_branch(
    *,
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    branch_id: int,
) -> ConversationBranch:
    conversation = await _get_user_conversation(session=session, user_id=user_id, conversation_id=conversation_id)
    branch = await get_conversation_branch(
        session=session,
        user_id=user_id,
        conversation_id=conversation_id,
        branch_id=branch_id,
    )
    if conversation.current_branch_id == branch.id:
        raise AppError(status_code=400, code="VALIDATION_ERROR", message="不能归档当前分支")
    if branch.archived_at is None:
        branch.archived_at = datetime.utcnow()
        await session.commit()
        await session.refresh(branch)
    return branch


async def get_conversation_branch(
    *,
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    branch_id: int,
) -> ConversationBranch:
    await _get_user_conversation(session=session, user_id=user_id, conversation_id=conversation_id)
    branch = await session.scalar(
        select(ConversationBranch).where(
            ConversationBranch.id == branch_id,
            ConversationBranch.conversation_id == conversation_id,
        )
    )
    if branch is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="分支不存在")
    return branch


async def ensure_current_branch(
    *,
    session: AsyncSession,
    conversation: Conversation,
) -> ConversationBranch:
    if conversation.current_branch_id is not None:
        branch = await session.scalar(
            select(ConversationBranch).where(
                ConversationBranch.id == conversation.current_branch_id,
                ConversationBranch.conversation_id == conversation.id,
            )
        )
        if branch is not None:
            return branch

    return await create_main_branch_for_conversation(
        session=session,
        conversation=conversation,
        current_leaf_message_id=conversation.current_leaf_message_id,
    )


async def resolve_branch_for_write(
    *,
    session: AsyncSession,
    conversation: Conversation,
    branch_id: int | None,
    activate_branch: bool,
) -> ConversationBranch | None:
    if branch_id is not None:
        branch = await session.scalar(
            select(ConversationBranch).where(
                ConversationBranch.id == branch_id,
                ConversationBranch.conversation_id == conversation.id,
            )
        )
        if branch is None:
            raise AppError(status_code=404, code="NOT_FOUND", message="分支不存在")
        if branch.archived_at is not None:
            raise AppError(status_code=400, code="VALIDATION_ERROR", message="不能写入已归档分支")
        return branch
    if activate_branch:
        return await ensure_current_branch(session=session, conversation=conversation)
    return None


async def create_main_branch_for_conversation(
    *,
    session: AsyncSession,
    conversation: Conversation,
    current_leaf_message_id: int | None = None,
) -> ConversationBranch:
    branch = ConversationBranch(
        conversation_id=conversation.id,
        parent_branch_id=None,
        forked_from_message_id=None,
        current_leaf_message_id=current_leaf_message_id,
        title=None,
        auto_title=MAIN_BRANCH_AUTO_TITLE,
    )
    session.add(branch)
    await session.flush()
    conversation.current_branch_id = branch.id
    conversation.current_leaf_message_id = current_leaf_message_id
    return branch


async def repair_branches_after_message_delete(
    *,
    session: AsyncSession,
    conversation: Conversation,
    deleted_message_ids: set[int],
    target_parent_id: int | None,
    remaining_messages: list[Message],
) -> None:
    result = await session.scalars(
        select(ConversationBranch).where(ConversationBranch.conversation_id == conversation.id)
    )
    branches = list(result.all())
    remaining_message_ids = {message.id for message in remaining_messages}
    replacement_leaf_id = target_parent_id if target_parent_id in remaining_message_ids else _latest_leaf_id(remaining_messages)

    for branch in branches:
        if branch.current_leaf_message_id in deleted_message_ids:
            branch.current_leaf_message_id = replacement_leaf_id
        if branch.forked_from_message_id in deleted_message_ids:
            branch.forked_from_message_id = None

    active_branch = next((branch for branch in branches if branch.id == conversation.current_branch_id), None)
    if active_branch is not None:
        conversation.current_leaf_message_id = active_branch.current_leaf_message_id
    elif conversation.current_leaf_message_id in deleted_message_ids:
        conversation.current_leaf_message_id = replacement_leaf_id


async def _get_user_conversation(
    *,
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
) -> Conversation:
    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
    )
    if conversation is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="会话不存在")
    return conversation


async def _load_conversation_messages(*, session: AsyncSession, conversation_id: int) -> list[Message]:
    result = await session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc(), Message.id.asc())
    )
    return list(result.all())


def _auto_title_for_branch(*, messages: list[Message], leaf_message_id: int) -> str:
    by_id = {message.id: message for message in messages}
    for message_id in reversed(_lineage_ids(by_id, leaf_message_id)):
        message = by_id.get(message_id)
        if message is not None and message.role == MessageRole.USER:
            title = _message_preview(message.content)
            if title:
                return title
    leaf_message = by_id.get(leaf_message_id)
    if leaf_message is not None:
        title = _message_preview(leaf_message.content)
        if title:
            return title
    return UNTITLED_BRANCH_AUTO_TITLE


def _message_preview(content: str) -> str:
    compact = " ".join((content or "").split())
    return compact[:40]


def _lineage_ids(by_id: dict[int, Message], leaf_message_id: int | None) -> list[int]:
    lineage_ids: list[int] = []
    seen: set[int] = set()
    cursor = leaf_message_id
    while cursor is not None and cursor in by_id and cursor not in seen:
        seen.add(cursor)
        message = by_id[cursor]
        lineage_ids.append(message.id)
        cursor = message.parent_id
    lineage_ids.reverse()
    return lineage_ids


def _latest_leaf_id(messages: list[Message]) -> int | None:
    if not messages:
        return None

    by_parent: dict[int | None, list[Message]] = defaultdict(list)
    for message in messages:
        by_parent[message.parent_id].append(message)

    leaves = [message for message in messages if not by_parent.get(message.id)]
    if not leaves:
        return None

    leaves.sort(key=lambda item: (item.updated_at, item.created_at, item.id))
    return leaves[-1].id
