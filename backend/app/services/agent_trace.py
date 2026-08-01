from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime
from typing import Any


PARTS_SCHEMA_VERSION = 1
EVENT_SCHEMA_VERSION = 1
TOOL_CALL_PROJECTION_VERSION = 1

REDACTED = "[REDACTED]"
PREVIEW_MAX_CHARS = 1200

_SENSITIVE_KEY_PATTERN = re.compile(
    r"(api[_-]?key|authorization|cookie|token|secret|password|jwt|ssh|private[_-]?key)",
    re.IGNORECASE,
)
_BEARER_PATTERN = re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE)


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_loads(value: str | None, *, default: Any) -> Any:
    if not value:
        return deepcopy(default)
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return deepcopy(default)


def parts_from_message(parts_json: str | None) -> list[dict[str, Any]]:
    raw = json_loads(parts_json, default=[])
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def aggregate_text_from_parts(parts: list[dict[str, Any]]) -> str:
    text_parts: list[str] = []
    for part in parts:
        if part.get("type") == "text":
            text_parts.append(str(part.get("text") or ""))
    return "".join(text_parts)


def sanitize_tool_input_for_display(value: Any) -> Any:
    return _sanitize_value(value)


def sanitize_tool_output_for_display(value: Any) -> Any:
    return _sanitize_value(value)


def sanitize_tool_output_for_audit(value: Any) -> Any:
    return _sanitize_value(value)


def build_preview(value: Any, max_chars: int = PREVIEW_MAX_CHARS) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, indent=2)
        except TypeError:
            text = str(value)
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def apply_run_event_to_parts(
    parts: list[dict[str, Any]],
    *,
    event_type: str,
    payload: dict[str, Any],
    tool_call_ref: str | None,
) -> list[dict[str, Any]]:
    next_parts = deepcopy(parts)

    if event_type == "message.text.delta":
        text = str(payload.get("text") or "")
        if not text:
            return next_parts
        if next_parts and next_parts[-1].get("type") == "text":
            next_parts[-1]["text"] = str(next_parts[-1].get("text") or "") + text
        else:
            next_parts.append({"type": "text", "text": text})
        return next_parts

    if event_type == "message.text.retracted":
        text = str(payload.get("text") or "")
        if not text:
            return next_parts
        return _retract_text_from_parts(next_parts, text)

    if event_type == "tool_call.created":
        next_parts.append(
            {
                "type": "tool_call",
                "tool_call_id": tool_call_ref,
                "tool_name": str(payload.get("tool_name") or ""),
                "status": "pending",
                "arguments_preview": str(payload.get("display_input_preview") or ""),
            }
        )
        return next_parts

    if event_type == "tool_call.arguments.completed":
        tool_part = _find_tool_part(next_parts, tool_call_ref)
        if tool_part is not None:
            tool_part["arguments_preview"] = str(payload.get("display_input_preview") or "")
        return next_parts

    if event_type == "tool_call.started":
        tool_part = _find_tool_part(next_parts, tool_call_ref)
        if tool_part is not None:
            tool_part["status"] = "running"
        return next_parts

    if event_type == "tool_call.approval.requested":
        tool_part = _find_tool_part(next_parts, tool_call_ref)
        if tool_part is not None:
            tool_part["status"] = "waiting_approval"
        next_parts.append(
            {
                "type": "approval",
                "tool_call_id": tool_call_ref,
                "tool_name": str(payload.get("tool_name") or ""),
                "status": "requested",
                "message": "Waiting for approval",
            }
        )
        return next_parts

    if event_type == "tool_call.approval.granted":
        tool_part = _find_tool_part(next_parts, tool_call_ref)
        if tool_part is not None:
            tool_part["status"] = "ready"
        approval_part = _find_approval_part(next_parts, tool_call_ref)
        if approval_part is not None:
            approval_part["status"] = "granted"
        return next_parts

    if event_type == "tool_call.approval.denied":
        tool_part = _find_tool_part(next_parts, tool_call_ref)
        if tool_part is not None:
            tool_part["status"] = "denied"
        approval_part = _find_approval_part(next_parts, tool_call_ref)
        if approval_part is not None:
            approval_part["status"] = "denied"
            approval_part["message"] = str(payload.get("comment") or payload.get("error_message") or "")
        return next_parts

    if event_type == "tool_call.completed":
        tool_name = str(payload.get("tool_name") or "")
        display_output_preview = str(payload.get("display_output_preview") or "")
        tool_part = _find_tool_part(next_parts, tool_call_ref)
        if tool_part is not None:
            tool_part["status"] = "completed"
        next_parts.append(
            {
                "type": "tool_result",
                "tool_call_id": tool_call_ref,
                "tool_name": tool_name,
                "status": "completed",
                "content": display_output_preview,
            }
        )
        return next_parts

    if event_type == "tool_call.failed":
        tool_name = str(payload.get("tool_name") or "")
        error_message = str(payload.get("error_message") or "")
        tool_part = _find_tool_part(next_parts, tool_call_ref)
        if tool_part is not None:
            tool_part["status"] = "error"
        next_parts.append(
            {
                "type": "error",
                "tool_call_id": tool_call_ref,
                "tool_name": tool_name,
                "message": error_message,
            }
        )
        return next_parts

    if event_type in {"run.failed", "run.cancelled"}:
        error_message = str(payload.get("error_message") or "")
        if error_message:
            next_parts.append({"type": "error", "message": error_message})
        return next_parts

    return next_parts


def utcnow_naive() -> datetime:
    return datetime.utcnow()


def _retract_text_from_parts(parts: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    remaining = len(text)
    next_parts = deepcopy(parts)

    for index in range(len(next_parts) - 1, -1, -1):
        if remaining <= 0:
            break
        part = next_parts[index]
        if part.get("type") != "text":
            continue

        current_text = str(part.get("text") or "")
        if not current_text:
            continue

        remove_count = min(len(current_text), remaining)
        part["text"] = current_text[:-remove_count]
        remaining -= remove_count

    return [part for part in next_parts if part.get("type") != "text" or str(part.get("text") or "")]


def _find_tool_part(parts: list[dict[str, Any]], tool_call_ref: str | None) -> dict[str, Any] | None:
    for part in reversed(parts):
        if part.get("type") == "tool_call" and part.get("tool_call_id") == tool_call_ref:
            return part
    return None


def _find_approval_part(parts: list[dict[str, Any]], tool_call_ref: str | None) -> dict[str, Any] | None:
    for part in reversed(parts):
        if part.get("type") == "approval" and part.get("tool_call_id") == tool_call_ref:
            return part
    return None


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if _SENSITIVE_KEY_PATTERN.search(str(key)):
                sanitized[str(key)] = REDACTED
            else:
                sanitized[str(key)] = _sanitize_value(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, str):
        masked = _BEARER_PATTERN.sub("Bearer [REDACTED]", value)
        return masked
    return value
