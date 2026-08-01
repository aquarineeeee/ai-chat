from __future__ import annotations

import json
import inspect
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import Message, MessageRole, MessageStatus
from app.models.run_event import RunEvent
from app.models.tool_call import ToolCall
TranscriptItemKind = Literal[
    "system_text",
    "user_text",
    "assistant_text",
    "assistant_tool_call",
    "tool_result",
]

TRACE_EVENT_TYPES = (
    "message.text.delta",
    "message.text.retracted",
    "assistant.model_text",
    "tool_call.started",
    "tool_call.completed",
)


@dataclass(frozen=True, slots=True)
class CanonicalTranscriptItem:
    kind: TranscriptItemKind
    text: str = ""
    tool_call_id: str = ""
    tool_name: str = ""
    arguments: str = ""
    result: str = ""


def system_text_item(text: str) -> CanonicalTranscriptItem:
    return CanonicalTranscriptItem(kind="system_text", text=text)


def user_text_item(text: str) -> CanonicalTranscriptItem:
    return CanonicalTranscriptItem(kind="user_text", text=text)


def assistant_text_item(text: str) -> CanonicalTranscriptItem:
    return CanonicalTranscriptItem(kind="assistant_text", text=text)


def assistant_tool_call_item(*, tool_call_id: str, tool_name: str, arguments: str) -> CanonicalTranscriptItem:
    return CanonicalTranscriptItem(
        kind="assistant_tool_call",
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        arguments=arguments,
    )


def tool_result_item(*, tool_call_id: str, tool_name: str, result: str) -> CanonicalTranscriptItem:
    return CanonicalTranscriptItem(
        kind="tool_result",
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        result=result,
    )


def latest_user_text(transcript: list[CanonicalTranscriptItem]) -> str:
    for item in reversed(transcript):
        if item.kind == "user_text":
            return item.text
    return ""


def transcript_to_simple_messages(transcript: list[CanonicalTranscriptItem]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for item in transcript:
        if item.kind == "system_text":
            messages.append({"role": "system", "content": item.text})
        elif item.kind == "user_text":
            messages.append({"role": "user", "content": item.text})
        elif item.kind == "assistant_text":
            messages.append({"role": "assistant", "content": item.text})
    return messages


def parse_tool_arguments(arguments: str) -> dict[str, object]:
    raw = arguments.strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": arguments}
    if isinstance(parsed, dict):
        return parsed
    return {"raw": arguments}


def build_assistant_transcript_from_trace(
    *,
    message: Message,
    events: list[RunEvent],
    tool_calls_by_ref: dict[str, ToolCall],
) -> list[CanonicalTranscriptItem]:
    items: list[CanonicalTranscriptItem] = []
    text_chunks: list[dict[str, object]] = []
    saw_text_delta = False

    def flush_text() -> None:
        if not text_chunks:
            return
        items.append(assistant_text_item("".join(str(chunk["text"]) for chunk in text_chunks)))
        text_chunks.clear()

    for event in events:
        payload = _event_payload(event)

        if event.event_type == "message.text.delta":
            text = str(payload.get("text") or "")
            if text:
                saw_text_delta = True
                text_chunks.append({"text": text, "stable": False})
            continue

        if event.event_type == "assistant.model_text":
            text = str(payload.get("text") or "")
            if text:
                saw_text_delta = True
                text_chunks.append({"text": text, "stable": True})
            continue

        if event.event_type == "message.text.retracted":
            text = str(payload.get("text") or "")
            remaining = len(text)
            while remaining > 0 and text_chunks:
                current = text_chunks[-1]
                if current.get("stable"):
                    break
                current_text = str(current["text"])
                remove_count = min(len(current_text), remaining)
                current["text"] = current_text[: len(current_text) - remove_count]
                remaining -= remove_count
                if not current["text"]:
                    text_chunks.pop()
            continue

        if event.event_type == "tool_call.started":
            flush_text()
            tool_call_ref = str(event.tool_call_ref or "")
            tool_call = tool_calls_by_ref.get(tool_call_ref)
            tool_name = tool_call.tool_name if tool_call is not None else str(payload.get("tool_name") or "")
            arguments = (
                tool_call.input_for_model_json
                if tool_call is not None and tool_call.input_for_model_json is not None
                else str(payload.get("input_for_model_json") or payload.get("arguments") or "")
            )
            if tool_call_ref or tool_name or arguments:
                items.append(
                    assistant_tool_call_item(
                        tool_call_id=tool_call_ref,
                        tool_name=tool_name,
                        arguments=arguments,
                    )
                )
            continue

        if event.event_type == "tool_call.completed":
            flush_text()
            tool_call_ref = str(event.tool_call_ref or "")
            tool_call = tool_calls_by_ref.get(tool_call_ref)
            tool_name = tool_call.tool_name if tool_call is not None else str(payload.get("tool_name") or "")
            result = (
                tool_call.output_for_model_json
                if tool_call is not None and tool_call.output_for_model_json is not None
                else str(payload.get("output_for_model_json") or "")
            )
            if tool_call_ref or tool_name or result:
                items.append(
                    tool_result_item(
                        tool_call_id=tool_call_ref,
                        tool_name=tool_name,
                        result=result,
                    )
                )

    flush_text()

    if not items and message.content:
        return [assistant_text_item(message.content)]

    if items and not saw_text_delta and message.content:
        items.append(assistant_text_item(message.content))

    return items


async def build_message_history_transcript(
    *,
    session: AsyncSession,
    messages: list[Message],
) -> list[CanonicalTranscriptItem]:
    transcript: list[CanonicalTranscriptItem] = []
    assistant_message_ids = [
        message.id
        for message in messages
        if message.role == MessageRole.ASSISTANT and message.status in {MessageStatus.COMPLETED, MessageStatus.PARTIAL}
    ]
    tool_calls_by_message: dict[int, dict[str, ToolCall]] = defaultdict(dict)
    events_by_message: dict[int, list[RunEvent]] = defaultdict(list)

    if assistant_message_ids:
        tool_call_rows = await session.scalars(
            select(ToolCall)
            .where(ToolCall.assistant_message_id.in_(assistant_message_ids))
            .order_by(ToolCall.assistant_message_id.asc(), ToolCall.sequence_index.asc(), ToolCall.id.asc())
        )
        tool_call_items = tool_call_rows.all()
        if inspect.isawaitable(tool_call_items):
            tool_call_items = await tool_call_items
        for tool_call in tool_call_items:
            if tool_call.assistant_message_id is None:
                continue
            tool_calls_by_message[tool_call.assistant_message_id][tool_call.tool_call_id] = tool_call

        event_rows = await session.scalars(
            select(RunEvent)
            .where(
                RunEvent.assistant_message_id.in_(assistant_message_ids),
                RunEvent.event_type.in_(TRACE_EVENT_TYPES),
            )
            .order_by(RunEvent.assistant_message_id.asc(), RunEvent.sequence.asc(), RunEvent.id.asc())
        )
        event_items = event_rows.all()
        if inspect.isawaitable(event_items):
            event_items = await event_items
        for event in event_items:
            if event.assistant_message_id is None:
                continue
            events_by_message[event.assistant_message_id].append(event)

    for message in messages:
        if message.status not in {MessageStatus.COMPLETED, MessageStatus.PARTIAL}:
            continue

        if message.role == MessageRole.SYSTEM and message.content:
            transcript.append(system_text_item(message.content))
            continue

        if message.role == MessageRole.USER and message.content:
            transcript.append(user_text_item(message.content))
            continue

        if message.role != MessageRole.ASSISTANT:
            continue

        transcript.extend(
            build_assistant_transcript_from_trace(
                message=message,
                events=events_by_message.get(message.id, []),
                tool_calls_by_ref=tool_calls_by_message.get(message.id, {}),
            )
        )

    return transcript


def _event_payload(event: RunEvent) -> dict[str, object]:
    raw = event.payload_json
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
