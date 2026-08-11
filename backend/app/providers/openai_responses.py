from __future__ import annotations

import json
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx

from app.canonical_transcript import CanonicalTranscriptItem
from app.core.encryption import decrypt_text
from app.core.exceptions import AppError
from app.models.api_key import ApiKey
from app.providers.openai import (
    DEFAULT_MAX_TOOL_ROUND_TRIPS,
    ReplyText,
    ToolCallLoopGuard,
    ToolEventCallback,
    ToolExecutor,
    UsageCallback,
    _build_headers,
    _extract_error_message,
    _http_error_message,
    _resolve_base_url,
)


def _coerce_token_count(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _extract_usage(data: dict[str, Any]) -> dict[str, int] | None:
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return None

    prompt_tokens = _coerce_token_count(usage.get("input_tokens"))
    completion_tokens = _coerce_token_count(usage.get("output_tokens"))
    total_tokens = _coerce_token_count(usage.get("total_tokens"))
    if prompt_tokens is None and completion_tokens is None and total_tokens is None:
        return None
    return {
        "prompt_tokens": prompt_tokens or 0,
        "completion_tokens": completion_tokens or 0,
        "total_tokens": total_tokens or 0,
    }


def _merge_usage(current: dict[str, int] | None, incoming: dict[str, int] | None) -> dict[str, int] | None:
    if incoming is None:
        return current
    if current is None:
        return dict(incoming)
    return {
        "prompt_tokens": current.get("prompt_tokens", 0) + incoming.get("prompt_tokens", 0),
        "completion_tokens": current.get("completion_tokens", 0) + incoming.get("completion_tokens", 0),
        "total_tokens": current.get("total_tokens", 0) + incoming.get("total_tokens", 0),
    }


def _supports_reasoning_summary(model: str) -> bool:
    normalized = model.strip().lower()
    return normalized.startswith(("gpt-5", "gpt-oss", "o1", "o3", "o4"))


def _responses_tools(tools: list[dict[str, object]] | None) -> list[dict[str, object]] | None:
    if not tools:
        return None

    converted: list[dict[str, object]] = []
    for tool in tools:
        function = tool.get("function") if isinstance(tool, dict) else None
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        parameters = function.get("parameters")
        if not isinstance(name, str) or not name.strip() or not isinstance(parameters, dict):
            continue
        item: dict[str, object] = {"type": "function", "name": name, "parameters": parameters}
        description = function.get("description")
        if isinstance(description, str) and description.strip():
            item["description"] = description
        converted.append(item)
    return converted or None


def _transcript_to_responses_input(transcript: list[CanonicalTranscriptItem]) -> list[dict[str, object]]:
    input_items: list[dict[str, object]] = []
    assistant_text = ""

    def flush_assistant() -> None:
        nonlocal assistant_text
        if assistant_text:
            input_items.append({"role": "assistant", "content": assistant_text})
            assistant_text = ""

    for item in transcript:
        if item.kind == "system_text":
            flush_assistant()
            input_items.append({"role": "developer", "content": item.text})
        elif item.kind == "user_text":
            flush_assistant()
            input_items.append({"role": "user", "content": item.text})
        elif item.kind == "assistant_text":
            assistant_text += item.text
        elif item.kind == "assistant_tool_call":
            flush_assistant()
            input_items.append(
                {
                    "type": "function_call",
                    "call_id": item.tool_call_id,
                    "name": item.tool_name,
                    "arguments": item.arguments,
                }
            )
        elif item.kind == "tool_result":
            flush_assistant()
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": item.tool_call_id,
                    "output": item.result,
                }
            )
    flush_assistant()
    return input_items


def _responses_payload(
    *,
    model: str,
    input_items: list[dict[str, object]],
    temperature: Decimal | None,
    max_tokens: int | None,
    stream: bool,
    tools: list[dict[str, object]] | None,
) -> dict[str, object]:
    payload: dict[str, object] = {"model": model, "input": input_items, "stream": stream}
    if max_tokens is not None:
        payload["max_output_tokens"] = max_tokens
    if temperature is not None and not _supports_reasoning_summary(model):
        payload["temperature"] = float(temperature)
    response_tools = _responses_tools(tools)
    if response_tools:
        payload["tools"] = response_tools
        payload["tool_choice"] = "auto"
    if _supports_reasoning_summary(model):
        payload["reasoning"] = {"summary": "auto"}
    return payload


def _response_output(data: dict[str, Any]) -> list[dict[str, Any]]:
    output = data.get("output")
    return [item for item in output if isinstance(item, dict)] if isinstance(output, list) else []


def _output_text(output: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in output:
        if item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "output_text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return "".join(parts)


def _function_calls(output: list[dict[str, Any]]) -> list[dict[str, str]]:
    calls: list[dict[str, str]] = []
    for item in output:
        if item.get("type") != "function_call":
            continue
        call_id = item.get("call_id")
        name = item.get("name")
        arguments = item.get("arguments")
        if isinstance(call_id, str) and call_id and isinstance(name, str) and name and isinstance(arguments, str):
            calls.append({"call_id": call_id, "name": name, "arguments": arguments})
    return calls


async def _execute_tool_calls(
    *,
    calls: list[dict[str, str]],
    tool_executor: ToolExecutor,
    event_callback: ToolEventCallback | None = None,
    loop_guard: ToolCallLoopGuard | None = None,
) -> list[dict[str, object]]:
    outputs: list[dict[str, object]] = []
    for call in calls:
        if loop_guard is not None:
            loop_guard.observe(call["name"], call["arguments"])
        if event_callback is not None:
            await event_callback({"name": call["name"], "status": "running", "arguments": call["arguments"]})
        result = await tool_executor(call["name"], call["arguments"])
        if event_callback is not None:
            await event_callback({"name": call["name"], "status": "completed", "content": result})
        outputs.append({"type": "function_call_output", "call_id": call["call_id"], "output": result})
    return outputs


async def create_openai_responses_reply(
    *,
    api_key: ApiKey,
    model: str,
    transcript: list[CanonicalTranscriptItem],
    temperature: Decimal | None,
    max_tokens: int | None,
    tools: list[dict[str, object]] | None = None,
    tool_executor: ToolExecutor | None = None,
    max_tool_round_trips: int = DEFAULT_MAX_TOOL_ROUND_TRIPS,
) -> ReplyText:
    if tools and tool_executor is None:
        raise AppError(status_code=500, code="CONFIG_ERROR", message="Tool execution requires a tool_executor")

    raw_key = decrypt_text(api_key.key_encrypted)
    url = f"{_resolve_base_url(api_key.base_url)}/responses"
    input_items = _transcript_to_responses_input(transcript)
    total_usage: dict[str, int] | None = None
    loop_guard = ToolCallLoopGuard()
    max_rounds = max_tool_round_trips if tools else 1

    async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=15.0), follow_redirects=True, trust_env=False, http2=False) as client:
        for _ in range(max_rounds):
            payload = _responses_payload(
                model=model,
                input_items=input_items,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=False,
                tools=tools,
            )
            try:
                response = await client.post(url, headers=_build_headers(raw_key), json=payload)
            except httpx.HTTPError as exc:
                raise AppError(status_code=502, code="MODEL_ERROR", message=_http_error_message("OpenAI request failed", exc)) from exc
            if response.status_code >= 400:
                raise AppError(status_code=502, code="MODEL_ERROR", message=_extract_error_message(response))

            try:
                data = response.json()
            except Exception as exc:
                raise AppError(status_code=502, code="MODEL_ERROR", message="OpenAI Responses response format is invalid") from exc
            if not isinstance(data, dict):
                raise AppError(status_code=502, code="MODEL_ERROR", message="OpenAI Responses response format is invalid")

            total_usage = _merge_usage(total_usage, _extract_usage(data))
            output = _response_output(data)
            calls = _function_calls(output)
            if calls:
                assert tool_executor is not None
                input_items = output + await _execute_tool_calls(calls=calls, tool_executor=tool_executor, loop_guard=loop_guard)
                continue

            content = _output_text(output)
            if content:
                return ReplyText(content, total_usage)
            raise AppError(status_code=502, code="MODEL_ERROR", message="OpenAI Responses returned no content")

    raise AppError(status_code=502, code="MODEL_ERROR", message="OpenAI Responses exceeded the tool-call limit")


async def _stream_response_round(
    client: httpx.AsyncClient,
    *,
    url: str,
    api_key: str,
    payload: dict[str, object],
    round_index: int,
) -> AsyncIterator[dict[str, object]]:
    output_items: list[dict[str, Any]] = []
    output_by_id: dict[str, dict[str, Any]] = {}
    thinking_ids: dict[str, str] = {}
    active_thinking: set[str] = set()
    usage: dict[str, int] | None = None
    content = ""

    def thinking_id(data: dict[str, Any], item: dict[str, Any] | None = None) -> str:
        ref = str(data.get("item_id") or (item or {}).get("id") or data.get("output_index") or len(thinking_ids))
        return thinking_ids.setdefault(ref, f"thinking-{round_index}-{ref}")

    try:
        async with client.stream("POST", url, headers=_build_headers(api_key), json=payload) as response:
            if response.status_code >= 400:
                body = await response.aread()
                fallback = httpx.Response(status_code=response.status_code, headers=response.headers, content=body, request=response.request)
                raise AppError(status_code=502, code="MODEL_ERROR", message=_extract_error_message(fallback))

            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                try:
                    data = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict):
                    continue
                event_type = str(data.get("type") or "")
                if event_type == "response.output_text.delta":
                    delta = data.get("delta")
                    if isinstance(delta, str) and delta:
                        content += delta
                        yield {"type": "content", "content": delta}
                elif event_type == "response.output_item.added":
                    item = data.get("item")
                    if isinstance(item, dict) and item.get("type") == "reasoning":
                        identifier = thinking_id(data, item)
                        active_thinking.add(identifier)
                        yield {"type": "thinking_started", "thinking_id": identifier, "text": ""}
                elif event_type == "response.reasoning_summary_text.delta":
                    delta = data.get("delta")
                    if isinstance(delta, str) and delta:
                        identifier = thinking_id(data)
                        if identifier not in active_thinking:
                            active_thinking.add(identifier)
                            yield {"type": "thinking_started", "thinking_id": identifier, "text": ""}
                        yield {"type": "thinking_delta", "thinking_id": identifier, "text": delta}
                elif event_type == "response.output_item.done":
                    item = data.get("item")
                    if isinstance(item, dict):
                        item_id = item.get("id")
                        if isinstance(item_id, str) and item_id:
                            output_by_id[item_id] = item
                        else:
                            output_items.append(item)
                        if item.get("type") == "reasoning":
                            identifier = thinking_id(data, item)
                            if identifier in active_thinking:
                                active_thinking.remove(identifier)
                                yield {"type": "thinking_completed", "thinking_id": identifier}
                elif event_type == "response.completed":
                    completed = data.get("response")
                    if isinstance(completed, dict):
                        usage = _extract_usage(completed)
                        completed_output = _response_output(completed)
                        if completed_output:
                            output_items = completed_output
                elif event_type in {"response.failed", "response.incomplete", "error"}:
                    error = data.get("error")
                    message = error.get("message") if isinstance(error, dict) else None
                    raise AppError(status_code=502, code="MODEL_ERROR", message=str(message or "OpenAI Responses returned an error"))
    except AppError:
        raise
    except httpx.HTTPError as exc:
        raise AppError(status_code=502, code="MODEL_ERROR", message=_http_error_message("OpenAI request failed", exc)) from exc

    for identifier in active_thinking:
        yield {"type": "thinking_completed", "thinking_id": identifier}
    if not output_items:
        output_items = list(output_by_id.values())
    yield {"type": "done", "content": content, "output": output_items, "usage": usage}


async def stream_openai_responses_reply(
    *,
    api_key: ApiKey,
    model: str,
    transcript: list[CanonicalTranscriptItem],
    temperature: Decimal | None,
    max_tokens: int | None,
    tools: list[dict[str, object]] | None = None,
    tool_executor: ToolExecutor | None = None,
    max_tool_round_trips: int = DEFAULT_MAX_TOOL_ROUND_TRIPS,
    tool_event_callback: ToolEventCallback | None = None,
    usage_callback: UsageCallback | None = None,
) -> AsyncIterator[dict[str, object]]:
    if tools and tool_executor is None:
        raise AppError(status_code=500, code="CONFIG_ERROR", message="Tool execution requires a tool_executor")

    raw_key = decrypt_text(api_key.key_encrypted)
    url = f"{_resolve_base_url(api_key.base_url)}/responses"
    input_items = _transcript_to_responses_input(transcript)
    total_usage: dict[str, int] | None = None
    loop_guard = ToolCallLoopGuard()
    max_rounds = max_tool_round_trips if tools else 1

    async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=15.0), follow_redirects=True, trust_env=False, http2=False) as client:
        for round_index in range(max_rounds):
            payload = _responses_payload(
                model=model,
                input_items=input_items,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                tools=tools,
            )
            response_output: list[dict[str, Any]] = []
            async for event in _stream_response_round(client, url=url, api_key=raw_key, payload=payload, round_index=round_index):
                event_type = str(event.get("type") or "")
                if event_type == "done":
                    output = event.get("output")
                    response_output = output if isinstance(output, list) else []
                    total_usage = _merge_usage(total_usage, event.get("usage") if isinstance(event.get("usage"), dict) else None)
                    continue
                yield event

            calls = _function_calls(response_output)
            if calls:
                assert tool_executor is not None
                input_items = response_output + await _execute_tool_calls(
                    calls=calls,
                    tool_executor=tool_executor,
                    event_callback=tool_event_callback,
                    loop_guard=loop_guard,
                )
                continue

            if usage_callback is not None:
                await usage_callback(total_usage)
            return

    raise AppError(status_code=502, code="MODEL_ERROR", message="OpenAI Responses exceeded the tool-call limit")
