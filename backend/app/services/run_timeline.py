from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from typing import Any

from app.models.agent_run import AgentRun
from app.models.run_event import RunEvent
from app.models.tool_call import ToolCall
from app.services.agent_trace import json_loads


def project_run_view(
    *,
    run: AgentRun,
    events: list[RunEvent],
    tool_calls: list[ToolCall],
) -> dict[str, Any]:
    tool_calls_by_ref = {
        tool_call.tool_call_id: tool_call
        for tool_call in tool_calls
        if tool_call.tool_call_id
    }
    phase = _phase_from_run(run)
    pending_approval = _pending_approval_from_run(run)
    steps: OrderedDict[str, dict[str, Any]] = OrderedDict()

    for event in sorted(events, key=lambda item: (item.sequence, item.id or 0)):
        payload = json_loads(event.payload_json, default={})
        if not isinstance(payload, dict):
            payload = {}

        if event.event_type == "run.phase.changed":
            next_phase = str(payload.get("phase") or "").strip()
            if next_phase:
                phase = next_phase
            continue

        step_key = _resolve_step_key(event=event)
        if step_key is None:
            continue

        step_entry = _ensure_step_entry(
            steps=steps,
            step_id=step_key,
            event=event,
            payload=payload,
            tool_call=tool_calls_by_ref.get(str(event.tool_call_ref or "")),
        )

        if event.event_type.startswith("tool_call."):
            _apply_tool_event_to_step(
                step=step_entry,
                event=event,
                payload=payload,
                tool_call=tool_calls_by_ref.get(str(event.tool_call_ref or "")),
            )
            continue

        if event.event_type.startswith("commentary."):
            _apply_commentary_event_to_step(step=step_entry, event=event, payload=payload)

    items: list[dict[str, Any]] = []
    for step in steps.values():
        tool_step = {
            "type": "tool_step",
            "step_id": step["step_id"],
            "tool_call_ref": step["tool_call_ref"],
            "tool_name": step["tool_name"],
            "status": step["status"],
            "started_at": step["started_at"],
            "completed_at": step["completed_at"],
            "arguments_preview": step["arguments_preview"],
            "result_preview": step["result_preview"],
        }
        items.append(tool_step)

        commentary_items = step["commentary"]
        commentary_text_present = any(str(item.get("text") or "").strip() for item in commentary_items)
        if commentary_text_present:
            items.append(
                {
                    "type": "processed_group",
                    "group_id": f"pg_{step['step_id']}",
                    "step_id": step["step_id"],
                    "label": _processed_label(step),
                    "status": "completed" if step["status"] not in {"pending", "running", "waiting_approval"} else "active",
                    "created_at": step["commentary_created_at"] or step["completed_at"] or step["started_at"] or step["created_at"],
                    "commentary": commentary_items,
                }
            )

    return {
        "run_id": run.id,
        "assistant_message_id": run.assistant_message_id,
        "status": run.status,
        "phase": phase,
        "last_sequence": run.last_sequence,
        "pending_approval": pending_approval,
        "items": items,
    }


def _phase_from_run(run: AgentRun) -> str | None:
    metadata = json_loads(run.metadata_json, default={})
    if not isinstance(metadata, dict):
        return None
    phase = metadata.get("phase")
    if isinstance(phase, str) and phase.strip():
        return phase.strip()
    return None


def _pending_approval_from_run(run: AgentRun) -> dict[str, Any] | None:
    metadata = json_loads(run.metadata_json, default={})
    if not isinstance(metadata, dict):
        return None
    pending = metadata.get("pending_approval")
    if isinstance(pending, dict):
        return pending
    return None


def _resolve_step_key(*, event: RunEvent) -> str | None:
    if event.step_id:
        return str(event.step_id)
    if event.tool_call_ref:
        return f"legacy_{event.tool_call_ref}"
    return None


def _ensure_step_entry(
    *,
    steps: OrderedDict[str, dict[str, Any]],
    step_id: str,
    event: RunEvent,
    payload: dict[str, Any],
    tool_call: ToolCall | None,
) -> dict[str, Any]:
    existing = steps.get(step_id)
    if existing is not None:
        return existing

    created_at = _dt_to_iso(event.created_at)
    tool_call_ref = str(event.tool_call_ref or "") or (
        tool_call.tool_call_id if tool_call is not None and tool_call.tool_call_id else None
    )
    entry = {
        "step_id": step_id,
        "tool_call_ref": tool_call_ref,
        "tool_name": (
            tool_call.tool_name
            if tool_call is not None and tool_call.tool_name
            else str(payload.get("tool_name") or "")
        ),
        "status": _tool_status_from_event(event.event_type),
        "created_at": created_at,
        "started_at": _dt_to_iso(tool_call.started_at) if tool_call is not None else None,
        "completed_at": _dt_to_iso(tool_call.completed_at) if tool_call is not None else None,
        "arguments_preview": (
            tool_call.display_input_preview
            if tool_call is not None and tool_call.display_input_preview is not None
            else str(payload.get("display_input_preview") or "")
        ),
        "result_preview": (
            tool_call.display_output_preview
            if tool_call is not None and tool_call.display_output_preview is not None
            else str(payload.get("display_output_preview") or "")
        ),
        "commentary_created_at": None,
        "commentary": [],
        "commentary_by_id": {},
    }
    steps[step_id] = entry
    return entry


def _apply_tool_event_to_step(
    *,
    step: dict[str, Any],
    event: RunEvent,
    payload: dict[str, Any],
    tool_call: ToolCall | None,
) -> None:
    if tool_call is not None:
        step["tool_name"] = tool_call.tool_name or step["tool_name"]
        step["arguments_preview"] = tool_call.display_input_preview or step["arguments_preview"]
        step["result_preview"] = tool_call.display_output_preview or step["result_preview"]
        step["started_at"] = _dt_to_iso(tool_call.started_at) or step["started_at"]
        step["completed_at"] = _dt_to_iso(tool_call.completed_at) or step["completed_at"]
        step["status"] = _normalize_tool_status(tool_call.status) or step["status"]

    if payload.get("tool_name"):
        step["tool_name"] = str(payload.get("tool_name") or step["tool_name"])
    if payload.get("display_input_preview") is not None:
        step["arguments_preview"] = str(payload.get("display_input_preview") or "")
    if payload.get("display_output_preview") is not None:
        step["result_preview"] = str(payload.get("display_output_preview") or "")

    status = _tool_status_from_event(event.event_type)
    if status:
        step["status"] = status
    if event.event_type == "tool_call.started" and step["started_at"] is None:
        step["started_at"] = _dt_to_iso(event.created_at)
    if event.event_type in {"tool_call.completed", "tool_call.failed", "tool_call.approval.denied"} and step["completed_at"] is None:
        step["completed_at"] = _dt_to_iso(event.created_at)


def _apply_commentary_event_to_step(
    *,
    step: dict[str, Any],
    event: RunEvent,
    payload: dict[str, Any],
) -> None:
    commentary_id = str(payload.get("commentary_id") or f"cm_{event.sequence}")
    source = str(payload.get("source") or "model")
    style = str(payload.get("style") or "progress")
    text = str(payload.get("text") or "")

    commentary_by_id = step["commentary_by_id"]
    commentary = commentary_by_id.get(commentary_id)
    if commentary is None:
        commentary = {
            "commentary_id": commentary_id,
            "text": "",
            "source": source,
            "style": style,
        }
        commentary_by_id[commentary_id] = commentary
        step["commentary"].append(commentary)
        if step["commentary_created_at"] is None:
            step["commentary_created_at"] = _dt_to_iso(event.created_at)

    if source:
        commentary["source"] = source
    if style:
        commentary["style"] = style

    if event.event_type == "commentary.created":
        commentary["text"] = text
        return

    if event.event_type == "commentary.delta":
        commentary["text"] = f"{commentary.get('text') or ''}{text}"
        return

    if event.event_type == "commentary.completed" and text:
        commentary["text"] = text


def _processed_label(step: dict[str, Any]) -> str:
    timestamp = step["completed_at"] or step["commentary_created_at"] or step["started_at"] or step["created_at"]
    if not timestamp:
        return "Processed"
    try:
        date = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return "Processed"
    return f"Processed at {date.strftime('%H:%M')}"


def _dt_to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _tool_status_from_event(event_type: str) -> str:
    mapping = {
        "tool_call.created": "pending",
        "tool_call.arguments.completed": "ready",
        "tool_call.approval.requested": "waiting_approval",
        "tool_call.approval.granted": "ready",
        "tool_call.approval.denied": "denied",
        "tool_call.started": "running",
        "tool_call.completed": "completed",
        "tool_call.failed": "error",
    }
    return mapping.get(event_type, "")


def _normalize_tool_status(status: str | None) -> str:
    mapping = {
        "pending": "pending",
        "ready": "ready",
        "waiting_approval": "waiting_approval",
        "running": "running",
        "success": "completed",
        "completed": "completed",
        "error": "error",
        "denied": "denied",
    }
    return mapping.get(str(status or ""), "")
