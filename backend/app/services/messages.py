from __future__ import annotations
import asyncio
import logging
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.canonical_transcript import (
    CanonicalTranscriptItem,
    build_message_history_transcript,
    latest_user_text,
    system_text_item,
    transcript_to_simple_messages,
    user_text_item,
)
from app.core.exceptions import AppError
from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.models.agent_run import AgentRun
from app.models.branch import ConversationBranch
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole, MessageStatus
from app.models.run_event import RunEvent
from app.models.tool_call import ToolCall
from app.providers import (
    create_anthropic_reply,
    create_openai_reply,
    create_openai_responses_reply,
    generate_mock_reply,
    stream_anthropic_reply,
    stream_openai_reply,
    stream_openai_responses_reply,
)
from app.schemas.message import (
    ConversationMessagesResponse,
    ConversationMessageTreeResponse,
    MessageEditRequest,
    MessageEditResponse,
    MessageCreateRequest,
    MessageTreeBranchMarkerResponse,
    MessageTreeEdgeResponse,
    MessageTreeNodeResponse,
    MessageNodeResponse,
    MessageRegenerateRequest,
    MessageRegenerateResponse,
    MessageSendResponse,
)
from app.schemas.agent_run import RunApprovalDecisionRequest
from app.services.approval_manager import ApprovalDecision, approval_manager
from app.services.agent_runner import agent_runner
from app.services.agent_artifacts import tool_output_should_externalize, write_tool_output_artifact
from app.services.api_keys import get_preferred_api_key
from app.services.providers import get_generation_connection, get_preferred_provider_instance, get_provider
from app.services.agent_trace import (
    EVENT_SCHEMA_VERSION,
    PARTS_SCHEMA_VERSION,
    TOOL_CALL_PROJECTION_VERSION,
    aggregate_text_from_parts,
    apply_run_event_to_parts,
    build_preview,
    json_dumps,
    json_loads,
    parts_from_message,
    sanitize_tool_input_for_display,
    sanitize_tool_output_for_audit,
    sanitize_tool_output_for_display,
    utcnow_naive,
)
from app.services.branches import (
    _branch_message_subtree_root_id,
    repair_branches_after_message_delete,
    resolve_branch_for_write,
)
from app.services.conversations import get_conversation
from app.services.memory_mcp import search_memory
from app.services.memory_tools import execute_memory_tool_call, memory_tool_definitions
from app.services.mcp_registry import close_runtime_sessions, execute_runtime_tool, runtime_snapshot
from app.services.run_timeline import project_run_view


MESSAGE_TREE_PREVIEW_LENGTH = 100
MESSAGE_TREE_MAX_NODES = 400
OPENAI_ADAPTERS = {"openai_chat_completions", "openai_responses"}
ANTHROPIC_ADAPTERS = {"anthropic_messages"}
MEMORY_TOOL_ADAPTERS = OPENAI_ADAPTERS | ANTHROPIC_ADAPTERS
DEFAULT_ADAPTER_BY_PROVIDER = {
    "openai": "openai_chat_completions",
    "anthropic": "anthropic_messages",
}
MEMORY_TOOL_GUIDANCE = (
    "你可以按需使用长期记忆工具：当回答依赖跨会话背景、用户偏好或历史约束时，调用 memory_search；"
    "memory_search 支持按需传入 query、domain、valence、arousal、max_results、importance_min、max_tokens；"
    "当需要查看当前记忆系统状态、记忆桶列表，或在修改前先浏览全局状态时，可以调用 memory_pulse；"
    "memory_pulse 支持 include_archive。"
    "当 query 为空时，可用于自动浮现相关记忆。"
    "当发现值得长期保留的信息时，可以调用 memory_write。memory_write 的 content 应该是一条提炼后的记忆，"
    "当需要修正、标记或删除已有记忆时，可以调用 memory_update；"
    "memory_update 支持 bucket_id、name、domain、valence、arousal、importance、tags、resolved、pinned、digested、content、delete。"
    "不要原样转录整段对话；tags、importance、pinned、feel、source_bucket、valence、arousal 仅在确有必要时填写。"
    "不是每轮都必须写记忆。"
)


MEMORY_TOOL_GUIDANCE += (
    "If the user provides a long journal entry, meeting notes, or a work log and wants it split into multiple memories, "
    "you may call memory_grow with content only."
)
MEMORY_TOOL_GUIDANCE += (
    "When available, you may call memory_dream to review recently added memories before deciding whether to keep organizing them."
)


RUN_STATUS_RUNNING = "running"
RUN_STATUS_WAITING_APPROVAL = "waiting_approval"
RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_FAILED = "failed"
RUN_STATUS_CANCELLED = "cancelled"

TOOL_STATUS_PENDING = "pending"
TOOL_STATUS_READY = "ready"
TOOL_STATUS_WAITING_APPROVAL = "waiting_approval"
TOOL_STATUS_RUNNING = "running"
TOOL_STATUS_SUCCESS = "success"
TOOL_STATUS_ERROR = "error"
TOOL_STATUS_DENIED = "denied"

APPROVAL_REQUIRED_TOOLS = {item.strip() for item in get_settings().approval_required_tools if item.strip()}

logger = logging.getLogger(__name__)


def _new_event_id(prefix: str = "evt") -> str:
    return f"{prefix}_{uuid4().hex}"


def _trace_state(context: dict[str, object]) -> dict[str, object]:
    state = context.get("trace_state")
    if isinstance(state, dict):
        return state
    state = {"open_tool_calls": []}
    context["trace_state"] = state
    return state


def _stream_chunk_base(event: dict[str, Any]) -> dict[str, object]:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    return {
        "event_id": event.get("event_id"),
        "type": event.get("type"),
        "run_id": event.get("run_id"),
        "sequence": event.get("sequence"),
        "assistant_message_id": event.get("assistant_message_id"),
        "step_id": event.get("step_id"),
        "tool_call_ref": event.get("tool_call_ref"),
        "payload": payload,
    }


async def list_conversation_messages(
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    leaf_message_id: int | None = None,
    root_message_id: int | None = None,
    expand_leaf_descendants: bool = False,
    before_message_id: int | None = None,
    limit: int | None = None,
) -> ConversationMessagesResponse:
    conversation = await get_conversation(session=session, user_id=user_id, conversation_id=conversation_id)
    history: list[Message] | None = None

    if root_message_id is not None:
        history = await _load_conversation_history(session=session, conversation_id=conversation.id)
        await _ensure_message_belongs_to_conversation(
            session=session,
            conversation_id=conversation.id,
            message_id=root_message_id,
        )

    if leaf_message_id is not None:
        if history is None and expand_leaf_descendants:
            history = await _load_conversation_history(session=session, conversation_id=conversation.id)
        await _ensure_message_belongs_to_conversation(
            session=session,
            conversation_id=conversation.id,
            message_id=leaf_message_id,
        )
        selected_leaf_message_id = (
            _resolve_branch_leaf_message_id(history or [], leaf_message_id)
            if expand_leaf_descendants
            else leaf_message_id
        )
    elif root_message_id is not None:
        selected_leaf_message_id = _resolve_branch_leaf_message_id(history, root_message_id)
    else:
        current_branch = await _load_current_branch(session=session, conversation=conversation)
        selected_leaf_message_id = (
            current_branch.current_leaf_message_id
            if current_branch is not None
            else conversation.current_leaf_message_id
        )

    if limit is not None and root_message_id is None:
        return await _build_paginated_messages_response(
            session=session,
            conversation=conversation,
            selected_leaf_message_id=selected_leaf_message_id,
            before_message_id=before_message_id,
            limit=limit,
        )

    if history is None:
        history = await _load_conversation_history(session=session, conversation_id=conversation.id)

    return _build_messages_response(
        conversation=conversation,
        history=history,
        selected_leaf_message_id=selected_leaf_message_id,
        root_message_id=root_message_id,
    )


async def list_agent_runs_for_conversation(
    *,
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    status: str | None = None,
) -> list[AgentRun]:
    conversation = await get_conversation(session=session, user_id=user_id, conversation_id=conversation_id)
    stmt = (
        select(AgentRun)
        .where(AgentRun.conversation_id == conversation.id)
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
    )
    if status is not None:
        stmt = stmt.where(AgentRun.status == status)
    result = await session.scalars(stmt)
    return list(result.all())


async def list_run_events_for_conversation_run(
    *,
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    run_id: int,
    after_sequence: int = 0,
) -> list[RunEvent]:
    conversation = await get_conversation(session=session, user_id=user_id, conversation_id=conversation_id)
    agent_run = await session.scalar(
        select(AgentRun).where(
            AgentRun.id == run_id,
            AgentRun.conversation_id == conversation.id,
        )
    )
    if agent_run is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="run 不存在")

    result = await session.scalars(
        select(RunEvent)
        .where(
            RunEvent.run_id == agent_run.id,
            RunEvent.sequence > after_sequence,
        )
        .order_by(RunEvent.sequence.asc(), RunEvent.id.asc())
    )
    return list(result.all())


async def get_run_view_for_conversation_run(
    *,
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    run_id: int,
) -> dict[str, Any]:
    agent_run = await get_agent_run_for_conversation(
        session=session,
        user_id=user_id,
        conversation_id=conversation_id,
        run_id=run_id,
    )
    events = await list_run_events_for_conversation_run(
        session=session,
        user_id=user_id,
        conversation_id=conversation_id,
        run_id=run_id,
        after_sequence=0,
    )
    tool_call_rows = await session.scalars(
        select(ToolCall)
        .where(ToolCall.run_id == agent_run.id)
        .order_by(ToolCall.sequence_index.asc(), ToolCall.id.asc())
    )
    tool_calls = list(tool_call_rows.all())
    return project_run_view(run=agent_run, events=events, tool_calls=tool_calls)


async def get_agent_run_for_conversation(
    *,
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    run_id: int,
) -> AgentRun:
    conversation = await get_conversation(session=session, user_id=user_id, conversation_id=conversation_id)
    agent_run = await session.scalar(
        select(AgentRun).where(
            AgentRun.id == run_id,
            AgentRun.conversation_id == conversation.id,
        )
    )
    if agent_run is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="run 不存在")
    return agent_run


async def submit_tool_approval_decision(
    *,
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    run_id: int,
    payload: RunApprovalDecisionRequest,
    approved: bool,
) -> AgentRun:
    agent_run = await get_agent_run_for_conversation(
        session=session,
        user_id=user_id,
        conversation_id=conversation_id,
        run_id=run_id,
    )
    metadata = _run_metadata(agent_run)
    pending = metadata.get("pending_approval")
    if not isinstance(pending, dict):
        raise AppError(status_code=409, code="NO_PENDING_APPROVAL", message="当前 run 没有待审批的工具调用")
    if str(pending.get("tool_call_ref") or "") != payload.tool_call_ref:
        raise AppError(status_code=409, code="APPROVAL_TARGET_MISMATCH", message="待审批的 tool_call_ref 不匹配")
    resolved = approval_manager.resolve(
        run_id=agent_run.id,
        tool_call_ref=payload.tool_call_ref,
        decision=ApprovalDecision(approved=approved, reviewer_id=user_id, comment=payload.comment),
    )
    if not resolved:
        raise AppError(status_code=409, code="APPROVAL_NOT_ACTIVE", message="该审批状态已变更，请刷新后重试")
    return agent_run


def _build_messages_response(
    *,
    conversation: Conversation,
    history: list[Message],
    selected_leaf_message_id: int | None,
    root_message_id: int | None = None,
) -> ConversationMessagesResponse:
    sibling_map = _build_sibling_meta_map(history)
    visible_messages = _visible_conversation_messages(
        history,
        selected_leaf_message_id,
        root_message_id=root_message_id,
    )
    return ConversationMessagesResponse(
        conversation_id=conversation.id,
        current_branch_id=conversation.current_branch_id,
        current_leaf_message_id=selected_leaf_message_id,
        items=[_serialize_message_node(item, sibling_map=sibling_map) for item in visible_messages],
    )


async def _build_paginated_messages_response(
    *,
    session: AsyncSession,
    conversation: Conversation,
    selected_leaf_message_id: int | None,
    before_message_id: int | None,
    limit: int,
) -> ConversationMessagesResponse:
    if selected_leaf_message_id is None:
        return ConversationMessagesResponse(
            conversation_id=conversation.id,
            current_branch_id=conversation.current_branch_id,
            current_leaf_message_id=None,
            items=[],
        )

    lineage = (
        select(Message.id.label("id"), Message.parent_id.label("parent_id"))
        .where(
            Message.id == selected_leaf_message_id,
            Message.conversation_id == conversation.id,
        )
        .cte("message_lineage", recursive=True)
    )
    parent = Message.__table__.alias("lineage_parent")
    lineage = lineage.union_all(
        select(parent.c.id, parent.c.parent_id).where(
            parent.c.id == lineage.c.parent_id,
            parent.c.conversation_id == conversation.id,
        )
    )

    stmt = select(Message).join(lineage, Message.id == lineage.c.id)
    if before_message_id is not None:
        stmt = stmt.where(Message.id < before_message_id)
    result = await session.scalars(stmt.order_by(Message.id.desc()).limit(limit + 1))
    newest_first = list(result.all())
    has_more = len(newest_first) > limit
    page = newest_first[:limit]
    page.reverse()

    sibling_map = await _load_sibling_meta_map(
        session=session,
        conversation_id=conversation.id,
        messages=page,
    )
    return ConversationMessagesResponse(
        conversation_id=conversation.id,
        current_branch_id=conversation.current_branch_id,
        current_leaf_message_id=selected_leaf_message_id,
        items=[_serialize_message_node(item, sibling_map=sibling_map) for item in page],
        has_more=has_more,
        next_before_message_id=page[0].id if has_more and page else None,
    )


async def _load_sibling_meta_map(
    *,
    session: AsyncSession,
    conversation_id: int,
    messages: list[Message],
) -> dict[int, tuple[int, int, int | None, int | None]]:
    if not messages:
        return {}

    parent_ids = {message.parent_id for message in messages}
    sibling_stmt = select(Message).where(Message.conversation_id == conversation_id)
    non_null_parent_ids = [parent_id for parent_id in parent_ids if parent_id is not None]
    parent_conditions = []
    if non_null_parent_ids:
        parent_conditions.append(Message.parent_id.in_(non_null_parent_ids))
    if None in parent_ids:
        parent_conditions.append(Message.parent_id.is_(None))
    sibling_stmt = sibling_stmt.where(or_(*parent_conditions))

    result = await session.scalars(sibling_stmt.order_by(Message.created_at.asc(), Message.id.asc()))
    return _build_sibling_meta_map(list(result.all()))


async def get_conversation_message(
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    message_id: int,
) -> MessageNodeResponse:
    await get_conversation(session=session, user_id=user_id, conversation_id=conversation_id)
    message = await _ensure_message_belongs_to_conversation(
        session=session,
        conversation_id=conversation_id,
        message_id=message_id,
    )
    history = await _load_conversation_history(session=session, conversation_id=conversation_id)
    sibling_map = _build_sibling_meta_map(history)
    return _serialize_message_node(message, sibling_map=sibling_map)


async def get_conversation_message_tree(
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
) -> ConversationMessageTreeResponse:
    conversation = await get_conversation(session=session, user_id=user_id, conversation_id=conversation_id)
    history = await _load_conversation_history(session=session, conversation_id=conversation.id)
    branches = await _load_conversation_branches(
        session=session,
        conversation_id=conversation.id,
        include_archived=False,
    )

    by_id = {message.id: message for message in history}
    children_by_parent: dict[int | None, list[Message]] = defaultdict(list)
    for message in history:
        children_by_parent[message.parent_id].append(message)

    sibling_map = _build_sibling_meta_map(history)
    active_path = _lineage_ids(by_id, conversation.current_leaf_message_id) if conversation.current_leaf_message_id else []
    active_path_set = set(active_path)

    anchor_leaf_ids: set[int] = set(active_path)
    for branch in branches:
        if branch.current_leaf_message_id is not None:
            anchor_leaf_ids.add(branch.current_leaf_message_id)
        if branch.forked_from_message_id is not None:
            anchor_leaf_ids.add(branch.forked_from_message_id)

    selected = _select_tree_messages(
        history,
        by_id=by_id,
        max_nodes=MESSAGE_TREE_MAX_NODES,
        anchor_leaf_ids=anchor_leaf_ids,
    )
    selected_ids = {message.id for message in selected}
    truncated = len(selected) < len(history)

    active_edges = {
        (active_path[index - 1], active_path[index])
        for index in range(1, len(active_path))
    }
    branch_markers = _build_message_tree_branch_markers(
        branches=branches,
        current_branch_id=conversation.current_branch_id,
    )

    nodes = [
        _serialize_message_tree_node(
            message,
            sibling_map=sibling_map,
            child_count=len(children_by_parent.get(message.id, [])),
            is_active_path=message.id in active_path_set,
            is_current_leaf=message.id == conversation.current_leaf_message_id,
            branch_markers=branch_markers.get(message.id, []),
        )
        for message in selected
    ]
    edges = [
        MessageTreeEdgeResponse(
            id=f"edge-{message.parent_id}-{message.id}",
            source=message.parent_id,
            target=message.id,
            is_active_path=(message.parent_id, message.id) in active_edges,
        )
        for message in selected
        if message.parent_id is not None and message.parent_id in selected_ids
    ]

    return ConversationMessageTreeResponse(
        conversation_id=conversation.id,
        current_branch_id=conversation.current_branch_id,
        current_leaf_message_id=conversation.current_leaf_message_id,
        active_path=active_path,
        nodes=nodes,
        edges=edges,
        truncated=truncated,
        total_node_count=len(history),
    )


async def activate_message_branch(
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    message_id: int,
    exact: bool = False,
) -> ConversationMessagesResponse:
    conversation = await get_conversation(session=session, user_id=user_id, conversation_id=conversation_id)
    history = await _load_conversation_history(session=session, conversation_id=conversation.id)
    by_id = {item.id: item for item in history}
    target_message = by_id.get(message_id)
    if target_message is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="消息不存在")

    selected_leaf_message_id = target_message.id if exact else _resolve_branch_leaf_message_id(history, target_message.id)

    branches = await _load_conversation_branches(
        session=session,
        conversation_id=conversation.id,
        include_archived=False,
    )
    owning_branch = _resolve_owning_branch(
        branches=branches,
        by_id=by_id,
        leaf_message_id=selected_leaf_message_id,
    )
    if owning_branch is None:
        owning_branch = await resolve_branch_for_write(
            session=session,
            conversation=conversation,
            branch_id=None,
            activate_branch=True,
        )

    if owning_branch is not None:
        owning_branch.current_leaf_message_id = selected_leaf_message_id
        conversation.current_branch_id = owning_branch.id
    conversation.current_leaf_message_id = selected_leaf_message_id

    await session.commit()
    await session.refresh(conversation)
    return _build_messages_response(
        conversation=conversation,
        history=history,
        selected_leaf_message_id=selected_leaf_message_id,
    )


async def delete_message(
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    message_id: int,
) -> None:
    conversation = await get_conversation(session=session, user_id=user_id, conversation_id=conversation_id)
    history = await _load_conversation_history(session=session, conversation_id=conversation.id)
    target_message = next((item for item in history if item.id == message_id), None)
    if target_message is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="消息不存在")
    if target_message.status == MessageStatus.STREAMING:
        raise AppError(status_code=409, code="CONFLICT", message="消息仍在生成中，暂时不能删除")

    subtree_messages = _subtree_messages(history, target_message.id)
    if any(item.status == MessageStatus.STREAMING for item in subtree_messages):
        raise AppError(status_code=409, code="CONFLICT", message="消息仍在生成中，暂时不能删除")

    deleted_message_ids = {item.id for item in subtree_messages}
    remaining_messages = [item for item in history if item.id not in deleted_message_ids]
    await repair_branches_after_message_delete(
        session=session,
        conversation=conversation,
        deleted_message_ids=deleted_message_ids,
        target_parent_id=target_message.parent_id,
        remaining_messages=remaining_messages,
    )

    for message in reversed(subtree_messages):
        await session.delete(message)
    await session.commit()
    await session.refresh(conversation)


async def edit_message(
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    message_id: int,
    payload: MessageEditRequest,
) -> MessageEditResponse:
    conversation = await get_conversation(session=session, user_id=user_id, conversation_id=conversation_id)
    target_message = await _ensure_message_belongs_to_conversation(
        session=session,
        conversation_id=conversation.id,
        message_id=message_id,
    )

    content = payload.content.strip()
    if not content:
        raise AppError(status_code=422, code="VALIDATION_ERROR", message="消息内容不能为空")
    if target_message.role != MessageRole.USER:
        raise AppError(status_code=400, code="VALIDATION_ERROR", message="仅支持编辑用户消息")
    if target_message.status == MessageStatus.STREAMING:
        raise AppError(status_code=409, code="CONFLICT", message="消息仍在生成中，暂时不能编辑")

    if payload.mode == "update":
        target_message.content = content
        target_message.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(target_message)
        await session.refresh(conversation)
        return MessageEditResponse(
            conversation_id=conversation.id,
            message_id=target_message.id,
            current_branch_id=conversation.current_branch_id,
            current_leaf_message_id=conversation.current_leaf_message_id,
        )

    response = await create_message_pair(
        session=session,
        user_id=user_id,
        conversation_id=conversation_id,
        payload=MessageCreateRequest(
            content=content,
            parent_id=target_message.parent_id,
            branch_id=payload.branch_id,
            activate_branch=True,
            context_mode=payload.context_mode,
            context_root_message_id=payload.context_root_message_id,
            context_message_count=payload.context_message_count,
        ),
    )
    return MessageEditResponse(
        conversation_id=response.conversation_id,
        message_id=response.user_message.id,
        current_branch_id=response.current_branch_id,
        current_leaf_message_id=response.current_leaf_message_id,
    )


async def create_message_pair(
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    payload: MessageCreateRequest,
) -> MessageSendResponse:
    context = await _prepare_generation(session=session, user_id=user_id, conversation_id=conversation_id, payload=payload)
    await _initialize_trace_for_context(session=session, context=context, user_message=context["user_message"])

    try:
        reply_content, usage = await _collect_reply_from_stream(
            session=session,
            context=context,
            user_id=user_id,
        )
    except Exception as exc:
        app_error = _to_app_error(exc)
        await _mark_failed(
            session=session,
            context=context,
            conversation=context["conversation"],
            branch=context["branch"],
            assistant_message=context["assistant_message"],
            message=app_error.message,
            status=MessageStatus.FAILED,
            leaf_message_id=context["user_message"].id,
            activate_branch=context["activate_branch"],
        )
        raise app_error

    await _finalize_success(
        session=session,
        context=context,
        conversation=context["conversation"],
        branch=context["branch"],
        assistant_message=context["assistant_message"],
        reply_content=reply_content,
        usage=usage,
        activate_branch=context["activate_branch"],
    )
    return await _build_send_response(
        session=session,
        conversation=context["conversation"],
        user_message=context["user_message"],
        assistant_message=context["assistant_message"],
        selected_leaf_message_id=context["assistant_message"].id,
        history=context["history"],
    )


async def create_message_stream(
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    payload: MessageCreateRequest,
) -> AsyncIterator[dict[str, object]]:
    context = await _prepare_generation(session=session, user_id=user_id, conversation_id=conversation_id, payload=payload)
    await _initialize_trace_for_context(session=session, context=context, user_message=context["user_message"])
    agent_run = context["agent_run"]
    assert isinstance(agent_run, AgentRun)
    agent_runner.start(
        agent_run.id,
        _execute_background_run(
            payload={
                "run_id": agent_run.id,
                "user_id": user_id,
                "conversation_id": conversation_id,
                "assistant_message_id": context["assistant_message"].id,
                "branch_id": context["branch"].id if isinstance(context.get("branch"), ConversationBranch) else None,
                "provider": context["provider"],
                "adapter_id": context["adapter_id"],
                "model": context["model"],
                "temperature": context["temperature"],
                "max_tokens": context["max_tokens"],
                "prompt_transcript": context["prompt_transcript"],
                "mcp_tools": context["mcp_tools"],
                "activate_branch": context["activate_branch"],
                "failure_leaf_message_id": context["user_message"].id,
            }
        ),
    )
    return stream_run_events(
        session=session,
        user_id=user_id,
        conversation_id=conversation_id,
        run_id=agent_run.id,
        after_sequence=0,
    )


async def regenerate_message(
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    message_id: int,
    payload: MessageRegenerateRequest,
) -> MessageRegenerateResponse:
    context = await _prepare_regeneration(
        session=session,
        user_id=user_id,
        conversation_id=conversation_id,
        message_id=message_id,
        payload=payload,
    )
    await _initialize_trace_for_context(session=session, context=context, user_message=None)

    try:
        reply_content, usage = await _collect_reply_from_stream(
            session=session,
            context=context,
            user_id=user_id,
        )
    except Exception as exc:
        app_error = _to_app_error(exc)
        await _mark_failed(
            session=session,
            context=context,
            conversation=context["conversation"],
            branch=context["branch"],
            assistant_message=context["assistant_message"],
            message=app_error.message,
            status=MessageStatus.FAILED,
            leaf_message_id=context["target_message"].id,
            activate_branch=context["activate_branch"],
        )
        raise app_error

    await _finalize_success(
        session=session,
        context=context,
        conversation=context["conversation"],
        branch=context["branch"],
        assistant_message=context["assistant_message"],
        reply_content=reply_content,
        usage=usage,
        activate_branch=context["activate_branch"],
    )
    return await _build_regenerate_response(
        session=session,
        conversation=context["conversation"],
        replaced_message=context["target_message"],
        assistant_message=context["assistant_message"],
        selected_leaf_message_id=context["assistant_message"].id,
        history=context["history"],
    )


async def regenerate_message_stream(
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    message_id: int,
    payload: MessageRegenerateRequest,
) -> AsyncIterator[dict[str, object]]:
    context = await _prepare_regeneration(
        session=session,
        user_id=user_id,
        conversation_id=conversation_id,
        message_id=message_id,
        payload=payload,
    )
    await _initialize_trace_for_context(session=session, context=context, user_message=None)
    agent_run = context["agent_run"]
    assert isinstance(agent_run, AgentRun)
    agent_runner.start(
        agent_run.id,
        _execute_background_run(
            payload={
                "run_id": agent_run.id,
                "user_id": user_id,
                "conversation_id": conversation_id,
                "assistant_message_id": context["assistant_message"].id,
                "branch_id": context["branch"].id if isinstance(context.get("branch"), ConversationBranch) else None,
                "provider": context["provider"],
                "adapter_id": context["adapter_id"],
                "model": context["model"],
                "temperature": context["temperature"],
                "max_tokens": context["max_tokens"],
                "prompt_transcript": context["prompt_transcript"],
                "mcp_tools": context["mcp_tools"],
                "activate_branch": context["activate_branch"],
                "failure_leaf_message_id": context["target_message"].id,
            }
        ),
    )
    return stream_run_events(
        session=session,
        user_id=user_id,
        conversation_id=conversation_id,
        run_id=agent_run.id,
        after_sequence=0,
    )


async def _prepare_generation(
    *,
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    payload: MessageCreateRequest,
) -> dict[str, object]:
    conversation = await get_conversation(session=session, user_id=user_id, conversation_id=conversation_id)
    branch = await resolve_branch_for_write(
        session=session,
        conversation=conversation,
        branch_id=payload.branch_id,
        activate_branch=payload.activate_branch,
    )
    parent_id = (
        payload.parent_id
        if payload.parent_id is not None
        else (
            branch.current_leaf_message_id
            if branch is not None
            else conversation.current_leaf_message_id
        )
    )
    if parent_id is not None:
        await _ensure_message_belongs_to_conversation(
            session=session,
            conversation_id=conversation.id,
            message_id=parent_id,
        )

    context_root_message_id = payload.context_root_message_id
    if payload.context_mode in {"root_only", "last_n"}:
        context_root_message_id = context_root_message_id or parent_id
        if context_root_message_id is not None:
            await _ensure_message_belongs_to_conversation(
                session=session,
                conversation_id=conversation.id,
                message_id=context_root_message_id,
            )

    provider_instance_id = payload.provider_id or conversation.provider_instance_id
    provider_instance = await get_provider(session, user_id, provider_instance_id) if provider_instance_id else None
    if provider_instance is None:
        requested_provider = (payload.provider or conversation.provider or "").strip().lower()
        provider_instance = await get_preferred_provider_instance(session, user_id, requested_provider)
        provider_instance_id = provider_instance.id if provider_instance is not None else None
    provider, model, temperature, max_tokens = _resolve_generation_options(
        conversation=conversation,
        provider=provider_instance.display_name if provider_instance is not None else payload.provider,
        model=payload.model or (provider_instance.default_model_id if provider_instance else None),
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
    )
    if payload.provider_id is not None:
        conversation.provider_instance_id = provider_instance_id
        conversation.provider = provider
        conversation.model = model
    if payload.temperature is not None:
        conversation.temperature = temperature

    user_message = Message(
        conversation_id=conversation.id,
        parent_id=parent_id,
        role=MessageRole.USER,
        content=payload.content,
        status=MessageStatus.COMPLETED,
    )
    session.add(user_message)
    await session.flush()

    assistant_message = await _create_assistant_message(
        session=session,
        conversation_id=conversation.id,
        parent_id=user_message.id,
        provider=provider,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        provider_instance_id=provider_instance_id,
        adapter_id=provider_instance.default_adapter_id if provider_instance else None,
        provider_name_snapshot=provider_instance.display_name if provider_instance else None,
    )
    # Both new messages are flushed above, so this snapshot includes them and can
    # be reused for the response (expire_on_commit=False keeps the objects live).
    history = await _load_conversation_history(session=session, conversation_id=conversation.id)
    prompt_transcript = await _build_prompt_transcript(
        session=session,
        conversation=conversation,
        parent_id=parent_id,
        user_content=payload.content,
        context_mode=payload.context_mode,
        context_root_message_id=context_root_message_id,
        context_message_count=payload.context_message_count,
        history=history,
        include_memory_context=_adapter_id_for_generation(provider=provider, instance=provider_instance) not in MEMORY_TOOL_ADAPTERS,
        include_memory_tool_guidance=_adapter_id_for_generation(provider=provider, instance=provider_instance) in MEMORY_TOOL_ADAPTERS,
    )
    mcp_tools = await runtime_snapshot(session, user_id)
    return {
        "activate_branch": payload.activate_branch,
        "assistant_message": assistant_message,
        "branch": branch,
        "conversation": conversation,
        "history": history,
        "max_tokens": max_tokens,
        "model": model,
        "prompt_transcript": prompt_transcript,
        "provider": provider,
        "provider_instance_id": provider_instance_id,
        "adapter_id": provider_instance.default_adapter_id if provider_instance else None,
        "provider_name_snapshot": provider_instance.display_name if provider_instance else None,
        "temperature": temperature,
        "user_message": user_message,
        "mcp_tools": mcp_tools,
        "mcp_tool_map": {str(item["model_tool_name"]): item for item in mcp_tools},
    }


async def _prepare_regeneration(
    *,
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    message_id: int,
    payload: MessageRegenerateRequest,
) -> dict[str, object]:
    conversation = await get_conversation(session=session, user_id=user_id, conversation_id=conversation_id)
    branch = await resolve_branch_for_write(
        session=session,
        conversation=conversation,
        branch_id=payload.branch_id,
        activate_branch=payload.activate_branch,
    )
    target_message = await _ensure_message_belongs_to_conversation(
        session=session,
        conversation_id=conversation.id,
        message_id=message_id,
    )
    provider_instance_id = payload.provider_id or target_message.provider_instance_id or conversation.provider_instance_id
    provider_instance = await get_provider(session, user_id, provider_instance_id) if provider_instance_id else None
    if provider_instance is None:
        requested_provider = (payload.provider or target_message.provider or conversation.provider or "").strip().lower()
        provider_instance = await get_preferred_provider_instance(session, user_id, requested_provider)
        provider_instance_id = provider_instance.id if provider_instance is not None else None
    if target_message.role == MessageRole.ASSISTANT:
        if target_message.status == MessageStatus.STREAMING:
            raise AppError(status_code=409, code="CONFLICT", message="消息仍在生成中，暂时不能重新生成")
        parent_id = target_message.parent_id
        provider, model, temperature, max_tokens = _resolve_generation_options(
            conversation=conversation,
            provider=provider_instance.display_name if provider_instance is not None else payload.provider or target_message.provider,
            model=payload.model or target_message.model or (provider_instance.default_model_id if provider_instance else None),
            temperature=payload.temperature if payload.temperature is not None else target_message.temperature,
            max_tokens=payload.max_tokens if payload.max_tokens is not None else target_message.max_tokens,
        )
    elif target_message.role == MessageRole.USER:
        parent_id = target_message.id
        provider, model, temperature, max_tokens = _resolve_generation_options(
            conversation=conversation,
            provider=provider_instance.display_name if provider_instance is not None else payload.provider,
            model=payload.model or (provider_instance.default_model_id if provider_instance else None),
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
        )
    else:
        raise AppError(status_code=400, code="VALIDATION_ERROR", message="不支持重新生成此类型消息")

    if payload.temperature is not None:
        conversation.temperature = temperature

    context_root_message_id = payload.context_root_message_id
    if payload.context_mode in {"root_only", "last_n"}:
        context_root_message_id = context_root_message_id or target_message.id
        if context_root_message_id is not None:
            await _ensure_message_belongs_to_conversation(
                session=session,
                conversation_id=conversation.id,
                message_id=context_root_message_id,
            )

    assistant_message = await _create_assistant_message(
        session=session,
        conversation_id=conversation.id,
        parent_id=parent_id,
        provider=provider,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        provider_instance_id=provider_instance_id,
        adapter_id=provider_instance.default_adapter_id if provider_instance else None,
        provider_name_snapshot=provider_instance.display_name if provider_instance else None,
    )
    # Assistant placeholder is flushed above, so this snapshot includes it and can
    # be reused for the response (expire_on_commit=False keeps the objects live).
    history = await _load_conversation_history(session=session, conversation_id=conversation.id)
    prompt_transcript = await _build_prompt_transcript(
        session=session,
        conversation=conversation,
        parent_id=parent_id,
        context_mode=payload.context_mode,
        context_root_message_id=context_root_message_id,
        context_message_count=payload.context_message_count,
        history=history,
        include_memory_context=_adapter_id_for_generation(provider=provider, instance=provider_instance) not in MEMORY_TOOL_ADAPTERS,
        include_memory_tool_guidance=_adapter_id_for_generation(provider=provider, instance=provider_instance) in MEMORY_TOOL_ADAPTERS,
    )
    mcp_tools = await runtime_snapshot(session, user_id)
    return {
        "activate_branch": payload.activate_branch,
        "assistant_message": assistant_message,
        "branch": branch,
        "conversation": conversation,
        "history": history,
        "max_tokens": max_tokens,
        "model": model,
        "prompt_transcript": prompt_transcript,
        "provider": provider,
        "provider_instance_id": provider_instance_id,
        "adapter_id": provider_instance.default_adapter_id if provider_instance else None,
        "provider_name_snapshot": provider_instance.display_name if provider_instance else None,
        "target_message": target_message,
        "temperature": temperature,
        "mcp_tools": mcp_tools,
        "mcp_tool_map": {str(item["model_tool_name"]): item for item in mcp_tools},
    }


async def _initialize_trace_for_context(
    *,
    session: AsyncSession,
    context: dict[str, object],
    user_message: Message | None,
) -> None:
    if isinstance(context.get("agent_run"), AgentRun):
        return

    assistant_message = context["assistant_message"]
    assert isinstance(assistant_message, Message)
    conversation = context["conversation"]
    assert isinstance(conversation, Conversation)

    agent_run = AgentRun(
        conversation_id=conversation.id,
        user_message_id=user_message.id if user_message is not None else None,
        assistant_message_id=assistant_message.id,
        provider=str(context["provider"]),
        provider_instance_id=context.get("provider_instance_id"),
        adapter_id=context.get("adapter_id"),
        provider_name_snapshot=context.get("provider_name_snapshot"),
        model=str(context["model"]),
        status=RUN_STATUS_RUNNING,
        started_at=utcnow_naive(),
        resume_token=_new_event_id("run"),
        metadata_json=json_dumps({"phase": "running"}),
    )
    session.add(agent_run)
    await session.flush()
    context["agent_run"] = agent_run
    _trace_state(context)

    await _record_run_event(
        session=session,
        context=context,
        event_type="run.created",
        payload={
            "status": RUN_STATUS_RUNNING,
            "provider": agent_run.provider,
            "model": agent_run.model,
            "user_message_id": user_message.id if user_message is not None else None,
        },
    )
    await _record_run_event(
        session=session,
        context=context,
        event_type="run.phase.changed",
        payload={"phase": "running"},
    )
    await _record_run_event(
        session=session,
        context=context,
        event_type="assistant_message.created",
        payload={"assistant_message_id": assistant_message.id},
    )


async def _execute_background_run(payload: dict[str, Any]) -> None:
    async with AsyncSessionLocal() as session:
        run = await session.get(AgentRun, int(payload["run_id"]))
        if run is None or run.status in {RUN_STATUS_COMPLETED, RUN_STATUS_FAILED, RUN_STATUS_CANCELLED}:
            return

        conversation = await session.get(Conversation, int(payload["conversation_id"]))
        assistant_message = await session.get(Message, int(payload["assistant_message_id"]))
        if conversation is None or assistant_message is None:
            return

        branch_id = payload.get("branch_id")
        branch = await session.get(ConversationBranch, int(branch_id)) if branch_id is not None else None
        context = {
            "agent_run": run,
            "assistant_message": assistant_message,
            "branch": branch,
            "conversation": conversation,
            "provider": payload["provider"],
            "adapter_id": payload.get("adapter_id") or run.adapter_id,
            "model": payload["model"],
            "temperature": payload.get("temperature"),
            "max_tokens": payload.get("max_tokens"),
            "prompt_transcript": payload.get("prompt_transcript") or [],
            "activate_branch": bool(payload.get("activate_branch", True)),
            "mcp_tools": payload.get("mcp_tools") or [],
            "mcp_tool_map": {str(item["model_tool_name"]): item for item in (payload.get("mcp_tools") or []) if isinstance(item, dict)},
        }

        try:
            reply_content, usage = await _collect_reply_from_stream(
                session=session,
                context=context,
                user_id=int(payload["user_id"]),
            )
        except asyncio.CancelledError:
            status = MessageStatus.PARTIAL if assistant_message.content else MessageStatus.FAILED
            await _mark_cancelled(
                session=session,
                context=context,
                conversation=conversation,
                branch=branch,
                assistant_message=assistant_message,
                message="Run cancelled while execution was still in progress.",
                status=status,
                leaf_message_id=int(payload["failure_leaf_message_id"]),
                partial_content=assistant_message.content or "",
                activate_branch=bool(payload.get("activate_branch", True)),
            )
            return
        except Exception as exc:
            app_error = _to_app_error(exc)
            status = MessageStatus.PARTIAL if assistant_message.content else MessageStatus.FAILED
            await _mark_failed(
                session=session,
                context=context,
                conversation=conversation,
                branch=branch,
                assistant_message=assistant_message,
                message=app_error.message,
                status=status,
                leaf_message_id=int(payload["failure_leaf_message_id"]),
                partial_content=assistant_message.content or "",
                activate_branch=bool(payload.get("activate_branch", True)),
            )
            return

        await _finalize_success(
            session=session,
            context=context,
            conversation=conversation,
            branch=branch,
            assistant_message=assistant_message,
            reply_content=reply_content,
            usage=usage,
            activate_branch=bool(payload.get("activate_branch", True)),
        )


async def stream_run_events(
    *,
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    run_id: int,
    after_sequence: int = 0,
) -> AsyncIterator[dict[str, object]]:
    run = await get_agent_run_for_conversation(
        session=session,
        user_id=user_id,
        conversation_id=conversation_id,
        run_id=run_id,
    )
    initial_assistant_message_id = run.assistant_message_id
    await session.rollback()
    yield {
        "run": {
            "id": run_id,
            "assistant_message_id": initial_assistant_message_id,
            "sequence": after_sequence,
        }
    }

    seen_sequence = after_sequence
    while True:
        session.expire_all()
        events = await list_run_events_for_conversation_run(
            session=session,
            user_id=user_id,
            conversation_id=conversation_id,
            run_id=run_id,
            after_sequence=seen_sequence,
        )
        for event in events:
            serialized = serialize_run_event(event)
            seen_sequence = max(seen_sequence, event.sequence)
            chunk = _stream_chunk_from_run_event(serialized)
            if chunk is not None:
                yield chunk
        await session.rollback()

        session.expire_all()
        run = await get_agent_run_for_conversation(
            session=session,
            user_id=user_id,
            conversation_id=conversation_id,
            run_id=run_id,
        )
        run_status = run.status
        await session.rollback()
        if run_status in {RUN_STATUS_COMPLETED, RUN_STATUS_FAILED, RUN_STATUS_CANCELLED} and not agent_runner.is_running(run_id):
            return

        await asyncio.sleep(0.2)


def _stream_chunk_from_run_event(event: dict[str, Any]) -> dict[str, object] | None:
    event_type = str(event.get("type") or "")
    payload = event.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    base = _stream_chunk_base(event)
    if event_type == "message.text.delta":
        return {**base, "content": str(payload.get("text") or "")}
    if event_type == "tool_call.started":
        return {
            **base,
            "tool": {
                "name": str(payload.get("tool_name") or ""),
                "status": "running",
                "arguments": str(payload.get("input_for_model_json") or payload.get("arguments") or ""),
            },
        }
    if event_type == "tool_call.completed":
        return {
            **base,
            "tool": {
                "name": str(payload.get("tool_name") or ""),
                "status": "completed",
                "content": str(payload.get("output_for_model_json") or payload.get("display_output_preview") or ""),
            },
        }
    if event_type in {"tool_call.failed", "run.failed", "run.cancelled"}:
        return {
            **base,
            "error": str(payload.get("error_message") or ""),
        }
    return base


async def _record_text_delta(
    *,
    session: AsyncSession,
    context: dict[str, object],
    text: str,
) -> RunEvent | None:
    if not text:
        return None
    return await _record_run_event(
        session=session,
        context=context,
        event_type="message.text.delta",
        payload={"text": text},
    )


async def _record_text_retraction(
    *,
    session: AsyncSession,
    context: dict[str, object],
    text: str,
) -> RunEvent | None:
    if not text:
        return None
    return await _record_run_event(
        session=session,
        context=context,
        event_type="message.text.retracted",
        payload={"text": text},
    )


async def _record_thinking_event(
    *,
    session: AsyncSession,
    context: dict[str, object],
    event_type: str,
    thinking_id: str,
    text: str = "",
    redacted: bool = False,
) -> RunEvent:
    return await _record_run_event(
        session=session,
        context=context,
        event_type=event_type,
        payload={
            "thinking_id": thinking_id,
            "text": text,
            "redacted": redacted,
        },
    )


async def _record_commentary(
    *,
    session: AsyncSession,
    context: dict[str, object],
    step_id: str | None,
    text: str,
    source: str = "model",
    style: str = "progress",
) -> list[RunEvent]:
    normalized = text.strip()
    if not step_id or not normalized:
        return []

    commentary_id = _new_event_id("cm")
    events = [
        await _record_run_event(
            session=session,
            context=context,
            event_type="commentary.created",
            step_id=step_id,
            payload={
                "commentary_id": commentary_id,
                "step_id": step_id,
                "source": source,
                "style": style,
                "text": "",
            },
        ),
        await _record_run_event(
            session=session,
            context=context,
            event_type="commentary.delta",
            step_id=step_id,
            payload={
                "commentary_id": commentary_id,
                "step_id": step_id,
                "source": source,
                "style": style,
                "text": normalized,
            },
        ),
        await _record_run_event(
            session=session,
            context=context,
            event_type="commentary.completed",
            step_id=step_id,
            payload={
                "commentary_id": commentary_id,
                "step_id": step_id,
                "source": source,
                "style": style,
            },
        ),
    ]
    return events


async def _flush_pending_commentary_for_step(
    *,
    session: AsyncSession,
    context: dict[str, object],
    step_id: str,
) -> list[RunEvent]:
    pending_commentary = context.get("pending_commentary")
    if not isinstance(pending_commentary, list) or not pending_commentary:
        return []

    text = "".join(str(chunk) for chunk in pending_commentary)
    pending_commentary.clear()
    normalized = text.strip()
    if not normalized:
        return []

    events = await _record_commentary(
        session=session,
        context=context,
        step_id=step_id,
        text=text,
        source="model",
        style="progress",
    )
    events.append(
        await _record_run_event(
            session=session,
            context=context,
            event_type="assistant.model_text",
            step_id=step_id,
            payload={"text": text},
        )
    )
    return events


async def _record_phase_change(
    *,
    session: AsyncSession,
    context: dict[str, object],
    phase: str,
) -> RunEvent:
    return await _record_run_event(
        session=session,
        context=context,
        event_type="run.phase.changed",
        payload={"phase": phase},
    )


async def _record_tool_event(
    *,
    session: AsyncSession,
    context: dict[str, object],
    tool: dict[str, object],
) -> list[RunEvent]:
    status = str(tool.get("status") or "")
    tool_name = str(tool.get("name") or "")
    if not status or not tool_name:
        return []

    state = _trace_state(context)
    open_tool_calls = state.setdefault("open_tool_calls", [])
    assert isinstance(open_tool_calls, list)
    events: list[RunEvent] = []

    if status == "running":
        raw_arguments = str(tool.get("arguments") or "")
        display_input = sanitize_tool_input_for_display(_maybe_parse_json(raw_arguments))
        tool_call_ref = _new_event_id("tc")
        step_id = _new_event_id("step")
        requires_approval = _tool_requires_approval(tool_name, context)
        open_tool_calls.append(
            {
                "tool_call_ref": tool_call_ref,
                "step_id": step_id,
                "tool_name": tool_name,
                "completed": False,
                "awaiting_approval": requires_approval,
                "arguments": raw_arguments,
            }
        )
        created_event = await _record_run_event(
            session=session,
            context=context,
            event_type="tool_call.created",
            step_id=step_id,
            tool_call_ref=tool_call_ref,
            payload={
                "step_id": step_id,
                "tool_name": tool_name,
                "display_input_preview": build_preview(display_input),
                "input_for_model_json": raw_arguments,
            },
        )
        arguments_event = await _record_run_event(
            session=session,
            context=context,
            event_type="tool_call.arguments.completed",
            step_id=step_id,
            tool_call_ref=tool_call_ref,
            payload={
                "step_id": step_id,
                "tool_name": tool_name,
                "display_input_preview": build_preview(display_input),
                "input_for_model_json": raw_arguments,
            },
        )
        events.extend([created_event, arguments_event])
        events.extend(
            await _flush_pending_commentary_for_step(
                session=session,
                context=context,
                step_id=step_id,
            )
        )
        if str(tool.get("commentary") or "").strip():
            events.extend(
                await _record_commentary(
                    session=session,
                    context=context,
                    step_id=step_id,
                    text=str(tool.get("commentary") or ""),
                    source=str(tool.get("commentary_source") or "model"),
                    style=str(tool.get("commentary_style") or "progress"),
                )
            )
        if requires_approval:
            approval_event = await _record_run_event(
                session=session,
                context=context,
                event_type="tool_call.approval.requested",
                step_id=step_id,
                tool_call_ref=tool_call_ref,
                payload={
                    "step_id": step_id,
                    "tool_name": tool_name,
                    "input_for_model_json": raw_arguments,
                    "display_input_preview": build_preview(display_input),
                },
            )
            events.append(approval_event)
            events.extend(
                await _record_commentary(
                    session=session,
                    context=context,
                    step_id=step_id,
                    text="Waiting for approval.",
                    source="system",
                    style="status",
                )
            )
            await _record_phase_change(session=session, context=context, phase="waiting_approval")
            agent_run = context.get("agent_run")
            assert isinstance(agent_run, AgentRun)
            approval_manager.register(run_id=agent_run.id, tool_call_ref=tool_call_ref)
        else:
            started_event = await _record_run_event(
                session=session,
                context=context,
                event_type="tool_call.started",
                step_id=step_id,
                tool_call_ref=tool_call_ref,
                payload={
                    "step_id": step_id,
                    "tool_name": tool_name,
                    "input_for_model_json": raw_arguments,
                    "display_input_preview": build_preview(display_input),
                },
            )
            events.append(started_event)
            await _record_phase_change(session=session, context=context, phase="running_tool")
        return events

    if status != "completed":
        return []

    tool_entry = _pop_open_tool_call_entry(state=state, tool_name=tool_name)
    tool_call_ref = str(tool_entry.get("tool_call_ref") or "") if isinstance(tool_entry, dict) else None
    step_id = str(tool_entry.get("step_id") or "") if isinstance(tool_entry, dict) else None
    raw_output = str(tool.get("content") or "")
    display_output = sanitize_tool_output_for_display(_maybe_parse_json(raw_output))
    audit_output = sanitize_tool_output_for_audit(_maybe_parse_json(raw_output))
    agent_run = context.get("agent_run")
    assert isinstance(agent_run, AgentRun)
    output_blob_ref = (
        write_tool_output_artifact(
            run_id=agent_run.id,
            tool_call_ref=tool_call_ref or _new_event_id("tc"),
            raw_output=raw_output,
        )
        if tool_call_ref and tool_output_should_externalize(raw_output)
        else None
    )
    completed_event = await _record_run_event(
        session=session,
        context=context,
        event_type="tool_call.completed",
        step_id=step_id or None,
        tool_call_ref=tool_call_ref,
        payload={
            "step_id": step_id,
            "tool_name": tool_name,
            "display_output_preview": build_preview(display_output),
            "audit_output_preview": build_preview(audit_output),
            "output_for_model_json": raw_output,
            "output_size_bytes": len(raw_output.encode("utf-8")),
            "output_blob_ref": output_blob_ref,
        },
    )
    events.append(completed_event)
    await _record_phase_change(session=session, context=context, phase="running")
    return events


async def _record_run_event(
    *,
    session: AsyncSession,
    context: dict[str, object],
    event_type: str,
    payload: dict[str, Any],
    step_id: str | None = None,
    tool_call_ref: str | None = None,
) -> RunEvent:
    agent_run = context.get("agent_run")
    assistant_message = context["assistant_message"]
    assert isinstance(agent_run, AgentRun)
    assert isinstance(assistant_message, Message)

    agent_run.last_sequence += 1
    event = RunEvent(
        event_id=_new_event_id(),
        run_id=agent_run.id,
        assistant_message_id=assistant_message.id,
        step_id=step_id,
        tool_call_ref=tool_call_ref,
        sequence=agent_run.last_sequence,
        event_type=event_type,
        payload_json=json_dumps(payload),
        schema_version=EVENT_SCHEMA_VERSION,
    )
    session.add(event)
    await session.flush()
    await _apply_projection_for_event(
        session=session,
        context=context,
        event_type=event_type,
        payload=payload,
        step_id=step_id,
        tool_call_ref=tool_call_ref,
        sequence=event.sequence,
    )
    await session.commit()
    return event


async def _apply_projection_for_event(
    *,
    session: AsyncSession,
    context: dict[str, object],
    event_type: str,
    payload: dict[str, Any],
    step_id: str | None,
    tool_call_ref: str | None,
    sequence: int,
) -> None:
    assistant_message = context["assistant_message"]
    conversation = context["conversation"]
    agent_run = context["agent_run"]
    assert isinstance(assistant_message, Message)
    assert isinstance(conversation, Conversation)
    assert isinstance(agent_run, AgentRun)

    if event_type.startswith("message.") or event_type in {"run.failed", "run.cancelled"}:
        current_parts = parts_from_message(assistant_message.parts_json)
        next_parts = apply_run_event_to_parts(
            current_parts,
            event_type=event_type,
            payload=payload,
            tool_call_ref=tool_call_ref,
        )
        if next_parts != current_parts or assistant_message.parts_json is None:
            assistant_message.parts_json = json_dumps(next_parts)
            assistant_message.parts_schema_version = PARTS_SCHEMA_VERSION
            assistant_message.parts_updated_at = utcnow_naive()
            assistant_message.content = aggregate_text_from_parts(next_parts)

    if event_type == "run.phase.changed":
        metadata = _run_metadata(agent_run)
        metadata["phase"] = str(payload.get("phase") or "")
        _set_run_metadata(agent_run, metadata)
    elif event_type == "tool_call.approval.requested":
        agent_run.status = RUN_STATUS_WAITING_APPROVAL
        metadata = _run_metadata(agent_run)
        metadata["pending_approval"] = {
            "tool_call_ref": tool_call_ref,
            "step_id": step_id,
            "tool_name": str(payload.get("tool_name") or ""),
            "arguments_preview": str(payload.get("display_input_preview") or ""),
            "requested_at": utcnow_naive().isoformat(),
        }
        _set_run_metadata(agent_run, metadata)
    elif event_type == "tool_call.approval.granted":
        agent_run.status = RUN_STATUS_RUNNING
        metadata = _run_metadata(agent_run)
        metadata.pop("pending_approval", None)
        _set_run_metadata(agent_run, metadata)
    elif event_type == "tool_call.approval.denied":
        metadata = _run_metadata(agent_run)
        metadata.pop("pending_approval", None)
        _set_run_metadata(agent_run, metadata)

    if event_type.startswith("tool_call."):
        await _project_tool_call_event(
            session=session,
            run=agent_run,
            conversation=conversation,
            assistant_message=assistant_message,
            event_type=event_type,
            payload=payload,
            step_id=step_id,
            tool_call_ref=tool_call_ref,
            sequence=sequence,
        )


async def _project_tool_call_event(
    *,
    session: AsyncSession,
    run: AgentRun,
    conversation: Conversation,
    assistant_message: Message,
    event_type: str,
    payload: dict[str, Any],
    step_id: str | None,
    tool_call_ref: str | None,
    sequence: int,
) -> None:
    if not tool_call_ref:
        return

    tool_call = await session.scalar(
        select(ToolCall).where(
            ToolCall.run_id == run.id,
            ToolCall.tool_call_id == tool_call_ref,
        )
    )

    if tool_call is None:
        if event_type != "tool_call.created":
            return
        tool_call = ToolCall(
            run_id=run.id,
            conversation_id=conversation.id,
            assistant_message_id=assistant_message.id,
            tool_call_id=tool_call_ref,
            tool_name=str(payload.get("tool_name") or ""),
            sequence_index=sequence,
            status=TOOL_STATUS_PENDING,
            projection_version=TOOL_CALL_PROJECTION_VERSION,
        )
        session.add(tool_call)
        await session.flush()

    if event_type == "tool_call.created":
        tool_call.tool_name = str(payload.get("tool_name") or tool_call.tool_name)
        tool_call.status = TOOL_STATUS_PENDING
        return

    if event_type == "tool_call.arguments.completed":
        tool_call.status = TOOL_STATUS_READY
        tool_call.input_for_model_json = str(payload.get("input_for_model_json") or "")
        tool_call.display_input_preview = str(payload.get("display_input_preview") or "")
        return

    if event_type == "tool_call.approval.requested":
        tool_call.status = TOOL_STATUS_WAITING_APPROVAL
        return

    if event_type == "tool_call.approval.granted":
        tool_call.status = TOOL_STATUS_READY
        return

    if event_type == "tool_call.approval.denied":
        tool_call.status = TOOL_STATUS_DENIED
        tool_call.error_message = str(payload.get("comment") or payload.get("error_message") or "")
        tool_call.completed_at = utcnow_naive()
        return

    if event_type == "tool_call.started":
        tool_call.status = TOOL_STATUS_RUNNING
        tool_call.started_at = tool_call.started_at or utcnow_naive()
        return

    if event_type == "tool_call.completed":
        tool_call.status = TOOL_STATUS_SUCCESS
        tool_call.completed_at = utcnow_naive()
        tool_call.output_for_model_json = str(payload.get("output_for_model_json") or "")
        tool_call.display_output_preview = str(payload.get("display_output_preview") or "")
        tool_call.audit_output_preview = str(payload.get("audit_output_preview") or "")
        tool_call.output_blob_ref = str(payload.get("output_blob_ref") or "") or None
        tool_call.output_size_bytes = payload.get("output_size_bytes")
        if tool_call.started_at is not None and tool_call.completed_at is not None:
            tool_call.duration_ms = int((tool_call.completed_at - tool_call.started_at).total_seconds() * 1000)
        return

    if event_type == "tool_call.failed":
        tool_call.status = TOOL_STATUS_ERROR
        tool_call.completed_at = utcnow_naive()
        tool_call.error_message = str(payload.get("error_message") or "")
        if tool_call.started_at is not None and tool_call.completed_at is not None:
            tool_call.duration_ms = int((tool_call.completed_at - tool_call.started_at).total_seconds() * 1000)


def _maybe_parse_json(value: str) -> Any:
    if not value:
        return value
    try:
        return json_loads(value, default=value)
    except TypeError:
        return value


def _run_metadata(run: AgentRun) -> dict[str, Any]:
    metadata = json_loads(run.metadata_json, default={})
    if isinstance(metadata, dict):
        return metadata
    return {}


def _set_run_metadata(run: AgentRun, metadata: dict[str, Any]) -> None:
    run.metadata_json = json_dumps(metadata)


def _tool_requires_approval(tool_name: str, context: dict[str, object] | None = None) -> bool:
    if context is not None:
        mapping = context.get("mcp_tool_map")
        if isinstance(mapping, dict) and isinstance(mapping.get(tool_name), dict):
            return bool(mapping[tool_name].get("requires_approval", True))
    return tool_name in APPROVAL_REQUIRED_TOOLS


def _find_open_tool_call_entry(*, state: dict[str, object], tool_name: str) -> dict[str, object] | None:
    open_tool_calls = state.get("open_tool_calls")
    if not isinstance(open_tool_calls, list):
        return None
    for item in reversed(open_tool_calls):
        if not isinstance(item, dict):
            continue
        if item.get("tool_name") != tool_name or item.get("completed") is True:
            continue
        return item
    return None


def _pop_open_tool_call_entry(*, state: dict[str, object], tool_name: str) -> dict[str, object] | None:
    open_tool_calls = state.get("open_tool_calls")
    if not isinstance(open_tool_calls, list):
        return None

    for item in reversed(open_tool_calls):
        if not isinstance(item, dict):
            continue
        if item.get("tool_name") != tool_name or item.get("completed") is True:
            continue
        item["completed"] = True
        return item
    return None


def _find_pending_tool_call(context: dict[str, object]) -> dict[str, object] | None:
    state = _trace_state(context)
    open_tool_calls = state.get("open_tool_calls")
    if not isinstance(open_tool_calls, list):
        return None

    for item in reversed(open_tool_calls):
        if isinstance(item, dict) and item.get("completed") is not True:
            item["completed"] = True
            return item
    return None


async def _build_prompt_transcript(
    *,
    session: AsyncSession,
    conversation: Conversation,
    parent_id: int | None,
    user_content: str | None = None,
    context_mode: str = "full",
    context_root_message_id: int | None = None,
    context_message_count: int | None = None,
    history: list[Message] | None = None,
    include_memory_context: bool = True,
    include_memory_tool_guidance: bool = False,
) -> list[CanonicalTranscriptItem]:
    transcript: list[CanonicalTranscriptItem] = []
    if conversation.system_prompt:
        transcript.append(system_text_item(conversation.system_prompt))
    if include_memory_tool_guidance:
        transcript.append(system_text_item(MEMORY_TOOL_GUIDANCE))

    if include_memory_context:
        memory_query = user_content.strip() if user_content else None
        if memory_query is None and history is not None:
            memory_query = _latest_user_content(history=history, parent_id=parent_id)
        if memory_query:
            memory_context = await search_memory(query=memory_query)
            if memory_context:
                transcript.append(system_text_item(memory_context))

    if history is None:
        history = await _load_conversation_history(session=session, conversation_id=conversation.id)
    by_id = {item.id: item for item in history}
    context_messages: list[Message] = []

    if context_mode == "root_only":
        if context_root_message_id is not None and context_root_message_id in by_id:
            root_message = by_id[context_root_message_id]
            if root_message.status in {MessageStatus.COMPLETED, MessageStatus.PARTIAL}:
                context_messages.append(root_message)
    elif context_mode == "last_n":
        root_id = context_root_message_id or parent_id
        if root_id is not None:
            lineage_messages = [
                item
                for item in _lineage_messages(by_id, root_id)
                if item.status in {MessageStatus.COMPLETED, MessageStatus.PARTIAL}
            ]
            # last_n means: include the root message itself plus the N messages
            # immediately before it on the ancestor chain.
            message_count = max(context_message_count or 1, 1) + 1
            context_messages.extend(lineage_messages[-message_count:])
    elif parent_id is not None:
        lineage_messages = [
            item
            for item in _lineage_messages(by_id, parent_id)
            if item.status in {MessageStatus.COMPLETED, MessageStatus.PARTIAL}
        ]
        context_messages.extend(lineage_messages)

    transcript.extend(await build_message_history_transcript(session=session, messages=context_messages))
    if user_content is not None:
        transcript.append(user_text_item(user_content))
    return transcript


async def _build_prompt_messages(
    *,
    session: AsyncSession,
    conversation: Conversation,
    parent_id: int | None,
    user_content: str | None = None,
    context_mode: str = "full",
    context_root_message_id: int | None = None,
    context_message_count: int | None = None,
    history: list[Message] | None = None,
    include_memory_context: bool = True,
    include_memory_tool_guidance: bool = False,
) -> list[dict[str, str]]:
    transcript = await _build_prompt_transcript(
        session=session,
        conversation=conversation,
        parent_id=parent_id,
        user_content=user_content,
        context_mode=context_mode,
        context_root_message_id=context_root_message_id,
        context_message_count=context_message_count,
        history=history,
        include_memory_context=include_memory_context,
        include_memory_tool_guidance=include_memory_tool_guidance,
    )
    return transcript_to_simple_messages(transcript)


def _build_context_tool_executor(
    *,
    session: AsyncSession,
    context: dict[str, object] | None,
):
    if context is None:
        return execute_memory_tool_call

    async def execute(tool_name: str, tool_arguments: str) -> str:
        state = _trace_state(context)
        entry = _find_open_tool_call_entry(state=state, tool_name=tool_name)
        if entry is not None and entry.get("awaiting_approval") is True:
            tool_call_ref = str(entry.get("tool_call_ref") or "")
            if tool_call_ref:
                agent_run = context.get("agent_run")
                assert isinstance(agent_run, AgentRun)
                decision = await approval_manager.wait_for_decision(run_id=agent_run.id, tool_call_ref=tool_call_ref)
                if decision.approved:
                    await _record_run_event(
                        session=session,
                        context=context,
                        event_type="tool_call.approval.granted",
                        step_id=str(entry.get("step_id") or "") or None,
                        tool_call_ref=tool_call_ref,
                        payload={
                            "step_id": str(entry.get("step_id") or "") or None,
                            "tool_name": tool_name,
                            "reviewer_id": decision.reviewer_id,
                            "comment": decision.comment,
                        },
                    )
                    await _record_commentary(
                        session=session,
                        context=context,
                        step_id=str(entry.get("step_id") or "") or None,
                        text="Approval granted.",
                        source="system",
                        style="status",
                    )
                    await _record_run_event(
                        session=session,
                        context=context,
                        event_type="tool_call.started",
                        step_id=str(entry.get("step_id") or "") or None,
                        tool_call_ref=tool_call_ref,
                        payload={
                            "step_id": str(entry.get("step_id") or "") or None,
                            "tool_name": tool_name,
                            "input_for_model_json": tool_arguments,
                            "display_input_preview": build_preview(
                                sanitize_tool_input_for_display(_maybe_parse_json(tool_arguments))
                            ),
                        },
                    )
                    await _record_phase_change(session=session, context=context, phase="running_tool")
                else:
                    await _record_run_event(
                        session=session,
                        context=context,
                        event_type="tool_call.approval.denied",
                        step_id=str(entry.get("step_id") or "") or None,
                        tool_call_ref=tool_call_ref,
                        payload={
                            "step_id": str(entry.get("step_id") or "") or None,
                            "tool_name": tool_name,
                            "reviewer_id": decision.reviewer_id,
                            "comment": decision.comment,
                            "error_message": "Tool approval denied",
                        },
                    )
                    raise AppError(status_code=409, code="TOOL_APPROVAL_DENIED", message="Tool approval denied")
        mcp_map = context.get("mcp_tool_map") if isinstance(context, dict) else None
        if isinstance(mcp_map, dict) and tool_name in mcp_map:
            try:
                parsed = json_loads(tool_arguments, default={})
                return await execute_runtime_tool(context, tool_name, parsed if isinstance(parsed, dict) else {})
            except Exception as exc:
                return f"工具执行失败：{str(exc)[:500]}"
        return await execute_memory_tool_call(tool_name, tool_arguments)

    return execute


async def _collect_reply_from_stream(
    *,
    session: AsyncSession,
    context: dict[str, object],
    user_id: int,
) -> tuple[str, dict[str, int] | None]:
    accumulated = ""
    round_buffer = ""
    usage: dict[str, int] | None = None

    async def capture_usage(value: dict[str, int] | None) -> None:
        nonlocal usage
        usage = value

    async for chunk in _stream_reply(
        session=session,
        context=context,
        user_id=user_id,
        conversation=context["conversation"],
        provider=context["provider"],
        model=context["model"],
        temperature=context["temperature"],
        max_tokens=context["max_tokens"],
        prompt_transcript=context["prompt_transcript"],
        usage_callback=capture_usage,
    ):
        chunk_type = str(chunk.get("type") or "")
        if chunk_type == "content":
            content = str(chunk.get("content") or "")
            if content:
                round_buffer += content
                await _record_text_delta(session=session, context=context, text=content)
        elif chunk_type == "content_retracted":
            # This round ended in a tool_use: the streamed text was only a
            # tool-round preamble, not part of the final answer. Drop it from
            # parts_json (the retained commentary/model_text paths keep it
            # visible in the RunView and in the model transcript).
            retracted_text = str(chunk.get("content") or "")
            if retracted_text:
                await _record_text_retraction(session=session, context=context, text=retracted_text)
            round_buffer = ""
        elif chunk_type == "round_commentary":
            text = str(chunk.get("text") or "")
            if text:
                pending_commentary = context.setdefault("pending_commentary", [])
                assert isinstance(pending_commentary, list)
                pending_commentary.append(text)
        elif chunk_type == "thinking_started":
            thinking_id = str(chunk.get("thinking_id") or "")
            if thinking_id:
                await _record_thinking_event(
                    session=session,
                    context=context,
                    event_type="thinking.created",
                    thinking_id=thinking_id,
                    text=str(chunk.get("text") or ""),
                )
        elif chunk_type == "thinking_delta":
            thinking_id = str(chunk.get("thinking_id") or "")
            text = str(chunk.get("text") or "")
            if thinking_id and text:
                await _record_thinking_event(
                    session=session,
                    context=context,
                    event_type="thinking.delta",
                    thinking_id=thinking_id,
                    text=text,
                )
        elif chunk_type == "thinking_completed":
            thinking_id = str(chunk.get("thinking_id") or "")
            if thinking_id:
                await _record_thinking_event(
                    session=session,
                    context=context,
                    event_type="thinking.completed",
                    thinking_id=thinking_id,
                )
        elif chunk_type == "thinking_redacted":
            thinking_id = str(chunk.get("thinking_id") or "")
            if thinking_id:
                await _record_thinking_event(
                    session=session,
                    context=context,
                    event_type="thinking.redacted",
                    thinking_id=thinking_id,
                    redacted=True,
                )
        elif chunk_type == "commentary":
            step_id = str(chunk.get("step_id") or "") or None
            if step_id:
                await _record_commentary(
                    session=session,
                    context=context,
                    step_id=step_id,
                    text=str(chunk.get("content") or ""),
                    source=str(chunk.get("source") or "model"),
                    style=str(chunk.get("style") or "progress"),
                )
        elif chunk_type == "tool":
            tool = chunk.get("tool")
            if isinstance(tool, dict):
                await _record_tool_event(session=session, context=context, tool=tool)

    accumulated += round_buffer
    return accumulated, usage


def _runtime_tools_for_context(context: dict[str, object] | None) -> list[dict[str, object]]:
    if context is not None:
        items = context.get("mcp_tools")
        if isinstance(items, list) and items:
            return [item["definition"] for item in items if isinstance(item, dict) and isinstance(item.get("definition"), dict)]
    return memory_tool_definitions(include_grow=True, include_pulse=True, include_dream=True)


async def _generate_reply(
    *,
    session: AsyncSession,
    context: dict[str, object] | None = None,
    user_id: int,
    conversation: Conversation,
    provider: str,
    model: str,
    temperature,
    max_tokens,
    prompt_transcript: list[CanonicalTranscriptItem],
) -> dict[str, object]:
    tool_executor = _build_context_tool_executor(session=session, context=context)
    if provider == "mock":
        return {
            "content": generate_mock_reply(
                conversation=conversation,
                content=_prompt_seed_content(prompt_transcript),
                model=model,
            ),
            "usage": None,
        }
    adapter_id = _adapter_id_for_generation(provider=provider, context=context)
    if adapter_id in OPENAI_ADAPTERS:
        api_key = (
            (await get_generation_connection(session, user_id, conversation.provider_instance_id))[1]
            if conversation.provider_instance_id is not None
            else await get_preferred_api_key(session=session, user_id=user_id, provider="openai")
        )
        create_reply = create_openai_responses_reply if adapter_id == "openai_responses" else create_openai_reply
        reply = await create_reply(
            api_key=api_key,
            model=model,
            transcript=prompt_transcript,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=_runtime_tools_for_context(context),
            tool_executor=tool_executor,
        )
        return {
            "content": str(reply),
            "usage": getattr(reply, "usage", None),
        }
    if adapter_id in ANTHROPIC_ADAPTERS:
        api_key = (
            (await get_generation_connection(session, user_id, conversation.provider_instance_id))[1]
            if conversation.provider_instance_id is not None
            else await get_preferred_api_key(session=session, user_id=user_id, provider="anthropic")
        )
        return {
            "content": await create_anthropic_reply(
                api_key=api_key,
                model=model,
                transcript=prompt_transcript,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=_runtime_tools_for_context(context),
                tool_executor=tool_executor,
            ),
            "usage": None,
        }
    raise AppError(status_code=422, code="VALIDATION_ERROR", message=f"暂不支持 adapter '{adapter_id}'")


async def _stream_reply(
    *,
    session: AsyncSession,
    context: dict[str, object] | None = None,
    user_id: int,
    conversation: Conversation,
    provider: str,
    model: str,
    temperature,
    max_tokens,
    prompt_transcript: list[CanonicalTranscriptItem],
    usage_callback=None,
) -> AsyncIterator[dict[str, object]]:
    tool_executor = _build_context_tool_executor(session=session, context=context)
    if provider == "mock":
        yield {
            "type": "content",
            "content": generate_mock_reply(
                conversation=conversation,
                content=_prompt_seed_content(prompt_transcript),
                model=model,
            ),
        }
        return
    adapter_id = _adapter_id_for_generation(provider=provider, context=context)
    if adapter_id in OPENAI_ADAPTERS:
        api_key = (
            (await get_generation_connection(session, user_id, conversation.provider_instance_id))[1]
            if conversation.provider_instance_id is not None
            else await get_preferred_api_key(session=session, user_id=user_id, provider="openai")
        )
        async def emit_tool_event(tool: dict[str, object]) -> None:
            if context is not None:
                await _record_tool_event(session=session, context=context, tool=tool)

        stream_reply = stream_openai_responses_reply if adapter_id == "openai_responses" else stream_openai_reply
        async for chunk in stream_reply(
            api_key=api_key,
            model=model,
            transcript=prompt_transcript,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=_runtime_tools_for_context(context),
            tool_executor=tool_executor,
            tool_event_callback=emit_tool_event,
            usage_callback=usage_callback,
        ):
            yield chunk
        return
    if adapter_id in ANTHROPIC_ADAPTERS:
        api_key = (
            (await get_generation_connection(session, user_id, conversation.provider_instance_id))[1]
            if conversation.provider_instance_id is not None
            else await get_preferred_api_key(session=session, user_id=user_id, provider="anthropic")
        )
        async def emit_tool_event(tool: dict[str, object]) -> None:
            if context is not None:
                await _record_tool_event(session=session, context=context, tool=tool)

        async for chunk in stream_anthropic_reply(
            api_key=api_key,
            model=model,
            transcript=prompt_transcript,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=_runtime_tools_for_context(context),
            tool_executor=tool_executor,
            tool_event_callback=emit_tool_event,
            usage_callback=usage_callback,
        ):
            yield chunk
        return
    raise AppError(status_code=422, code="VALIDATION_ERROR", message=f"暂不支持 adapter '{adapter_id}'")


async def _finalize_success(
    *,
    session: AsyncSession,
    context: dict[str, object],
    conversation: Conversation,
    branch: ConversationBranch | None,
    assistant_message: Message,
    reply_content: str,
    usage: dict[str, int] | None,
    activate_branch: bool,
) -> None:
    current_parts = parts_from_message(assistant_message.parts_json)
    completed_parts = _merge_reply_content_into_parts(parts=current_parts, reply_content=reply_content)
    if completed_parts != current_parts or assistant_message.parts_json is None:
        assistant_message.parts_json = json_dumps(completed_parts)
        assistant_message.parts_schema_version = PARTS_SCHEMA_VERSION
        assistant_message.parts_updated_at = utcnow_naive()
    await _record_phase_change(session=session, context=context, phase="finalizing")
    await _record_run_event(
        session=session,
        context=context,
        event_type="message.completed",
        payload={
            "status": MessageStatus.COMPLETED.value,
            "content": reply_content,
            "parts": completed_parts,
            "parts_schema_version": assistant_message.parts_schema_version,
        },
    )
    assistant_message.content = reply_content
    assistant_message.status = MessageStatus.COMPLETED
    assistant_message.error_message = None
    assistant_message.prompt_tokens = usage.get("prompt_tokens") if usage is not None else None
    assistant_message.completion_tokens = usage.get("completion_tokens") if usage is not None else None
    assistant_message.total_tokens = usage.get("total_tokens") if usage is not None else None
    agent_run = context.get("agent_run")
    if isinstance(agent_run, AgentRun):
        agent_run.status = RUN_STATUS_COMPLETED
        agent_run.completed_at = utcnow_naive()
        agent_run.error_message = None
    if branch is not None:
        branch.current_leaf_message_id = assistant_message.id
    if activate_branch:
        if branch is not None:
            conversation.current_branch_id = branch.id
        conversation.current_leaf_message_id = assistant_message.id
    await session.commit()
    await session.refresh(assistant_message)
    await session.refresh(conversation)
    await close_runtime_sessions(context)


def _merge_reply_content_into_parts(
    *,
    parts: list[dict[str, Any]],
    reply_content: str,
) -> list[dict[str, Any]]:
    if not reply_content:
        return [dict(part) for part in parts]

    current_text = aggregate_text_from_parts(parts)
    if current_text == reply_content:
        return [dict(part) for part in parts]

    next_parts = [dict(part) for part in parts]
    if reply_content.startswith(current_text):
        suffix = reply_content[len(current_text):]
        if suffix:
            if next_parts and next_parts[-1].get("type") == "text":
                next_parts[-1]["text"] = str(next_parts[-1].get("text") or "") + suffix
            else:
                next_parts.append({"type": "text", "text": suffix})
        return next_parts

    # Reaching here means accumulated reply_content diverged from parts' own text
    # instead of being a pure suffix extension. With the retraction fix in place
    # this should only happen for legacy/partial messages; log it so regressions
    # in the Anthropic tool-round text handling are easy to spot.
    logger.warning(
        "reply_content did not extend existing parts text; falling back to collapsing parts (current_text=%r, reply_content=%r)",
        current_text,
        reply_content,
    )
    non_text_parts = [dict(part) for part in next_parts if part.get("type") != "text"]
    non_text_parts.append({"type": "text", "text": reply_content})
    return non_text_parts


async def _mark_failed(
    *,
    session: AsyncSession,
    context: dict[str, object] | None,
    conversation: Conversation,
    branch: ConversationBranch | None,
    assistant_message: Message,
    message: str,
    status: MessageStatus,
    leaf_message_id: int,
    activate_branch: bool,
    partial_content: str = "",
) -> None:
    if context is not None:
        pending_tool = _find_pending_tool_call(context)
        if pending_tool is not None:
            await _record_run_event(
                session=session,
                context=context,
                event_type="tool_call.failed",
                step_id=str(pending_tool.get("step_id") or "") or None,
                tool_call_ref=str(pending_tool.get("tool_call_ref") or ""),
                payload={
                    "step_id": str(pending_tool.get("step_id") or "") or None,
                    "tool_name": str(pending_tool.get("tool_name") or ""),
                    "error_message": message,
                },
            )
        await _record_run_event(
            session=session,
            context=context,
            event_type="run.failed",
            payload={"error_message": message, "status": status.value},
        )
    assistant_message.content = partial_content
    assistant_message.status = status
    assistant_message.error_message = message
    agent_run = context.get("agent_run") if context is not None else None
    if isinstance(agent_run, AgentRun):
        agent_run.status = RUN_STATUS_FAILED
        agent_run.completed_at = utcnow_naive()
        agent_run.error_message = message
    if branch is not None:
        branch.current_leaf_message_id = leaf_message_id
    if activate_branch:
        if branch is not None:
            conversation.current_branch_id = branch.id
        conversation.current_leaf_message_id = leaf_message_id
    await session.commit()
    await session.refresh(assistant_message)
    await session.refresh(conversation)
    await close_runtime_sessions(context or {})


async def _mark_cancelled(
    *,
    session: AsyncSession,
    context: dict[str, object] | None,
    conversation: Conversation,
    branch: ConversationBranch | None,
    assistant_message: Message,
    message: str,
    status: MessageStatus,
    leaf_message_id: int,
    activate_branch: bool,
    partial_content: str = "",
) -> None:
    if context is not None:
        pending_tool = _find_pending_tool_call(context)
        if pending_tool is not None:
            await _record_run_event(
                session=session,
                context=context,
                event_type="tool_call.failed",
                step_id=str(pending_tool.get("step_id") or "") or None,
                tool_call_ref=str(pending_tool.get("tool_call_ref") or ""),
                payload={
                    "step_id": str(pending_tool.get("step_id") or "") or None,
                    "tool_name": str(pending_tool.get("tool_name") or ""),
                    "error_message": message,
                },
            )
        await _record_run_event(
            session=session,
            context=context,
            event_type="run.cancelled",
            payload={"error_message": message, "status": status.value},
        )
    assistant_message.content = partial_content
    assistant_message.status = status
    assistant_message.error_message = message
    agent_run = context.get("agent_run") if context is not None else None
    if isinstance(agent_run, AgentRun):
        agent_run.status = RUN_STATUS_CANCELLED
        agent_run.completed_at = utcnow_naive()
        agent_run.error_message = message
    if branch is not None:
        branch.current_leaf_message_id = leaf_message_id
    if activate_branch:
        if branch is not None:
            conversation.current_branch_id = branch.id
        conversation.current_leaf_message_id = leaf_message_id
    await session.commit()
    await session.refresh(assistant_message)
    await session.refresh(conversation)
    await close_runtime_sessions(context or {})


async def _build_send_response(
    *,
    session: AsyncSession,
    conversation: Conversation,
    user_message: Message,
    assistant_message: Message,
    selected_leaf_message_id: int,
    history: list[Message],
) -> MessageSendResponse:
    await session.refresh(user_message)
    await session.refresh(assistant_message)
    await session.refresh(conversation)
    sibling_map = _build_sibling_meta_map(history)
    return MessageSendResponse(
        conversation_id=conversation.id,
        current_branch_id=conversation.current_branch_id,
        current_leaf_message_id=selected_leaf_message_id,
        user_message=_serialize_message_node(user_message, sibling_map=sibling_map),
        assistant_message=_serialize_message_node(assistant_message, sibling_map=sibling_map),
    )


async def _build_regenerate_response(
    *,
    session: AsyncSession,
    conversation: Conversation,
    replaced_message: Message,
    assistant_message: Message,
    selected_leaf_message_id: int,
    history: list[Message],
) -> MessageRegenerateResponse:
    await session.refresh(assistant_message)
    await session.refresh(conversation)
    sibling_map = _build_sibling_meta_map(history)
    return MessageRegenerateResponse(
        conversation_id=conversation.id,
        current_branch_id=conversation.current_branch_id,
        current_leaf_message_id=selected_leaf_message_id,
        replaced_message_id=replaced_message.id,
        assistant_message=_serialize_message_node(assistant_message, sibling_map=sibling_map),
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
        raise AppError(status_code=400, code="VALIDATION_ERROR", message="message_id 不属于当前会话")
    return message


async def _load_conversation_history(
    *,
    session: AsyncSession,
    conversation_id: int,
) -> list[Message]:
    result = await session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc(), Message.id.asc())
    )
    return list(result.all())


async def _load_current_branch(
    *,
    session: AsyncSession,
    conversation: Conversation,
) -> ConversationBranch | None:
    if conversation.current_branch_id is None:
        return None
    return await session.scalar(
        select(ConversationBranch).where(
            ConversationBranch.id == conversation.current_branch_id,
            ConversationBranch.conversation_id == conversation.id,
        )
    )


async def _load_conversation_branches(
    *,
    session: AsyncSession,
    conversation_id: int,
    include_archived: bool = True,
) -> list[ConversationBranch]:
    stmt = select(ConversationBranch).where(ConversationBranch.conversation_id == conversation_id)
    if not include_archived:
        stmt = stmt.where(ConversationBranch.archived_at.is_(None))
    result = await session.scalars(
        stmt.order_by(ConversationBranch.created_at.asc(), ConversationBranch.id.asc())
    )
    return list(result.all())


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


async def _create_assistant_message(
    *,
    session: AsyncSession,
    conversation_id: int,
    parent_id: int | None,
    provider: str,
    model: str,
    temperature,
    max_tokens,
    provider_instance_id: int | None = None,
    adapter_id: str | None = None,
    provider_name_snapshot: str | None = None,
) -> Message:
    assistant_message = Message(
        conversation_id=conversation_id,
        parent_id=parent_id,
        role=MessageRole.ASSISTANT,
        content="",
        parts_json=json_dumps([]),
        parts_schema_version=PARTS_SCHEMA_VERSION,
        parts_updated_at=utcnow_naive(),
        provider=provider,
        provider_instance_id=provider_instance_id,
        adapter_id=adapter_id,
        provider_name_snapshot=provider_name_snapshot,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        status=MessageStatus.STREAMING,
    )
    session.add(assistant_message)
    await session.flush()
    return assistant_message


def _resolve_generation_options(
    *,
    conversation: Conversation,
    provider: str | None,
    model: str | None,
    temperature,
    max_tokens,
) -> tuple[str, str, object, object]:
    resolved_provider = (provider or conversation.provider or "mock").strip().lower()
    default_models = {
        "anthropic": "claude-sonnet-4-20250514",
        "mock": "mock-model",
        "openai": "gpt-4.1-mini",
    }
    resolved_model = model or conversation.model or default_models.get(resolved_provider, "gpt-4.1-mini")
    resolved_temperature = temperature if temperature is not None else conversation.temperature
    resolved_max_tokens = max_tokens if max_tokens is not None else conversation.max_tokens
    return resolved_provider, resolved_model, resolved_temperature, resolved_max_tokens


def _adapter_id_for_generation(
    *,
    provider: str,
    context: dict[str, object] | None = None,
    instance=None,
) -> str:
    """Resolve the request protocol without overloading the provider name."""
    if context is not None and context.get("adapter_id"):
        return str(context["adapter_id"])
    if instance is not None:
        return str(instance.default_adapter_id)
    return DEFAULT_ADAPTER_BY_PROVIDER.get(provider, provider)


def _visible_conversation_messages(
    messages: list[Message],
    current_leaf_message_id: int | None,
    *,
    root_message_id: int | None = None,
) -> list[Message]:
    if not messages:
        return []
    if current_leaf_message_id is None:
        return messages

    by_id = {item.id: item for item in messages}
    lineage_ids = _lineage_ids(by_id, current_leaf_message_id)
    if not lineage_ids:
        return messages
    if root_message_id is not None and root_message_id in lineage_ids:
        lineage_ids = lineage_ids[lineage_ids.index(root_message_id):]
    elif root_message_id is not None:
        return [by_id[root_message_id]] if root_message_id in by_id else []
    return [by_id[item_id] for item_id in lineage_ids]


def _lineage_messages(by_id: dict[int, Message], leaf_message_id: int) -> list[Message]:
    return [by_id[item_id] for item_id in _lineage_ids(by_id, leaf_message_id) if item_id in by_id]


def _lineage_ids(by_id: dict[int, Message], leaf_message_id: int) -> list[int]:
    lineage_ids: list[int] = []
    cursor: int | None = leaf_message_id
    while cursor is not None and cursor in by_id:
        message = by_id[cursor]
        lineage_ids.append(message.id)
        cursor = message.parent_id
    lineage_ids.reverse()
    return lineage_ids


def _build_sibling_meta_map(messages: list[Message]) -> dict[int, tuple[int, int, int | None, int | None]]:
    siblings_by_parent: dict[int | None, list[Message]] = defaultdict(list)
    for message in messages:
        siblings_by_parent[message.parent_id].append(message)

    sibling_map: dict[int, tuple[int, int, int | None, int | None]] = {}
    for siblings in siblings_by_parent.values():
        total = len(siblings)
        for position, message in enumerate(siblings):
            # prev/next wrap around so sibling navigation is circular; a single
            # child has no siblings to move to.
            if total > 1:
                previous_id = siblings[position - 1].id
                next_id = siblings[(position + 1) % total].id
            else:
                previous_id = None
                next_id = None
            sibling_map[message.id] = (position + 1, total, previous_id, next_id)
    return sibling_map


def _serialize_message_node(
    message: Message,
    *,
    sibling_map: dict[int, tuple[int, int, int | None, int | None]],
) -> MessageNodeResponse:
    sibling_index, sibling_count, previous_sibling_id, next_sibling_id = sibling_map.get(
        message.id,
        (1, 1, None, None),
    )
    parsed_parts = parts_from_message(message.parts_json) if message.parts_json is not None else None
    return MessageNodeResponse.model_validate(message).model_copy(
        update={
            "parts": parsed_parts,
            "parts_schema_version": message.parts_schema_version,
            "sibling_index": sibling_index,
            "sibling_count": sibling_count,
            "previous_sibling_id": previous_sibling_id,
            "next_sibling_id": next_sibling_id,
        }
    )


def _serialize_message_tree_node(
    message: Message,
    *,
    sibling_map: dict[int, tuple[int, int, int | None, int | None]],
    child_count: int,
    is_active_path: bool,
    is_current_leaf: bool,
    branch_markers: list[MessageTreeBranchMarkerResponse],
) -> MessageTreeNodeResponse:
    sibling_index, sibling_count, _, _ = sibling_map.get(message.id, (1, 1, None, None))
    return MessageTreeNodeResponse(
        id=message.id,
        conversation_id=message.conversation_id,
        parent_id=message.parent_id,
        role=message.role,
        preview=_message_tree_preview(message.content),
        status=message.status,
        error_message=message.error_message,
        provider=message.provider,
        model=message.model,
        created_at=message.created_at,
        updated_at=message.updated_at,
        sibling_index=sibling_index,
        sibling_count=sibling_count,
        child_count=child_count,
        is_leaf=child_count == 0,
        is_active_path=is_active_path,
        is_current_leaf=is_current_leaf,
        branch_markers=branch_markers,
    )


def serialize_agent_run(run: AgentRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "conversation_id": run.conversation_id,
        "user_message_id": run.user_message_id,
        "assistant_message_id": run.assistant_message_id,
        "provider": run.provider,
        "provider_id": run.provider_instance_id,
        "adapter_id": run.adapter_id,
        "provider_name_snapshot": run.provider_name_snapshot,
        "model": run.model,
        "status": run.status,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "last_sequence": run.last_sequence,
        "resume_token": run.resume_token,
        "error_message": run.error_message,
        "metadata": _run_metadata(run),
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


def serialize_run_event(event: RunEvent) -> dict[str, Any]:
    payload = json_loads(event.payload_json, default={})
    if not isinstance(payload, dict):
        payload = {}
    return {
        "event_id": event.event_id,
        "type": event.event_type,
        "run_id": event.run_id,
        "sequence": event.sequence,
        "assistant_message_id": event.assistant_message_id,
        "step_id": event.step_id,
        "tool_call_ref": event.tool_call_ref,
        "created_at": event.created_at,
        "payload": payload,
    }


def _build_message_tree_branch_markers(
    *,
    branches: list[ConversationBranch],
    current_branch_id: int | None,
) -> dict[int, list[MessageTreeBranchMarkerResponse]]:
    markers: dict[int, list[MessageTreeBranchMarkerResponse]] = defaultdict(list)
    for branch in branches:
        is_current_branch = branch.id == current_branch_id
        if branch.forked_from_message_id is not None:
            markers[branch.forked_from_message_id].append(
                MessageTreeBranchMarkerResponse(
                    id=branch.id,
                    title=branch.title,
                    auto_title=branch.auto_title,
                    marker_type="fork",
                    is_current_branch=is_current_branch,
                )
            )
        if branch.current_leaf_message_id is not None:
            markers[branch.current_leaf_message_id].append(
                MessageTreeBranchMarkerResponse(
                    id=branch.id,
                    title=branch.title,
                    auto_title=branch.auto_title,
                    marker_type="leaf",
                    is_current_branch=is_current_branch,
                )
            )
    return markers


def _message_tree_preview(content: str) -> str:
    compact = " ".join((content or "").split())
    if len(compact) <= MESSAGE_TREE_PREVIEW_LENGTH:
        return compact
    return compact[:MESSAGE_TREE_PREVIEW_LENGTH].rstrip() + "…"


def _select_tree_messages(
    messages: list[Message],
    *,
    by_id: dict[int, Message],
    max_nodes: int,
    anchor_leaf_ids: set[int],
) -> list[Message]:
    """Pick at most ``max_nodes`` messages to render in the tree.

    Connectivity wins over the cap: the full ancestor lineage of every anchor
    leaf (active path, each branch's leaf and fork point) is always kept so no
    important node ends up detached from a root. Any remaining budget is filled
    with the most recent messages. The cap may be exceeded only when the anchor
    lineages alone are larger than it. Output keeps the input ordering.
    """
    if len(messages) <= max_nodes:
        return messages

    keep_ids: set[int] = set()
    for leaf_id in anchor_leaf_ids:
        keep_ids.update(_lineage_ids(by_id, leaf_id))

    if len(keep_ids) < max_nodes:
        for message in reversed(messages):
            if len(keep_ids) >= max_nodes:
                break
            keep_ids.add(message.id)

    return [message for message in messages if message.id in keep_ids]


def _resolve_owning_branch(
    *,
    branches: list[ConversationBranch],
    by_id: dict[int, Message],
    leaf_message_id: int | None,
) -> ConversationBranch | None:
    """Find the branch that owns ``leaf_message_id``.

    A non-main branch owns the leaf when the branch's first unique message
    (the child of its fork point) is an ancestor of the leaf. The deepest such
    fork point wins, so the most specific branch is chosen. The main branch
    (no fork point) is the fallback owner when no child branch claims the leaf.
    """
    if leaf_message_id is None:
        return None

    lineage_ids = _lineage_ids(by_id, leaf_message_id)
    depth_by_message = {message_id: depth for depth, message_id in enumerate(lineage_ids)}

    best_branch: ConversationBranch | None = None
    best_depth = -1
    main_branch: ConversationBranch | None = None
    for branch in branches:
        if branch.forked_from_message_id is None:
            if main_branch is None:
                main_branch = branch
            continue
        subtree_root_id = _branch_message_subtree_root_id(branch=branch, by_id=by_id)
        if subtree_root_id is None:
            continue
        depth = depth_by_message.get(subtree_root_id)
        if depth is not None and depth > best_depth:
            best_depth = depth
            best_branch = branch

    return best_branch or main_branch


def _resolve_branch_leaf_message_id(messages: list[Message], root_message_id: int) -> int:
    by_parent: dict[int | None, list[Message]] = defaultdict(list)
    for message in messages:
        by_parent[message.parent_id].append(message)

    subtree_ids: set[int] = set()
    stack = [root_message_id]
    while stack:
        current_id = stack.pop()
        if current_id in subtree_ids:
            continue
        subtree_ids.add(current_id)
        for child in by_parent.get(current_id, []):
            stack.append(child.id)

    leaves = [message for message in messages if message.id in subtree_ids and not by_parent.get(message.id)]
    if not leaves:
        return root_message_id
    leaves.sort(key=lambda item: (item.updated_at, item.created_at, item.id))
    return leaves[-1].id


def _resolve_latest_leaf_message_id(messages: list[Message]) -> int | None:
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


def _prompt_seed_content(prompt_transcript: list[CanonicalTranscriptItem]) -> str:
    return latest_user_text(prompt_transcript)


def _latest_user_content(*, history: list[Message], parent_id: int | None) -> str | None:
    if parent_id is None:
        return None

    by_id = {item.id: item for item in history}
    for item in reversed(_lineage_messages(by_id, parent_id)):
        if item.role == MessageRole.USER and item.status in {MessageStatus.COMPLETED, MessageStatus.PARTIAL}:
            return item.content
    return None


def _to_app_error(exc: Exception) -> AppError:
    if isinstance(exc, AppError):
        return exc
    return AppError(status_code=500, code="INTERNAL_ERROR", message=str(exc) or "未预期的服务端错误")
