from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
import json
import re
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.agent_run import AgentRun
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole, MessageStatus
from app.models.run_event import RunEvent
from app.models.tool_call import ToolCall
from app.services.agent_artifacts import read_artifact_text
from app.services.agent_trace import json_loads
from app.services.conversations import get_conversation


class ExportFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"


class ExportScope(str, Enum):
    CURRENT_BRANCH = "current_branch"
    ALL_BRANCHES = "all_branches"


EXPORT_CAPABILITIES: dict[ExportFormat, set[ExportScope]] = {
    ExportFormat.MARKDOWN: {ExportScope.CURRENT_BRANCH},
    ExportFormat.JSON: {ExportScope.CURRENT_BRANCH, ExportScope.ALL_BRANCHES},
}


@dataclass(slots=True)
class ExportedFile:
    content: str
    media_type: str
    filename: str


async def export_conversation(
    *,
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    export_format: ExportFormat,
    scope: ExportScope,
) -> ExportedFile:
    _validate_export_capability(export_format=export_format, scope=scope)

    conversation = await get_conversation(session=session, user_id=user_id, conversation_id=conversation_id)
    messages = await _load_all_messages(session=session, conversation_id=conversation.id)
    exported_at = datetime.now().astimezone()
    selected_messages, warnings = _select_messages_for_scope(
        messages=messages,
        scope=scope,
        current_leaf_message_id=conversation.current_leaf_message_id,
    )
    trace_bundle = await _load_trace_bundle(
        session=session,
        conversation_id=conversation.id,
        message_ids={message.id for message in selected_messages},
    )

    if export_format == ExportFormat.MARKDOWN:
        return ExportedFile(
            content=_render_markdown_export(
                conversation=conversation,
                messages=selected_messages,
                exported_at=exported_at,
                scope=scope,
            ),
            media_type="text/markdown",
            filename=_build_export_filename(
                title=conversation.title,
                conversation_id=conversation.id,
                scope=scope,
                export_format=export_format,
                exported_at=exported_at,
            ),
        )

    return ExportedFile(
        content=_render_json_export(
            conversation=conversation,
            messages=selected_messages,
            exported_at=exported_at,
            scope=scope,
            trace_bundle=trace_bundle,
            warnings=warnings,
        ),
        media_type="application/json",
        filename=_build_export_filename(
            title=conversation.title,
            conversation_id=conversation.id,
            scope=scope,
            export_format=export_format,
            exported_at=exported_at,
        ),
    )


def build_content_disposition(filename: str) -> str:
    fallback = re.sub(r"[^\x20-\x7E]", "", filename).strip() or "conversation-export"
    fallback = fallback.replace('"', "")
    encoded = quote(filename)
    return f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{encoded}'


async def _load_all_messages(*, session: AsyncSession, conversation_id: int) -> list[Message]:
    result = await session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc(), Message.id.asc())
    )
    return list(result.all())


def _validate_export_capability(*, export_format: ExportFormat, scope: ExportScope) -> None:
    supported_scopes = EXPORT_CAPABILITIES.get(export_format, set())
    if scope not in supported_scopes:
        raise AppError(
            status_code=422,
            code="UNSUPPORTED_EXPORT_COMBINATION",
            message=f"不支持导出 format={export_format.value}, scope={scope.value} 的组合",
        )


def _select_messages_for_scope(
    *,
    messages: list[Message],
    scope: ExportScope,
    current_leaf_message_id: int | None,
) -> tuple[list[Message], list[str]]:
    if scope == ExportScope.ALL_BRANCHES:
        warnings: list[str] = []
        if current_leaf_message_id is not None and all(message.id != current_leaf_message_id for message in messages):
            warnings.append("current_leaf_message_id does not reference an existing message")
        return messages, warnings

    if not messages:
        return [], []

    if current_leaf_message_id is None:
        raise AppError(status_code=409, code="INVALID_BRANCH_STATE", message="当前分支不存在，无法导出当前分支")

    return _lineage_messages(messages=messages, leaf_id=current_leaf_message_id), []


def _lineage_messages(*, messages: list[Message], leaf_id: int) -> list[Message]:
    by_id = {message.id: message for message in messages}
    if leaf_id not in by_id:
        raise AppError(status_code=409, code="INVALID_BRANCH_STATE", message="当前分支节点不存在，无法导出当前分支")

    lineage: list[Message] = []
    visited: set[int] = set()
    cursor: int | None = leaf_id

    while cursor is not None:
        if cursor in visited:
            raise AppError(status_code=409, code="INVALID_BRANCH_STATE", message="检测到循环引用，无法导出当前分支")
        visited.add(cursor)

        message = by_id.get(cursor)
        if message is None:
            raise AppError(status_code=409, code="INVALID_BRANCH_STATE", message="当前分支祖先链不完整，无法导出当前分支")

        lineage.append(message)
        cursor = message.parent_id

    lineage.reverse()
    return lineage


def _render_markdown_export(
    *,
    conversation: Conversation,
    messages: list[Message],
    exported_at: datetime,
    scope: ExportScope,
) -> str:
    lines = [
        f"# {(conversation.title or f'conversation-{conversation.id}').strip()}",
        "",
        "> Exported from ai-chat",
        "> Format: markdown",
        f"> Scope: {scope.value}",
        f"> Exported at: {exported_at.isoformat()}",
        "",
    ]

    if conversation.system_prompt and conversation.system_prompt.strip():
        lines.extend([
            "## System",
            "",
            conversation.system_prompt.strip(),
            "",
        ])

    for message in messages:
        block = _markdown_message_block(message)
        if block is None:
            continue
        lines.extend(block)

    return "\n".join(lines).rstrip() + "\n"


def _markdown_message_block(message: Message) -> list[str] | None:
    if message.status == MessageStatus.STREAMING:
        return None

    content = message.content.strip()
    if message.status == MessageStatus.FAILED and not content:
        return None
    if not content:
        return None

    role_heading = {
        MessageRole.SYSTEM: "System",
        MessageRole.USER: "User",
        MessageRole.ASSISTANT: "Assistant",
    }[message.role]

    lines = [f"## {role_heading}", ""]

    if message.status == MessageStatus.PARTIAL:
        lines.extend(["> Status: partial", ""])
    elif message.status == MessageStatus.FAILED:
        lines.append("> Status: failed")
        if message.error_message and message.error_message.strip():
            lines.extend(_quote_markdown_lines("Error", message.error_message.strip()))
        lines.append("")

    lines.extend([content, ""])
    return lines


def _quote_markdown_lines(label: str, text: str) -> list[str]:
    parts = text.splitlines() or [text]
    quoted = [f"> {label}: {parts[0]}"]
    quoted.extend(f"> {part}" for part in parts[1:])
    return quoted


def _render_json_export(
    *,
    conversation: Conversation,
    messages: list[Message],
    exported_at: datetime,
    scope: ExportScope,
    trace_bundle: dict[str, object],
    warnings: list[str],
) -> str:
    payload: dict[str, object] = {
        "schema_version": 2,
        "type": "ai-chat.conversation_export",
        "format": "json",
        "scope": scope.value,
        "exported_at": exported_at.isoformat(),
        "conversation": {
            "id": conversation.id,
            "title": conversation.title,
            "system_prompt": conversation.system_prompt,
            "provider": conversation.provider,
            "model": conversation.model,
            "temperature": _decimal_to_string(conversation.temperature),
            "max_tokens": conversation.max_tokens,
            "current_leaf_message_id": conversation.current_leaf_message_id,
            "created_at": _datetime_to_iso(conversation.created_at),
            "updated_at": _datetime_to_iso(conversation.updated_at),
        },
        "messages": [
            {
                "id": message.id,
                "conversation_id": message.conversation_id,
                "parent_id": message.parent_id,
                "role": message.role.value,
                "content": message.content,
                "provider": message.provider,
                "model": message.model,
                "temperature": _decimal_to_string(message.temperature),
                "max_tokens": message.max_tokens,
                "status": message.status.value,
                "error_message": message.error_message,
                "created_at": _datetime_to_iso(message.created_at),
                "updated_at": _datetime_to_iso(message.updated_at),
            }
            for message in messages
        ],
        "agent_runs": trace_bundle["agent_runs"],
        "tool_calls": trace_bundle["tool_calls"],
        "run_events": trace_bundle["run_events"],
    }

    if warnings:
        payload["warnings"] = warnings

    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _build_export_filename(
    *,
    title: str,
    conversation_id: int,
    scope: ExportScope,
    export_format: ExportFormat,
    exported_at: datetime,
) -> str:
    base_title = _sanitize_filename_component(title) or f"conversation-{conversation_id}"
    scope_part = "current-branch" if scope == ExportScope.CURRENT_BRANCH else "all-branches"
    timestamp = exported_at.strftime("%Y%m%d-%H%M%S")
    extension = "md" if export_format == ExportFormat.MARKDOWN else "json"
    return f"{base_title}-{scope_part}-{timestamp}.{extension}"


def _sanitize_filename_component(value: str | None) -> str:
    candidate = (value or "").strip()
    candidate = re.sub(r'[\\/:*?"<>|]+', " ", candidate)
    candidate = re.sub(r"\s+", " ", candidate).strip(" .")
    if not candidate:
        return ""
    return candidate[:80].rstrip(" .")


def _decimal_to_string(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _datetime_to_iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


async def _load_trace_bundle(
    *,
    session: AsyncSession,
    conversation_id: int,
    message_ids: set[int],
) -> dict[str, object]:
    if not message_ids:
        return {"agent_runs": [], "tool_calls": [], "run_events": []}

    run_rows = await session.scalars(
        select(AgentRun)
        .where(
            AgentRun.conversation_id == conversation_id,
            AgentRun.assistant_message_id.in_(message_ids),
        )
        .order_by(AgentRun.id.asc())
    )
    runs = list(run_rows.all())
    run_ids = [run.id for run in runs]
    if not run_ids:
        return {"agent_runs": [], "tool_calls": [], "run_events": []}

    tool_call_rows = await session.scalars(
        select(ToolCall)
        .where(ToolCall.run_id.in_(run_ids))
        .order_by(ToolCall.run_id.asc(), ToolCall.sequence_index.asc(), ToolCall.id.asc())
    )
    tool_calls = list(tool_call_rows.all())

    event_rows = await session.scalars(
        select(RunEvent)
        .where(RunEvent.run_id.in_(run_ids))
        .order_by(RunEvent.run_id.asc(), RunEvent.sequence.asc(), RunEvent.id.asc())
    )
    events = list(event_rows.all())

    return {
        "agent_runs": [_serialize_agent_run(run) for run in runs],
        "tool_calls": [_serialize_tool_call(tool_call) for tool_call in tool_calls],
        "run_events": [_serialize_run_event(event) for event in events],
    }


def _serialize_agent_run(run: AgentRun) -> dict[str, object]:
    metadata = json_loads(run.metadata_json, default={})
    if not isinstance(metadata, dict):
        metadata = {}
    return {
        "id": run.id,
        "conversation_id": run.conversation_id,
        "user_message_id": run.user_message_id,
        "assistant_message_id": run.assistant_message_id,
        "provider": run.provider,
        "model": run.model,
        "status": run.status,
        "started_at": _datetime_to_iso(run.started_at),
        "completed_at": _datetime_to_iso(run.completed_at),
        "last_sequence": run.last_sequence,
        "resume_token": run.resume_token,
        "error_message": run.error_message,
        "metadata": metadata,
        "created_at": _datetime_to_iso(run.created_at),
        "updated_at": _datetime_to_iso(run.updated_at),
    }


def _serialize_tool_call(tool_call: ToolCall) -> dict[str, object]:
    return {
        "id": tool_call.id,
        "run_id": tool_call.run_id,
        "conversation_id": tool_call.conversation_id,
        "assistant_message_id": tool_call.assistant_message_id,
        "tool_call_id": tool_call.tool_call_id,
        "provider_tool_call_id": tool_call.provider_tool_call_id,
        "tool_name": tool_call.tool_name,
        "sequence_index": tool_call.sequence_index,
        "status": tool_call.status,
        "input_for_model_json": tool_call.input_for_model_json,
        "display_input_preview": tool_call.display_input_preview,
        "output_for_model_json": tool_call.output_for_model_json,
        "display_output_preview": tool_call.display_output_preview,
        "audit_output_preview": tool_call.audit_output_preview,
        "output_blob_ref": tool_call.output_blob_ref,
        "output_blob_content": read_artifact_text(tool_call.output_blob_ref or ""),
        "output_size_bytes": tool_call.output_size_bytes,
        "error_message": tool_call.error_message,
        "started_at": _datetime_to_iso(tool_call.started_at),
        "completed_at": _datetime_to_iso(tool_call.completed_at),
        "duration_ms": tool_call.duration_ms,
        "created_at": _datetime_to_iso(tool_call.created_at),
        "updated_at": _datetime_to_iso(tool_call.updated_at),
    }


def _serialize_run_event(event: RunEvent) -> dict[str, object]:
    payload = json_loads(event.payload_json, default={})
    if not isinstance(payload, dict):
        payload = {}
    return {
        "id": event.id,
        "event_id": event.event_id,
        "run_id": event.run_id,
        "assistant_message_id": event.assistant_message_id,
        "step_id": event.step_id,
        "tool_call_ref": event.tool_call_ref,
        "sequence": event.sequence,
        "event_type": event.event_type,
        "payload": payload,
        "schema_version": event.schema_version,
        "created_at": _datetime_to_iso(event.created_at),
    }
