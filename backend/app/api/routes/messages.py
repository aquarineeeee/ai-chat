from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session, get_current_user
from app.models.user import User
from app.schemas.agent_run import (
    AgentRunListResponse,
    AgentRunResponse,
    RunApprovalDecisionRequest,
    RunEventListResponse,
    RunEventResponse,
    RunViewResponse,
)
from app.schemas.message import (
    MessageEditRequest,
    MessageEditResponse,
    ConversationMessageTreeResponse,
    ConversationMessagesResponse,
    MessageCreateRequest,
    MessageNodeResponse,
    MessageRegenerateRequest,
    MessageRegenerateResponse,
    MessageSendResponse,
)
from app.services.messages import (
    activate_message_branch,
    create_message_pair,
    create_message_stream,
    delete_message,
    edit_message,
    get_conversation_message,
    get_conversation_message_tree,
    list_conversation_messages,
    list_agent_runs_for_conversation,
    list_run_events_for_conversation_run,
    get_run_view_for_conversation_run,
    regenerate_message,
    regenerate_message_stream,
    serialize_agent_run,
    serialize_run_event,
    stream_run_events,
    submit_tool_approval_decision,
)


router = APIRouter()


def _resolve_after_sequence(*, after_sequence: int, request: Request) -> int:
    raw_last_event_id = request.headers.get("Last-Event-ID")
    if raw_last_event_id is None:
        return after_sequence
    try:
        return max(after_sequence, int(raw_last_event_id))
    except (TypeError, ValueError):
        return after_sequence


def _encode_sse_chunk(chunk: dict[str, object]) -> str:
    lines: list[str] = []
    sequence = chunk.get("sequence")
    if isinstance(sequence, int):
        lines.append(f"id: {sequence}")
    lines.append(f"data: {json.dumps(chunk, ensure_ascii=False)}")
    return "\n".join(lines) + "\n\n"


@router.get("/conversations/{conversation_id}/runs", response_model=AgentRunListResponse)
async def conversation_runs_index(
    conversation_id: int,
    status: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> AgentRunListResponse:
    items = await list_agent_runs_for_conversation(
        session=session,
        user_id=current_user.id,
        conversation_id=conversation_id,
        status=status,
    )
    return AgentRunListResponse(items=[AgentRunResponse.model_validate(serialize_agent_run(item)) for item in items])


@router.get("/conversations/{conversation_id}/runs/{run_id}/events", response_model=RunEventListResponse)
async def conversation_run_events_index(
    conversation_id: int,
    run_id: int,
    after_sequence: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> RunEventListResponse:
    items = await list_run_events_for_conversation_run(
        session=session,
        user_id=current_user.id,
        conversation_id=conversation_id,
        run_id=run_id,
        after_sequence=after_sequence,
    )
    return RunEventListResponse(
        run_id=run_id,
        after_sequence=after_sequence,
        items=[RunEventResponse.model_validate(serialize_run_event(item)) for item in items],
    )


@router.get("/conversations/{conversation_id}/runs/{run_id}/view", response_model=RunViewResponse)
async def conversation_run_view(
    conversation_id: int,
    run_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> RunViewResponse:
    view = await get_run_view_for_conversation_run(
        session=session,
        user_id=current_user.id,
        conversation_id=conversation_id,
        run_id=run_id,
    )
    return RunViewResponse.model_validate(view)


@router.get("/conversations/{conversation_id}/runs/{run_id}/stream")
async def conversation_run_stream(
    conversation_id: int,
    run_id: int,
    request: Request,
    after_sequence: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> StreamingResponse:
    resolved_after_sequence = _resolve_after_sequence(after_sequence=after_sequence, request=request)
    stream = stream_run_events(
        session=session,
        user_id=current_user.id,
        conversation_id=conversation_id,
        run_id=run_id,
        after_sequence=resolved_after_sequence,
    )

    async def event_stream():
        async for chunk in stream:
            yield _encode_sse_chunk(chunk)
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/conversations/{conversation_id}/runs/{run_id}/approve", response_model=AgentRunResponse)
async def conversation_run_approve(
    conversation_id: int,
    run_id: int,
    payload: RunApprovalDecisionRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> AgentRunResponse:
    run = await submit_tool_approval_decision(
        session=session,
        user_id=current_user.id,
        conversation_id=conversation_id,
        run_id=run_id,
        payload=payload,
        approved=True,
    )
    return AgentRunResponse.model_validate(serialize_agent_run(run))


@router.post("/conversations/{conversation_id}/runs/{run_id}/deny", response_model=AgentRunResponse)
async def conversation_run_deny(
    conversation_id: int,
    run_id: int,
    payload: RunApprovalDecisionRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> AgentRunResponse:
    run = await submit_tool_approval_decision(
        session=session,
        user_id=current_user.id,
        conversation_id=conversation_id,
        run_id=run_id,
        payload=payload,
        approved=False,
    )
    return AgentRunResponse.model_validate(serialize_agent_run(run))


@router.get("/conversations/{conversation_id}/messages", response_model=ConversationMessagesResponse)
async def messages_index(
    conversation_id: int,
    leaf_message_id: int | None = Query(default=None, ge=1),
    root_message_id: int | None = Query(default=None, ge=1),
    expand_leaf_descendants: bool = False,
    before_message_id: int | None = Query(default=None, ge=1),
    limit: int | None = Query(default=None, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> ConversationMessagesResponse:
    return await list_conversation_messages(
        session=session,
        user_id=current_user.id,
        conversation_id=conversation_id,
        leaf_message_id=leaf_message_id,
        root_message_id=root_message_id,
        expand_leaf_descendants=expand_leaf_descendants,
        before_message_id=before_message_id,
        limit=limit,
    )


@router.get("/conversations/{conversation_id}/message-tree", response_model=ConversationMessageTreeResponse)
async def messages_tree(
    conversation_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> ConversationMessageTreeResponse:
    return await get_conversation_message_tree(
        session=session,
        user_id=current_user.id,
        conversation_id=conversation_id,
    )


@router.get("/conversations/{conversation_id}/messages/{message_id}", response_model=MessageNodeResponse)
async def messages_show(
    conversation_id: int,
    message_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> MessageNodeResponse:
    return await get_conversation_message(
        session=session,
        user_id=current_user.id,
        conversation_id=conversation_id,
        message_id=message_id,
    )


@router.post("/conversations/{conversation_id}/messages", response_model=MessageSendResponse)
async def messages_create(
    conversation_id: int,
    payload: MessageCreateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> MessageSendResponse:
    return await create_message_pair(
        session=session,
        user_id=current_user.id,
        conversation_id=conversation_id,
        payload=payload,
    )


@router.post("/conversations/{conversation_id}/messages/stream")
async def messages_create_stream(
    conversation_id: int,
    payload: MessageCreateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> StreamingResponse:
    stream = await create_message_stream(
        session=session,
        user_id=current_user.id,
        conversation_id=conversation_id,
        payload=payload,
    )

    async def event_stream():
        async for chunk in stream:
            yield _encode_sse_chunk(chunk)
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/regenerate",
    response_model=MessageRegenerateResponse,
)
async def messages_regenerate(
    conversation_id: int,
    message_id: int,
    payload: MessageRegenerateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> MessageRegenerateResponse:
    return await regenerate_message(
        session=session,
        user_id=current_user.id,
        conversation_id=conversation_id,
        message_id=message_id,
        payload=payload,
    )


@router.post("/conversations/{conversation_id}/messages/{message_id}/regenerate/stream")
async def messages_regenerate_stream(
    conversation_id: int,
    message_id: int,
    payload: MessageRegenerateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> StreamingResponse:
    stream = await regenerate_message_stream(
        session=session,
        user_id=current_user.id,
        conversation_id=conversation_id,
        message_id=message_id,
        payload=payload,
    )

    async def event_stream():
        async for chunk in stream:
            yield _encode_sse_chunk(chunk)
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/conversations/{conversation_id}/messages/{message_id}/activate", response_model=ConversationMessagesResponse)
async def messages_activate_branch(
    conversation_id: int,
    message_id: int,
    exact: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
    ) -> ConversationMessagesResponse:
    return await activate_message_branch(
        session=session,
        user_id=current_user.id,
        conversation_id=conversation_id,
        message_id=message_id,
        exact=exact,
    )


@router.post("/conversations/{conversation_id}/messages/{message_id}/edit", response_model=MessageEditResponse)
async def messages_edit(
    conversation_id: int,
    message_id: int,
    payload: MessageEditRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> MessageEditResponse:
    return await edit_message(
        session=session,
        user_id=current_user.id,
        conversation_id=conversation_id,
        message_id=message_id,
        payload=payload,
    )


@router.delete("/conversations/{conversation_id}/messages/{message_id}", status_code=204)
async def messages_delete(
    conversation_id: int,
    message_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> Response:
    await delete_message(
        session=session,
        user_id=current_user.id,
        conversation_id=conversation_id,
        message_id=message_id,
    )
    return Response(status_code=204)
