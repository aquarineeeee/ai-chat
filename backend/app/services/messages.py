from __future__ import annotations
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.agent_run import AgentRun
from app.models.branch import ConversationBranch
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole, MessageStatus
from app.models.run_event import RunEvent
from app.models.tool_call import ToolCall
from app.providers import (
    create_anthropic_reply,
    create_openai_compatible_reply,
    generate_mock_reply,
    stream_anthropic_reply,
    stream_openai_compatible_reply,
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
from app.services.api_keys import get_preferred_api_key
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


MESSAGE_TREE_PREVIEW_LENGTH = 100
MESSAGE_TREE_MAX_NODES = 400
MEMORY_TOOL_PROVIDERS = {"openai", "anthropic"}
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
RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_FAILED = "failed"
RUN_STATUS_CANCELLED = "cancelled"

TOOL_STATUS_PENDING = "pending"
TOOL_STATUS_READY = "ready"
TOOL_STATUS_RUNNING = "running"
TOOL_STATUS_SUCCESS = "success"
TOOL_STATUS_ERROR = "error"


def _new_event_id(prefix: str = "evt") -> str:
    return f"{prefix}_{uuid4().hex}"


def _trace_state(context: dict[str, object]) -> dict[str, object]:
    state = context.get("trace_state")
    if isinstance(state, dict):
        return state
    state = {"open_tool_calls": []}
    context["trace_state"] = state
    return state


async def list_conversation_messages(
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    leaf_message_id: int | None = None,
    root_message_id: int | None = None,
    expand_leaf_descendants: bool = False,
) -> ConversationMessagesResponse:
    conversation = await get_conversation(session=session, user_id=user_id, conversation_id=conversation_id)
    history = await _load_conversation_history(session=session, conversation_id=conversation.id)

    if root_message_id is not None:
        await _ensure_message_belongs_to_conversation(
            session=session,
            conversation_id=conversation.id,
            message_id=root_message_id,
        )

    if leaf_message_id is not None:
        await _ensure_message_belongs_to_conversation(
            session=session,
            conversation_id=conversation.id,
            message_id=leaf_message_id,
        )
        selected_leaf_message_id = (
            _resolve_branch_leaf_message_id(history, leaf_message_id)
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

    return _build_messages_response(
        conversation=conversation,
        history=history,
        selected_leaf_message_id=selected_leaf_message_id,
        root_message_id=root_message_id,
    )


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

    async def iterator() -> AsyncIterator[dict[str, object]]:
        accumulated = ""
        usage: dict[str, int] | None = None

        async def capture_usage(value: dict[str, int] | None) -> None:
            nonlocal usage
            usage = value

        try:
            async for chunk in _stream_reply(
                session=session,
                user_id=user_id,
                conversation=context["conversation"],
                provider=context["provider"],
                model=context["model"],
                temperature=context["temperature"],
                max_tokens=context["max_tokens"],
                prompt_messages=context["prompt_messages"],
                usage_callback=capture_usage,
            ):
                chunk_type = str(chunk.get("type") or "")
                if chunk_type == "content":
                    content = str(chunk.get("content") or "")
                    accumulated += content
                    await _record_text_delta(session=session, context=context, text=content)
                    yield {"content": content}
                elif chunk_type == "tool":
                    tool = chunk.get("tool")
                    if isinstance(tool, dict):
                        await _record_tool_event(session=session, context=context, tool=tool)
                        yield {"tool": tool}
        except Exception as exc:
            app_error = _to_app_error(exc)
            status = MessageStatus.PARTIAL if accumulated else MessageStatus.FAILED
            leaf_message_id = context["assistant_message"].id if accumulated else context["user_message"].id
            await _mark_failed(
                session=session,
                context=context,
                conversation=context["conversation"],
                branch=context["branch"],
                assistant_message=context["assistant_message"],
                message=app_error.message,
                status=status,
                leaf_message_id=leaf_message_id,
                partial_content=accumulated,
                activate_branch=context["activate_branch"],
            )
            yield {"error": app_error.message}
            return

        await _finalize_success(
            session=session,
            context=context,
            conversation=context["conversation"],
            branch=context["branch"],
            assistant_message=context["assistant_message"],
            reply_content=accumulated,
            usage=usage,
            activate_branch=context["activate_branch"],
        )

    return iterator()


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

    async def iterator() -> AsyncIterator[dict[str, object]]:
        accumulated = ""
        usage: dict[str, int] | None = None

        async def capture_usage(value: dict[str, int] | None) -> None:
            nonlocal usage
            usage = value

        try:
            async for chunk in _stream_reply(
                session=session,
                user_id=user_id,
                conversation=context["conversation"],
                provider=context["provider"],
                model=context["model"],
                temperature=context["temperature"],
                max_tokens=context["max_tokens"],
                prompt_messages=context["prompt_messages"],
                usage_callback=capture_usage,
            ):
                chunk_type = str(chunk.get("type") or "")
                if chunk_type == "content":
                    content = str(chunk.get("content") or "")
                    accumulated += content
                    await _record_text_delta(session=session, context=context, text=content)
                    yield {"content": content}
                elif chunk_type == "tool":
                    tool = chunk.get("tool")
                    if isinstance(tool, dict):
                        await _record_tool_event(session=session, context=context, tool=tool)
                        yield {"tool": tool}
        except Exception as exc:
            app_error = _to_app_error(exc)
            status = MessageStatus.PARTIAL if accumulated else MessageStatus.FAILED
            leaf_message_id = context["assistant_message"].id if accumulated else context["target_message"].id
            await _mark_failed(
                session=session,
                context=context,
                conversation=context["conversation"],
                branch=context["branch"],
                assistant_message=context["assistant_message"],
                message=app_error.message,
                status=status,
                leaf_message_id=leaf_message_id,
                partial_content=accumulated,
                activate_branch=context["activate_branch"],
            )
            yield {"error": app_error.message}
            return

        await _finalize_success(
            session=session,
            context=context,
            conversation=context["conversation"],
            branch=context["branch"],
            assistant_message=context["assistant_message"],
            reply_content=accumulated,
            usage=usage,
            activate_branch=context["activate_branch"],
        )

    return iterator()


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
    if payload.context_mode == "root_only":
        context_root_message_id = context_root_message_id or parent_id
        if context_root_message_id is not None:
            await _ensure_message_belongs_to_conversation(
                session=session,
                conversation_id=conversation.id,
                message_id=context_root_message_id,
            )

    provider, model, temperature, max_tokens = _resolve_generation_options(
        conversation=conversation,
        provider=payload.provider,
        model=payload.model,
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
    )

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
    )
    # Both new messages are flushed above, so this snapshot includes them and can
    # be reused for the response (expire_on_commit=False keeps the objects live).
    history = await _load_conversation_history(session=session, conversation_id=conversation.id)
    prompt_messages = await _build_prompt_messages(
        session=session,
        conversation=conversation,
        parent_id=parent_id,
        user_content=payload.content,
        context_mode=payload.context_mode,
        context_root_message_id=context_root_message_id,
        history=history,
        include_memory_context=provider not in MEMORY_TOOL_PROVIDERS,
        include_memory_tool_guidance=provider in MEMORY_TOOL_PROVIDERS,
    )
    return {
        "activate_branch": payload.activate_branch,
        "assistant_message": assistant_message,
        "branch": branch,
        "conversation": conversation,
        "history": history,
        "max_tokens": max_tokens,
        "model": model,
        "prompt_messages": prompt_messages,
        "provider": provider,
        "temperature": temperature,
        "user_message": user_message,
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
    if target_message.role == MessageRole.ASSISTANT:
        if target_message.status == MessageStatus.STREAMING:
            raise AppError(status_code=409, code="CONFLICT", message="消息仍在生成中，暂时不能重新生成")
        parent_id = target_message.parent_id
        provider, model, temperature, max_tokens = _resolve_generation_options(
            conversation=conversation,
            provider=payload.provider or target_message.provider,
            model=payload.model or target_message.model,
            temperature=payload.temperature if payload.temperature is not None else target_message.temperature,
            max_tokens=payload.max_tokens if payload.max_tokens is not None else target_message.max_tokens,
        )
    elif target_message.role == MessageRole.USER:
        parent_id = target_message.id
        provider, model, temperature, max_tokens = _resolve_generation_options(
            conversation=conversation,
            provider=payload.provider,
            model=payload.model,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
        )
    else:
        raise AppError(status_code=400, code="VALIDATION_ERROR", message="不支持重新生成此类型消息")

    context_root_message_id = payload.context_root_message_id
    if payload.context_mode == "root_only":
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
    )
    # Assistant placeholder is flushed above, so this snapshot includes it and can
    # be reused for the response (expire_on_commit=False keeps the objects live).
    history = await _load_conversation_history(session=session, conversation_id=conversation.id)
    prompt_messages = await _build_prompt_messages(
        session=session,
        conversation=conversation,
        parent_id=parent_id,
        context_mode=payload.context_mode,
        context_root_message_id=context_root_message_id,
        history=history,
        include_memory_context=provider not in MEMORY_TOOL_PROVIDERS,
        include_memory_tool_guidance=provider in MEMORY_TOOL_PROVIDERS,
    )
    return {
        "activate_branch": payload.activate_branch,
        "assistant_message": assistant_message,
        "branch": branch,
        "conversation": conversation,
        "history": history,
        "max_tokens": max_tokens,
        "model": model,
        "prompt_messages": prompt_messages,
        "provider": provider,
        "target_message": target_message,
        "temperature": temperature,
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
        model=str(context["model"]),
        status=RUN_STATUS_RUNNING,
        started_at=utcnow_naive(),
        resume_token=_new_event_id("run"),
        metadata_json=json_dumps({"phase": "1A"}),
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
        event_type="assistant_message.created",
        payload={"assistant_message_id": assistant_message.id},
    )


async def _record_text_delta(
    *,
    session: AsyncSession,
    context: dict[str, object],
    text: str,
) -> None:
    if not text:
        return
    await _record_run_event(
        session=session,
        context=context,
        event_type="message.text.delta",
        payload={"text": text},
    )


async def _record_tool_event(
    *,
    session: AsyncSession,
    context: dict[str, object],
    tool: dict[str, object],
) -> None:
    status = str(tool.get("status") or "")
    tool_name = str(tool.get("name") or "")
    if not status or not tool_name:
        return

    state = _trace_state(context)
    open_tool_calls = state.setdefault("open_tool_calls", [])
    assert isinstance(open_tool_calls, list)

    if status == "running":
        raw_arguments = str(tool.get("arguments") or "")
        display_input = sanitize_tool_input_for_display(_maybe_parse_json(raw_arguments))
        tool_call_ref = _new_event_id("tc")
        open_tool_calls.append({"tool_call_ref": tool_call_ref, "tool_name": tool_name, "completed": False})
        await _record_run_event(
            session=session,
            context=context,
            event_type="tool_call.created",
            tool_call_ref=tool_call_ref,
            payload={
                "tool_name": tool_name,
                "display_input_preview": build_preview(display_input),
                "input_for_model_json": raw_arguments,
            },
        )
        await _record_run_event(
            session=session,
            context=context,
            event_type="tool_call.arguments.completed",
            tool_call_ref=tool_call_ref,
            payload={
                "tool_name": tool_name,
                "display_input_preview": build_preview(display_input),
                "input_for_model_json": raw_arguments,
            },
        )
        await _record_run_event(
            session=session,
            context=context,
            event_type="tool_call.started",
            tool_call_ref=tool_call_ref,
            payload={"tool_name": tool_name},
        )
        return

    if status != "completed":
        return

    tool_call_ref = _pop_open_tool_call_ref(state=state, tool_name=tool_name)
    raw_output = str(tool.get("content") or "")
    display_output = sanitize_tool_output_for_display(_maybe_parse_json(raw_output))
    audit_output = sanitize_tool_output_for_audit(_maybe_parse_json(raw_output))
    await _record_run_event(
        session=session,
        context=context,
        event_type="tool_call.completed",
        tool_call_ref=tool_call_ref,
        payload={
            "tool_name": tool_name,
            "display_output_preview": build_preview(display_output),
            "audit_output_preview": build_preview(audit_output),
            "output_for_model_json": raw_output,
            "output_size_bytes": len(raw_output.encode("utf-8")),
        },
    )


async def _record_run_event(
    *,
    session: AsyncSession,
    context: dict[str, object],
    event_type: str,
    payload: dict[str, Any],
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
    tool_call_ref: str | None,
    sequence: int,
) -> None:
    assistant_message = context["assistant_message"]
    conversation = context["conversation"]
    agent_run = context["agent_run"]
    assert isinstance(assistant_message, Message)
    assert isinstance(conversation, Conversation)
    assert isinstance(agent_run, AgentRun)

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

    if event_type.startswith("tool_call."):
        await _project_tool_call_event(
            session=session,
            run=agent_run,
            conversation=conversation,
            assistant_message=assistant_message,
            event_type=event_type,
            payload=payload,
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


def _pop_open_tool_call_ref(*, state: dict[str, object], tool_name: str) -> str | None:
    open_tool_calls = state.get("open_tool_calls")
    if not isinstance(open_tool_calls, list):
        return None

    for item in reversed(open_tool_calls):
        if not isinstance(item, dict):
            continue
        if item.get("tool_name") != tool_name or item.get("completed") is True:
            continue
        item["completed"] = True
        tool_call_ref = item.get("tool_call_ref")
        return str(tool_call_ref) if tool_call_ref else None
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


async def _build_prompt_messages(
    *,
    session: AsyncSession,
    conversation: Conversation,
    parent_id: int | None,
    user_content: str | None = None,
    context_mode: str = "full",
    context_root_message_id: int | None = None,
    history: list[Message] | None = None,
    include_memory_context: bool = True,
    include_memory_tool_guidance: bool = False,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if conversation.system_prompt:
        messages.append({"role": "system", "content": conversation.system_prompt})
    if include_memory_tool_guidance:
        messages.append({"role": "system", "content": MEMORY_TOOL_GUIDANCE})

    if include_memory_context:
        memory_query = user_content.strip() if user_content else None
        if memory_query is None and history is not None:
            memory_query = _latest_user_content(history=history, parent_id=parent_id)
        if memory_query:
            memory_context = await search_memory(query=memory_query)
            if memory_context:
                messages.append({"role": "system", "content": memory_context})

    if history is None:
        history = await _load_conversation_history(session=session, conversation_id=conversation.id)
    by_id = {item.id: item for item in history}

    if context_mode == "root_only":
        if context_root_message_id is not None and context_root_message_id in by_id:
            root_message = by_id[context_root_message_id]
            if root_message.status in {MessageStatus.COMPLETED, MessageStatus.PARTIAL}:
                messages.append({"role": root_message.role.value, "content": root_message.content})
    elif parent_id is not None:
        for item in _lineage_messages(by_id, parent_id):
            if item.status in {MessageStatus.COMPLETED, MessageStatus.PARTIAL}:
                messages.append({"role": item.role.value, "content": item.content})

    if user_content is not None:
        messages.append({"role": "user", "content": user_content})
    return messages


async def _collect_reply_from_stream(
    *,
    session: AsyncSession,
    context: dict[str, object],
    user_id: int,
) -> tuple[str, dict[str, int] | None]:
    accumulated = ""
    usage: dict[str, int] | None = None

    async def capture_usage(value: dict[str, int] | None) -> None:
        nonlocal usage
        usage = value

    async for chunk in _stream_reply(
        session=session,
        user_id=user_id,
        conversation=context["conversation"],
        provider=context["provider"],
        model=context["model"],
        temperature=context["temperature"],
        max_tokens=context["max_tokens"],
        prompt_messages=context["prompt_messages"],
        usage_callback=capture_usage,
    ):
        chunk_type = str(chunk.get("type") or "")
        if chunk_type == "content":
            content = str(chunk.get("content") or "")
            accumulated += content
            await _record_text_delta(session=session, context=context, text=content)
        elif chunk_type == "tool":
            tool = chunk.get("tool")
            if isinstance(tool, dict):
                await _record_tool_event(session=session, context=context, tool=tool)

    return accumulated, usage


async def _generate_reply(
    *,
    session: AsyncSession,
    user_id: int,
    conversation: Conversation,
    provider: str,
    model: str,
    temperature,
    max_tokens,
    prompt_messages: list[dict[str, str]],
) -> dict[str, object]:
    if provider == "mock":
        return {
            "content": generate_mock_reply(
                conversation=conversation,
                content=_prompt_seed_content(prompt_messages),
                model=model,
            ),
            "usage": None,
        }
    if provider == "openai":
        api_key = await get_preferred_api_key(session=session, user_id=user_id, provider=provider)
        reply = await create_openai_compatible_reply(
            api_key=api_key,
            model=model,
            messages=prompt_messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=memory_tool_definitions(include_grow=True, include_pulse=True, include_dream=True),
            tool_executor=execute_memory_tool_call,
        )
        return {
            "content": str(reply),
            "usage": getattr(reply, "usage", None),
        }
    if provider == "anthropic":
        api_key = await get_preferred_api_key(session=session, user_id=user_id, provider=provider)
        return {
            "content": await create_anthropic_reply(
                api_key=api_key,
                model=model,
                messages=prompt_messages,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=memory_tool_definitions(include_grow=True, include_pulse=True, include_dream=True),
                tool_executor=execute_memory_tool_call,
            ),
            "usage": None,
        }
    raise AppError(status_code=422, code="VALIDATION_ERROR", message=f"暂不支持 provider '{provider}'")


async def _stream_reply(
    *,
    session: AsyncSession,
    user_id: int,
    conversation: Conversation,
    provider: str,
    model: str,
    temperature,
    max_tokens,
    prompt_messages: list[dict[str, str]],
    usage_callback=None,
) -> AsyncIterator[dict[str, object]]:
    if provider == "mock":
        yield {
            "type": "content",
            "content": generate_mock_reply(
                conversation=conversation,
                content=_prompt_seed_content(prompt_messages),
                model=model,
            ),
        }
        return
    if provider == "openai":
        api_key = await get_preferred_api_key(session=session, user_id=user_id, provider=provider)
        async def emit_tool_event(tool: dict[str, object]) -> None:
            yield_event.append({"type": "tool", "tool": tool})

        yield_event: list[dict[str, object]] = []
        async for chunk in stream_openai_compatible_reply(
            api_key=api_key,
            model=model,
            messages=prompt_messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=memory_tool_definitions(include_grow=True, include_pulse=True, include_dream=True),
            tool_executor=execute_memory_tool_call,
            tool_event_callback=emit_tool_event,
            usage_callback=usage_callback,
        ):
            while yield_event:
                yield yield_event.pop(0)
            yield chunk
        while yield_event:
            yield yield_event.pop(0)
        return
    if provider == "anthropic":
        api_key = await get_preferred_api_key(session=session, user_id=user_id, provider=provider)
        async def emit_tool_event(tool: dict[str, object]) -> None:
            yield_event.append({"type": "tool", "tool": tool})

        yield_event: list[dict[str, object]] = []
        async for chunk in stream_anthropic_reply(
            api_key=api_key,
            model=model,
            messages=prompt_messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=memory_tool_definitions(include_grow=True, include_pulse=True, include_dream=True),
            tool_executor=execute_memory_tool_call,
            tool_event_callback=emit_tool_event,
        ):
            while yield_event:
                yield yield_event.pop(0)
            yield {"type": "content", "content": chunk}
        while yield_event:
            yield yield_event.pop(0)
        return
    raise AppError(status_code=422, code="VALIDATION_ERROR", message=f"暂不支持 provider '{provider}'")


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
    await _record_run_event(
        session=session,
        context=context,
        event_type="message.completed",
        payload={"status": MessageStatus.COMPLETED.value},
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
                tool_call_ref=str(pending_tool.get("tool_call_ref") or ""),
                payload={
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


def _prompt_seed_content(prompt_messages: list[dict[str, str]]) -> str:
    for item in reversed(prompt_messages):
        if item["role"] == "user":
            return item["content"]
    if prompt_messages:
        return prompt_messages[-1]["content"]
    return ""


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
