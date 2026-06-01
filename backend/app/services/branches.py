from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.branch import ConversationBranch
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole, MessageStatus
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


async def delete_conversation_branch(
    *,
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    branch_id: int,
) -> None:
    conversation = await _get_user_conversation(session=session, user_id=user_id, conversation_id=conversation_id)
    branch = await get_conversation_branch(
        session=session,
        user_id=user_id,
        conversation_id=conversation_id,
        branch_id=branch_id,
    )
    if branch.parent_branch_id is None or branch.forked_from_message_id is None:
        raise AppError(status_code=400, code="VALIDATION_ERROR", message="不能删除主分支")

    branches = await _load_conversation_branches(session=session, conversation_id=conversation_id)
    branch_ids_to_delete = _descendant_branch_ids(branches=branches, root_branch_id=branch.id)
    if conversation.current_branch_id in branch_ids_to_delete:
        replacement_branch = _replacement_branch_after_delete(
            branches=branches,
            deleted_branch_ids=branch_ids_to_delete,
            target_branch_id=branch.id,
        )
        conversation.current_branch_id = replacement_branch.id if replacement_branch is not None else None
        conversation.current_leaf_message_id = (
            replacement_branch.current_leaf_message_id
            if replacement_branch is not None
            else branch.forked_from_message_id
        )

    history = await _load_conversation_messages(session=session, conversation_id=conversation_id)
    by_id = {message.id: message for message in history}
    subtree_root_id = _branch_message_subtree_root_id(branch=branch, by_id=by_id)
    subtree_messages = _subtree_messages(history, subtree_root_id) if subtree_root_id is not None else []
    if any(item.status == MessageStatus.STREAMING for item in subtree_messages):
        raise AppError(status_code=409, code="CONFLICT", message="分支仍有消息在生成中，暂时不能删除")

    deleted_message_ids = {item.id for item in subtree_messages}
    if deleted_message_ids:
        remaining_messages = [item for item in history if item.id not in deleted_message_ids]
        await repair_branches_after_message_delete(
            session=session,
            conversation=conversation,
            deleted_message_ids=deleted_message_ids,
            target_parent_id=branch.forked_from_message_id,
            remaining_messages=remaining_messages,
        )

    branches_by_id = {item.id: item for item in branches}
    for target_branch_id in sorted(branch_ids_to_delete, reverse=True):
        target_branch = branches_by_id.get(target_branch_id)
        if target_branch is not None:
            await session.delete(target_branch)

    for message in reversed(subtree_messages):
        await session.delete(message)

    await session.commit()
    await session.refresh(conversation)


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


async def _load_conversation_branches(*, session: AsyncSession, conversation_id: int) -> list[ConversationBranch]:
    result = await session.scalars(
        select(ConversationBranch)
        .where(ConversationBranch.conversation_id == conversation_id)
        .order_by(ConversationBranch.created_at.asc(), ConversationBranch.id.asc())
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


def _branch_message_subtree_root_id(*, branch: ConversationBranch, by_id: dict[int, Message]) -> int | None:
    if branch.forked_from_message_id is None or branch.current_leaf_message_id is None:
        return None

    lineage_ids = _lineage_ids(by_id, branch.current_leaf_message_id)
    try:
        fork_index = lineage_ids.index(branch.forked_from_message_id)
    except ValueError:
        return None

    child_index = fork_index + 1
    return lineage_ids[child_index] if child_index < len(lineage_ids) else None


def _subtree_messages(messages: list[Message], root_message_id: int) -> list[Message]:
    by_id = {item.id: item for item in messages}
    by_parent: dict[int | None, list[Message]] = defaultdict(list)
    for message in messages:
        by_parent[message.parent_id].append(message)

    subtree: list[Message] = []
    seen: set[int] = set()
    stack = [root_message_id]
    while stack:
        current_id = stack.pop()
        if current_id in seen or current_id not in by_id:
            continue

        seen.add(current_id)
        message = by_id[current_id]
        subtree.append(message)
        stack.extend(child.id for child in reversed(by_parent.get(current_id, [])))

    return subtree


def _descendant_branch_ids(*, branches: list[ConversationBranch], root_branch_id: int) -> set[int]:
    by_parent: dict[int | None, list[ConversationBranch]] = defaultdict(list)
    for branch in branches:
        by_parent[branch.parent_branch_id].append(branch)

    branch_ids: set[int] = set()
    stack = [root_branch_id]
    while stack:
        current_id = stack.pop()
        if current_id in branch_ids:
            continue

        branch_ids.add(current_id)
        stack.extend(child.id for child in by_parent.get(current_id, []))

    return branch_ids


def _replacement_branch_after_delete(
    *,
    branches: list[ConversationBranch],
    deleted_branch_ids: set[int],
    target_branch_id: int,
) -> ConversationBranch | None:
    active_branches = [branch for branch in branches if branch.archived_at is None]
    visible_branches = [branch for branch in active_branches if branch.parent_branch_id is not None]
    visible_index = next((index for index, branch in enumerate(visible_branches) if branch.id == target_branch_id), None)

    if visible_index is not None:
        for branch in visible_branches[visible_index + 1 :]:
            if branch.id not in deleted_branch_ids:
                return branch
        for branch in reversed(visible_branches[:visible_index]):
            if branch.id not in deleted_branch_ids:
                return branch

    return next(
        (
            branch
            for branch in active_branches
            if branch.parent_branch_id is None and branch.id not in deleted_branch_ids
        ),
        None,
    )


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
