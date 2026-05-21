from __future__ import annotations

from collections import defaultdict
from collections.abc import AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole, MessageStatus
from app.providers import (
    create_openai_compatible_reply,
    generate_mock_reply,
    stream_openai_compatible_reply,
)
from app.schemas.message import (
    ConversationMessagesResponse,
    MessageCreateRequest,
    MessageNodeResponse,
    MessageRegenerateRequest,
    MessageRegenerateResponse,
    MessageSendResponse,
)
from app.services.api_keys import get_preferred_api_key
from app.services.conversations import get_conversation


async def list_conversation_messages(
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
) -> ConversationMessagesResponse:
    conversation = await get_conversation(session=session, user_id=user_id, conversation_id=conversation_id)
    history = await _load_conversation_history(session=session, conversation_id=conversation.id)
    sibling_map = _build_sibling_position_map(history)
    visible_messages = _visible_conversation_messages(history, conversation.current_leaf_message_id)

    return ConversationMessagesResponse(
        conversation_id=conversation.id,
        current_leaf_message_id=conversation.current_leaf_message_id,
        items=[_serialize_message_node(item, sibling_map=sibling_map) for item in visible_messages],
    )


async def create_message_pair(
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    payload: MessageCreateRequest,
) -> MessageSendResponse:
    context = await _prepare_generation(session=session, user_id=user_id, conversation_id=conversation_id, payload=payload)

    try:
        reply_content = await _generate_reply(
            session=session,
            user_id=user_id,
            conversation=context["conversation"],
            provider=context["provider"],
            model=context["model"],
            temperature=context["temperature"],
            max_tokens=context["max_tokens"],
            prompt_messages=context["prompt_messages"],
        )
    except Exception as exc:
        app_error = _to_app_error(exc)
        await _mark_failed(
            session=session,
            conversation=context["conversation"],
            assistant_message=context["assistant_message"],
            message=app_error.message,
            status=MessageStatus.FAILED,
            leaf_message_id=context["user_message"].id,
        )
        raise app_error

    await _finalize_success(
        session=session,
        conversation=context["conversation"],
        assistant_message=context["assistant_message"],
        reply_content=reply_content,
    )
    return await _build_send_response(
        session=session,
        conversation=context["conversation"],
        user_message=context["user_message"],
        assistant_message=context["assistant_message"],
    )


async def create_message_stream(
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    payload: MessageCreateRequest,
) -> AsyncIterator[dict[str, str]]:
    context = await _prepare_generation(session=session, user_id=user_id, conversation_id=conversation_id, payload=payload)

    async def iterator() -> AsyncIterator[dict[str, str]]:
        accumulated = ""
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
            ):
                accumulated += chunk
                yield {"content": chunk}
        except Exception as exc:
            app_error = _to_app_error(exc)
            status = MessageStatus.PARTIAL if accumulated else MessageStatus.FAILED
            leaf_message_id = context["assistant_message"].id if accumulated else context["user_message"].id
            await _mark_failed(
                session=session,
                conversation=context["conversation"],
                assistant_message=context["assistant_message"],
                message=app_error.message,
                status=status,
                leaf_message_id=leaf_message_id,
                partial_content=accumulated,
            )
            yield {"error": app_error.message}
            return

        await _finalize_success(
            session=session,
            conversation=context["conversation"],
            assistant_message=context["assistant_message"],
            reply_content=accumulated,
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

    try:
        reply_content = await _generate_reply(
            session=session,
            user_id=user_id,
            conversation=context["conversation"],
            provider=context["provider"],
            model=context["model"],
            temperature=context["temperature"],
            max_tokens=context["max_tokens"],
            prompt_messages=context["prompt_messages"],
        )
    except Exception as exc:
        app_error = _to_app_error(exc)
        await _mark_failed(
            session=session,
            conversation=context["conversation"],
            assistant_message=context["assistant_message"],
            message=app_error.message,
            status=MessageStatus.FAILED,
            leaf_message_id=context["target_message"].id,
        )
        raise app_error

    await _finalize_success(
        session=session,
        conversation=context["conversation"],
        assistant_message=context["assistant_message"],
        reply_content=reply_content,
    )
    return await _build_regenerate_response(
        session=session,
        conversation=context["conversation"],
        replaced_message=context["target_message"],
        assistant_message=context["assistant_message"],
    )


async def regenerate_message_stream(
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    message_id: int,
    payload: MessageRegenerateRequest,
) -> AsyncIterator[dict[str, str]]:
    context = await _prepare_regeneration(
        session=session,
        user_id=user_id,
        conversation_id=conversation_id,
        message_id=message_id,
        payload=payload,
    )

    async def iterator() -> AsyncIterator[dict[str, str]]:
        accumulated = ""
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
            ):
                accumulated += chunk
                yield {"content": chunk}
        except Exception as exc:
            app_error = _to_app_error(exc)
            status = MessageStatus.PARTIAL if accumulated else MessageStatus.FAILED
            leaf_message_id = context["assistant_message"].id if accumulated else context["target_message"].id
            await _mark_failed(
                session=session,
                conversation=context["conversation"],
                assistant_message=context["assistant_message"],
                message=app_error.message,
                status=status,
                leaf_message_id=leaf_message_id,
                partial_content=accumulated,
            )
            yield {"error": app_error.message}
            return

        await _finalize_success(
            session=session,
            conversation=context["conversation"],
            assistant_message=context["assistant_message"],
            reply_content=accumulated,
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
    parent_id = payload.parent_id if payload.parent_id is not None else conversation.current_leaf_message_id
    if parent_id is not None:
        await _ensure_message_belongs_to_conversation(
            session=session,
            conversation_id=conversation.id,
            message_id=parent_id,
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
    prompt_messages = await _build_prompt_messages(
        session=session,
        conversation=conversation,
        parent_id=parent_id,
        user_content=payload.content,
    )
    return {
        "assistant_message": assistant_message,
        "conversation": conversation,
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
    target_message = await _ensure_message_belongs_to_conversation(
        session=session,
        conversation_id=conversation.id,
        message_id=message_id,
    )
    if target_message.role != MessageRole.ASSISTANT:
        raise AppError(status_code=400, code="VALIDATION_ERROR", message="只能重新生成 assistant 消息")
    if target_message.status == MessageStatus.STREAMING:
        raise AppError(status_code=409, code="CONFLICT", message="消息仍在生成中，暂时不能重新生成")

    provider, model, temperature, max_tokens = _resolve_generation_options(
        conversation=conversation,
        provider=payload.provider or target_message.provider,
        model=payload.model or target_message.model,
        temperature=payload.temperature if payload.temperature is not None else target_message.temperature,
        max_tokens=payload.max_tokens if payload.max_tokens is not None else target_message.max_tokens,
    )

    assistant_message = await _create_assistant_message(
        session=session,
        conversation_id=conversation.id,
        parent_id=target_message.parent_id,
        provider=provider,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    prompt_messages = await _build_prompt_messages(
        session=session,
        conversation=conversation,
        parent_id=target_message.parent_id,
    )
    return {
        "assistant_message": assistant_message,
        "conversation": conversation,
        "max_tokens": max_tokens,
        "model": model,
        "prompt_messages": prompt_messages,
        "provider": provider,
        "target_message": target_message,
        "temperature": temperature,
    }


async def _build_prompt_messages(
    *,
    session: AsyncSession,
    conversation: Conversation,
    parent_id: int | None,
    user_content: str | None = None,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if conversation.system_prompt:
        messages.append({"role": "system", "content": conversation.system_prompt})

    if parent_id is not None:
        history = await _load_conversation_history(session=session, conversation_id=conversation.id)
        by_id = {item.id: item for item in history}
        lineage = _lineage_messages(by_id, parent_id)
        for item in lineage:
            if item.status not in {MessageStatus.COMPLETED, MessageStatus.PARTIAL}:
                continue
            messages.append({"role": item.role.value, "content": item.content})

    if user_content is not None:
        messages.append({"role": "user", "content": user_content})
    return messages


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
) -> str:
    if provider == "mock":
        return generate_mock_reply(
            conversation=conversation,
            content=_prompt_seed_content(prompt_messages),
            model=model,
        )
    if provider == "openai":
        api_key = await get_preferred_api_key(session=session, user_id=user_id, provider=provider)
        return await create_openai_compatible_reply(
            api_key=api_key,
            model=model,
            messages=prompt_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
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
) -> AsyncIterator[str]:
    if provider == "mock":
        yield generate_mock_reply(
            conversation=conversation,
            content=_prompt_seed_content(prompt_messages),
            model=model,
        )
        return
    if provider == "openai":
        api_key = await get_preferred_api_key(session=session, user_id=user_id, provider=provider)
        async for chunk in stream_openai_compatible_reply(
            api_key=api_key,
            model=model,
            messages=prompt_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            yield chunk
        return
    raise AppError(status_code=422, code="VALIDATION_ERROR", message=f"暂不支持 provider '{provider}'")


async def _finalize_success(
    *,
    session: AsyncSession,
    conversation: Conversation,
    assistant_message: Message,
    reply_content: str,
) -> None:
    assistant_message.content = reply_content
    assistant_message.status = MessageStatus.COMPLETED
    assistant_message.error_message = None
    conversation.current_leaf_message_id = assistant_message.id
    await session.commit()
    await session.refresh(assistant_message)
    await session.refresh(conversation)


async def _mark_failed(
    *,
    session: AsyncSession,
    conversation: Conversation,
    assistant_message: Message,
    message: str,
    status: MessageStatus,
    leaf_message_id: int,
    partial_content: str = "",
) -> None:
    assistant_message.content = partial_content
    assistant_message.status = status
    assistant_message.error_message = message
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
) -> MessageSendResponse:
    await session.refresh(user_message)
    await session.refresh(assistant_message)
    await session.refresh(conversation)
    history = await _load_conversation_history(session=session, conversation_id=conversation.id)
    sibling_map = _build_sibling_position_map(history)
    return MessageSendResponse(
        conversation_id=conversation.id,
        current_leaf_message_id=conversation.current_leaf_message_id,
        user_message=_serialize_message_node(user_message, sibling_map=sibling_map),
        assistant_message=_serialize_message_node(assistant_message, sibling_map=sibling_map),
    )


async def _build_regenerate_response(
    *,
    session: AsyncSession,
    conversation: Conversation,
    replaced_message: Message,
    assistant_message: Message,
) -> MessageRegenerateResponse:
    await session.refresh(assistant_message)
    await session.refresh(conversation)
    history = await _load_conversation_history(session=session, conversation_id=conversation.id)
    sibling_map = _build_sibling_position_map(history)
    return MessageRegenerateResponse(
        conversation_id=conversation.id,
        current_leaf_message_id=conversation.current_leaf_message_id,
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
        raise AppError(status_code=400, code="VALIDATION_ERROR", message="parent_id 不属于当前会话")
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
    resolved_model = model or conversation.model or ("mock-model" if resolved_provider == "mock" else "gpt-4.1-mini")
    resolved_temperature = temperature if temperature is not None else conversation.temperature
    resolved_max_tokens = max_tokens if max_tokens is not None else conversation.max_tokens
    return resolved_provider, resolved_model, resolved_temperature, resolved_max_tokens


def _visible_conversation_messages(messages: list[Message], current_leaf_message_id: int | None) -> list[Message]:
    if not messages:
        return []
    if current_leaf_message_id is None:
        return messages

    by_id = {item.id: item for item in messages}
    lineage_ids = _lineage_ids(by_id, current_leaf_message_id)
    if not lineage_ids:
        return messages
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


def _build_sibling_position_map(messages: list[Message]) -> dict[int, tuple[int, int]]:
    siblings_by_parent: dict[int | None, list[Message]] = defaultdict(list)
    for message in messages:
        siblings_by_parent[message.parent_id].append(message)

    sibling_map: dict[int, tuple[int, int]] = {}
    for siblings in siblings_by_parent.values():
        total = len(siblings)
        for index, message in enumerate(siblings, start=1):
            sibling_map[message.id] = (index, total)
    return sibling_map


def _serialize_message_node(
    message: Message,
    *,
    sibling_map: dict[int, tuple[int, int]],
) -> MessageNodeResponse:
    sibling_index, sibling_count = sibling_map.get(message.id, (1, 1))
    return MessageNodeResponse.model_validate(message).model_copy(
        update={
            "sibling_index": sibling_index,
            "sibling_count": sibling_count,
        }
    )


def _prompt_seed_content(prompt_messages: list[dict[str, str]]) -> str:
    for item in reversed(prompt_messages):
        if item["role"] == "user":
            return item["content"]
    if prompt_messages:
        return prompt_messages[-1]["content"]
    return ""


def _to_app_error(exc: Exception) -> AppError:
    if isinstance(exc, AppError):
        return exc
    return AppError(status_code=500, code="INTERNAL_ERROR", message=str(exc) or "未预期的服务端错误")
