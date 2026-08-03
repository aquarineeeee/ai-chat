from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from decimal import Decimal
from typing import Any

import httpx

from app.canonical_transcript import CanonicalTranscriptItem, parse_tool_arguments
from app.core.encryption import decrypt_text
from app.core.exceptions import AppError
from app.models.api_key import ApiKey


DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_ANTHROPIC_MAX_TOKENS = 2000
DEFAULT_CACHE_CONTROL = {"type": "ephemeral"}
DEFAULT_MAX_TOOL_ROUND_TRIPS = 99
ToolExecutor = Callable[[str, str], Awaitable[str]]
ToolEventCallback = Callable[[dict[str, object]], Awaitable[None]]
UsageCallback = Callable[[dict[str, int] | None], Awaitable[None]]


class ReplyText(str):
    usage: dict[str, int] | None

    def __new__(cls, content: str, usage: dict[str, int] | None = None) -> "ReplyText":
        instance = super().__new__(cls, content)
        instance.usage = usage
        return instance


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

    input_tokens = _coerce_token_count(usage.get("input_tokens"))
    cache_creation_tokens = _coerce_token_count(usage.get("cache_creation_input_tokens"))
    cache_read_tokens = _coerce_token_count(usage.get("cache_read_input_tokens"))
    output_tokens = _coerce_token_count(usage.get("output_tokens"))
    if all(value is None for value in (input_tokens, cache_creation_tokens, cache_read_tokens, output_tokens)):
        return None

    prompt_tokens = sum(
        value or 0
        for value in (input_tokens, cache_creation_tokens, cache_read_tokens)
    )
    completion_tokens = output_tokens or 0
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def _merge_usage(
    current: dict[str, int] | None,
    incoming: dict[str, int] | None,
) -> dict[str, int] | None:
    if incoming is None:
        return current
    if current is None:
        return dict(incoming)
    return {
        "prompt_tokens": current.get("prompt_tokens", 0) + incoming.get("prompt_tokens", 0),
        "completion_tokens": current.get("completion_tokens", 0) + incoming.get("completion_tokens", 0),
        "total_tokens": current.get("total_tokens", 0) + incoming.get("total_tokens", 0),
    }


def normalize_anthropic_base_url(base_url: str | None) -> str | None:
    if base_url is None:
        return None
    normalized = base_url.strip().rstrip("/")
    return normalized or None


def _resolve_base_url(base_url: str | None) -> str:
    return normalize_anthropic_base_url(base_url) or DEFAULT_ANTHROPIC_BASE_URL


def _build_headers(api_key: str) -> dict[str, str]:
    return {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "Content-Type": "application/json",
        "Accept-Encoding": "identity",
    }


def _http_error_message(prefix: str, exc: httpx.HTTPError) -> str:
    detail = str(exc).strip()
    if not detail:
        detail = repr(exc)
    return f"{prefix}: {type(exc).__name__}: {detail}"


def _extract_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        return response.text or f"上游模型服务返回 HTTP {response.status_code}"

    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if message:
            return str(message)
    if isinstance(error, str):
        return error
    return response.text or f"上游模型服务返回 HTTP {response.status_code}"


def _stringify_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return ""


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _normalize_message_content(content: object) -> str | list[dict[str, object]] | None:
    if isinstance(content, list):
        normalized_blocks: list[dict[str, object]] = []
        for item in content:
            if not isinstance(item, dict):
                continue

            block_type = item.get("type")
            if block_type == "text":
                text = str(item.get("text") or "")
                if text:
                    block: dict[str, object] = {"type": "text", "text": text}
                    cache_control = item.get("cache_control")
                    if isinstance(cache_control, dict):
                        block["cache_control"] = _json_copy(cache_control)
                    normalized_blocks.append(block)
                continue

            if block_type == "thinking":
                thinking = str(item.get("thinking") or "")
                signature = str(item.get("signature") or "")
                normalized_blocks.append(
                    {
                        "type": "thinking",
                        "thinking": thinking,
                        "signature": signature,
                    }
                )
                continue

            if block_type == "redacted_thinking":
                data = str(item.get("data") or "")
                if data:
                    normalized_blocks.append({"type": "redacted_thinking", "data": data})
                continue

            if block_type == "tool_use":
                tool_use_id = item.get("id")
                name = item.get("name")
                tool_input = item.get("input")
                if isinstance(tool_use_id, str) and tool_use_id and isinstance(name, str) and name:
                    normalized_blocks.append(
                        {
                            "type": "tool_use",
                            "id": tool_use_id,
                            "name": name,
                            "input": _json_copy(tool_input) if isinstance(tool_input, dict) else {},
                        }
                    )
                continue

            if block_type == "tool_result":
                tool_use_id = item.get("tool_use_id")
                if not isinstance(tool_use_id, str) or not tool_use_id:
                    continue
                tool_result: dict[str, object] = {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": _stringify_content(item.get("content", "")),
                }
                if item.get("is_error") is True:
                    tool_result["is_error"] = True
                normalized_blocks.append(tool_result)

        return normalized_blocks or None

    text = _stringify_content(content).strip()
    return text or None


def _anthropic_system_blocks(system_text: str) -> list[dict[str, object]]:
    if not system_text:
        return []
    return [
        {
            "type": "text",
            "text": system_text,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _convert_messages(messages: list[dict[str, object]]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    system_parts: list[str] = []
    anthropic_messages: list[dict[str, object]] = []

    for message in messages:
        role = message.get("role")
        if role == "system":
            content = _stringify_content(message.get("content", "")).strip()
            if not content:
                continue
            system_parts.append(content)
            continue
        if role not in {"user", "assistant"}:
            continue

        content = _normalize_message_content(message.get("content", ""))
        if content is None:
            continue
        anthropic_messages.append({"role": role, "content": content})

    return _anthropic_system_blocks("\n\n".join(system_parts)), anthropic_messages


def _transcript_to_anthropic_history(transcript: list[CanonicalTranscriptItem]) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = []
    current_role: str | None = None
    current_content: str | list[dict[str, object]] | None = None

    def ensure_role(role: str) -> list[dict[str, object]]:
        nonlocal current_role, current_content
        if current_role != role or not isinstance(current_content, list):
            flush_message()
            current_role = role
            current_content = []
        assert isinstance(current_content, list)
        return current_content

    def flush_message() -> None:
        nonlocal current_role, current_content
        if current_role is None or current_content is None:
            current_role = None
            current_content = None
            return
        messages.append({"role": current_role, "content": _json_copy(current_content)})
        current_role = None
        current_content = None

    for item in transcript:
        if item.kind == "system_text":
            flush_message()
            messages.append({"role": "system", "content": item.text})
            continue

        if item.kind == "user_text":
            blocks = ensure_role("user")
            blocks.append({"type": "text", "text": item.text})
            continue

        if item.kind == "assistant_text":
            blocks = ensure_role("assistant")
            blocks.append({"type": "text", "text": item.text})
            continue

        if item.kind == "assistant_tool_call":
            blocks = ensure_role("assistant")
            blocks.append(
                {
                    "type": "tool_use",
                    "id": item.tool_call_id or f"toolu-{len(blocks)}",
                    "name": item.tool_name,
                    "input": parse_tool_arguments(item.arguments),
                }
            )
            continue

        if item.kind == "tool_result":
            flush_message()
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": item.tool_call_id,
                            "content": item.result,
                        }
                    ],
                }
            )

    flush_message()
    return messages


def _anthropic_tools(tools: list[dict[str, object]] | None) -> list[dict[str, object]] | None:
    if not tools:
        return None

    anthropic_tools: list[dict[str, object]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue

        name = tool.get("name")
        description = tool.get("description")
        input_schema = tool.get("input_schema")
        if isinstance(name, str) and name and isinstance(input_schema, dict):
            anthropic_tool: dict[str, object] = {
                "name": name,
                "input_schema": _json_copy(input_schema),
            }
            if isinstance(description, str) and description.strip():
                anthropic_tool["description"] = description.strip()
            anthropic_tools.append(anthropic_tool)
            continue

        function = tool.get("function")
        if not isinstance(function, dict):
            continue

        name = function.get("name")
        parameters = function.get("parameters")
        description = function.get("description")
        if not isinstance(name, str) or not name or not isinstance(parameters, dict):
            continue

        anthropic_tool = {
            "name": name,
            "input_schema": _json_copy(parameters),
        }
        if isinstance(description, str) and description.strip():
            anthropic_tool["description"] = description.strip()
        anthropic_tools.append(anthropic_tool)

    return anthropic_tools or None


def _messages_payload(
    *,
    model: str,
    messages: list[dict[str, object]],
    temperature: Decimal | None,
    max_tokens: int | None,
    stream: bool,
    tools: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    system, anthropic_messages = _convert_messages(messages)
    if not anthropic_messages:
        raise AppError(status_code=422, code="VALIDATION_ERROR", message="Anthropic 请求至少需要一条 user/assistant 消息")

    payload: dict[str, object] = {
        "model": model,
        "messages": anthropic_messages,
        "max_tokens": max_tokens or DEFAULT_ANTHROPIC_MAX_TOKENS,
        "stream": stream,
        "cache_control": dict(DEFAULT_CACHE_CONTROL),
    }
    if system:
        payload["system"] = system
    if temperature is not None:
        payload["temperature"] = float(temperature)
    anthropic_tools = _anthropic_tools(tools)
    if anthropic_tools:
        payload["tools"] = anthropic_tools
    return payload


def _response_content_blocks(data: dict[str, Any]) -> list[dict[str, object]]:
    normalized = _normalize_message_content(data.get("content"))
    if not isinstance(normalized, list):
        raise AppError(status_code=502, code="MODEL_ERROR", message="Anthropic 响应格式不正确")
    return normalized


def _extract_tool_uses(content_blocks: list[dict[str, object]]) -> list[dict[str, object]]:
    tool_uses: list[dict[str, object]] = []
    for block in content_blocks:
        if block.get("type") != "tool_use":
            continue
        tool_use_id = block.get("id")
        name = block.get("name")
        tool_input = block.get("input")
        if not isinstance(tool_use_id, str) or not tool_use_id:
            continue
        if not isinstance(name, str) or not name:
            continue
        tool_uses.append(
            {
                "id": tool_use_id,
                "name": name,
                "input": _json_copy(tool_input) if isinstance(tool_input, dict) else {},
            }
        )
    return tool_uses


def _tool_arguments_json(tool_input: dict[str, object]) -> str:
    return json.dumps(tool_input, ensure_ascii=False, separators=(",", ":"))


async def _run_tool_round(
    *,
    message_history: list[dict[str, object]],
    assistant_content_blocks: list[dict[str, object]],
    tool_uses: list[dict[str, object]],
    tool_executor: ToolExecutor,
    event_callback: ToolEventCallback | None = None,
) -> None:
    message_history.append({"role": "assistant", "content": _json_copy(assistant_content_blocks)})

    tool_result_blocks: list[dict[str, object]] = []
    for index, tool_use in enumerate(tool_uses):
        tool_name = str(tool_use["name"])
        tool_input = tool_use.get("input")
        tool_arguments = _tool_arguments_json(tool_input) if isinstance(tool_input, dict) else "{}"
        running_event: dict[str, object] = {
            "name": tool_name,
            "status": "running",
            "arguments": tool_arguments,
        }
        if event_callback is not None:
            await event_callback(running_event)

        tool_result = await tool_executor(tool_name, tool_arguments)

        if event_callback is not None:
            await event_callback(
                {
                    "name": tool_name,
                    "status": "completed",
                    "content": tool_result,
                }
            )

        tool_result_blocks.append(
            {
                "type": "tool_result",
                "tool_use_id": str(tool_use.get("id") or f"toolu-{index}"),
                "content": tool_result,
            }
        )

    message_history.append({"role": "user", "content": tool_result_blocks})


def _finalize_stream_content_blocks(
    content_blocks_by_index: dict[int, dict[str, object]],
) -> list[dict[str, object]]:
    content_blocks: list[dict[str, object]] = []
    for index in sorted(content_blocks_by_index):
        block = content_blocks_by_index[index]
        block_type = block.get("type")

        if block_type == "text":
            text = str(block.get("text") or "")
            if text:
                content_blocks.append({"type": "text", "text": text})
            continue

        if block_type == "thinking":
            content_blocks.append(
                {
                    "type": "thinking",
                    "thinking": str(block.get("thinking") or ""),
                    "signature": str(block.get("signature") or ""),
                }
            )
            continue

        if block_type == "redacted_thinking":
            data = str(block.get("data") or "")
            if data:
                content_blocks.append({"type": "redacted_thinking", "data": data})
            continue

        if block_type != "tool_use":
            continue

        name = block.get("name")
        if not isinstance(name, str) or not name:
            continue

        tool_input = block.get("input")
        partial_json = str(block.get("_input_json") or "")
        if partial_json:
            try:
                parsed_input = json.loads(partial_json)
            except json.JSONDecodeError as exc:
                raise AppError(status_code=502, code="MODEL_ERROR", message="Anthropic 工具参数格式不正确") from exc
            if not isinstance(parsed_input, dict):
                raise AppError(status_code=502, code="MODEL_ERROR", message="Anthropic 工具参数格式不正确")
            tool_input = parsed_input

        content_blocks.append(
            {
                "type": "tool_use",
                "id": str(block.get("id") or f"toolu-{index}"),
                "name": name,
                "input": _json_copy(tool_input) if isinstance(tool_input, dict) else {},
            }
        )

    return content_blocks


async def _stream_completion_round(
    client: httpx.AsyncClient,
    *,
    url: str,
    api_key: str,
    payload: dict[str, object],
    round_index: int,
) -> AsyncIterator[dict[str, object]]:
    try:
        async with client.stream("POST", url, headers=_build_headers(api_key), json=payload) as response:
            if response.status_code >= 400:
                body = await response.aread()
                fallback = httpx.Response(
                    status_code=response.status_code,
                    headers=response.headers,
                    content=body,
                    request=response.request,
                )
                raise AppError(status_code=502, code="MODEL_ERROR", message=_extract_error_message(fallback))

            accumulated_content = ""
            emitted_content = ""
            content_blocks_by_index: dict[int, dict[str, object]] = {}
            round_usage: dict[str, int] | None = None

            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    break
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                event_type = data.get("type")
                if event_type == "error":
                    error = data.get("error")
                    if isinstance(error, dict):
                        message = str(error.get("message") or "Anthropic 返回错误")
                    else:
                        message = str(error or "Anthropic 返回错误")
                    raise AppError(status_code=502, code="MODEL_ERROR", message=message)

                if event_type == "message_start":
                    message = data.get("message")
                    if isinstance(message, dict):
                        round_usage = _merge_usage(round_usage, _extract_usage(message))
                    continue

                if event_type == "message_delta":
                    delta_usage = _extract_usage(data)
                    if delta_usage is not None:
                        # Anthropic's message_delta output_tokens is cumulative for
                        # this request, so replace it rather than adding each delta.
                        prompt_tokens = (round_usage or {}).get("prompt_tokens", 0)
                        completion_tokens = delta_usage["completion_tokens"]
                        round_usage = {
                            "prompt_tokens": prompt_tokens,
                            "completion_tokens": completion_tokens,
                            "total_tokens": prompt_tokens + completion_tokens,
                        }
                    continue

                if event_type == "content_block_start":
                    index = data.get("index")
                    if not isinstance(index, int):
                        index = len(content_blocks_by_index)
                    content_block = data.get("content_block")
                    if not isinstance(content_block, dict):
                        continue

                    block_type = content_block.get("type")
                    if block_type == "text":
                        text = str(content_block.get("text") or "")
                        content_blocks_by_index[index] = {"type": "text", "text": text}
                        if text:
                            accumulated_content += text
                            emitted_content += text
                            yield {"type": "content", "content": text}
                    elif block_type == "thinking":
                        thinking = str(content_block.get("thinking") or "")
                        content_blocks_by_index[index] = {
                            "type": "thinking",
                            "thinking": thinking,
                            "signature": str(content_block.get("signature") or ""),
                        }
                        thinking_id = f"thinking-{round_index}-{index}"
                        yield {
                            "type": "thinking_started",
                            "thinking_id": thinking_id,
                            "text": thinking,
                        }
                    elif block_type == "redacted_thinking":
                        data = str(content_block.get("data") or "")
                        content_blocks_by_index[index] = {
                            "type": "redacted_thinking",
                            "data": data,
                        }
                        yield {
                            "type": "thinking_redacted",
                            "thinking_id": f"thinking-{round_index}-{index}",
                        }
                    elif block_type == "tool_use":
                        tool_input = content_block.get("input")
                        content_blocks_by_index[index] = {
                            "type": "tool_use",
                            "id": str(content_block.get("id") or f"toolu-{index}"),
                            "name": str(content_block.get("name") or ""),
                            "input": _json_copy(tool_input) if isinstance(tool_input, dict) else {},
                            "_input_json": "",
                        }
                    continue

                if event_type == "content_block_stop":
                    index = data.get("index")
                    if isinstance(index, int) and content_blocks_by_index.get(index, {}).get("type") == "thinking":
                        yield {
                            "type": "thinking_completed",
                            "thinking_id": f"thinking-{round_index}-{index}",
                        }
                    continue

                if event_type != "content_block_delta":
                    continue

                index = data.get("index")
                if not isinstance(index, int):
                    continue

                delta = data.get("delta")
                if not isinstance(delta, dict):
                    continue

                delta_type = delta.get("type")
                if delta_type == "text_delta":
                    text = str(delta.get("text") or "")
                    if not text:
                        continue
                    block = content_blocks_by_index.setdefault(index, {"type": "text", "text": ""})
                    block["type"] = "text"
                    block["text"] = str(block.get("text") or "") + text
                    accumulated_content += text
                    emitted_content += text
                    yield {"type": "content", "content": text}
                elif delta_type == "thinking_delta":
                    thinking = str(delta.get("thinking") or "")
                    if not thinking:
                        continue
                    block = content_blocks_by_index.setdefault(
                        index,
                        {"type": "thinking", "thinking": "", "signature": ""},
                    )
                    block["type"] = "thinking"
                    block["thinking"] = str(block.get("thinking") or "") + thinking
                    yield {
                        "type": "thinking_delta",
                        "thinking_id": f"thinking-{round_index}-{index}",
                        "text": thinking,
                    }
                elif delta_type == "signature_delta":
                    signature = str(delta.get("signature") or "")
                    block = content_blocks_by_index.setdefault(
                        index,
                        {"type": "thinking", "thinking": "", "signature": ""},
                    )
                    block["type"] = "thinking"
                    block["signature"] = signature
                elif delta_type == "input_json_delta":
                    partial_json = str(delta.get("partial_json") or "")
                    if not partial_json:
                        continue
                    block = content_blocks_by_index.setdefault(
                        index,
                        {
                            "type": "tool_use",
                            "id": f"toolu-{index}",
                            "name": "",
                            "input": {},
                            "_input_json": "",
                        },
                    )
                    block["type"] = "tool_use"
                    block["_input_json"] = str(block.get("_input_json") or "") + partial_json

            content_blocks = _finalize_stream_content_blocks(content_blocks_by_index)
            tool_uses = _extract_tool_uses(content_blocks)
            if tool_uses:
                round_text_blocks = [
                    str(content_blocks_by_index[index].get("text") or "")
                    for index in sorted(content_blocks_by_index)
                    if content_blocks_by_index[index].get("type") == "text"
                    and str(content_blocks_by_index[index].get("text") or "")
                ]
                yield {
                    "type": "tool_uses",
                    "tool_uses": tool_uses,
                    "content_blocks": content_blocks,
                    "emitted_content": emitted_content,
                    "round_text_blocks": round_text_blocks,
                    "usage": round_usage,
                }
            else:
                yield {"type": "done", "content": accumulated_content, "usage": round_usage}
    except AppError:
        raise
    except httpx.HTTPError as exc:
        raise AppError(
            status_code=502,
            code="MODEL_ERROR",
            message=_http_error_message("请求 Anthropic 失败", exc),
        ) from exc


async def create_anthropic_reply(
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
    raw_key = decrypt_text(api_key.key_encrypted)
    url = f"{_resolve_base_url(api_key.base_url)}/messages"
    if tools and tool_executor is None:
        raise AppError(status_code=500, code="CONFIG_ERROR", message="启用工具调用时必须提供 tool_executor")

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(180.0, connect=15.0),
        follow_redirects=True,
        trust_env=False,
        http2=False,
    ) as client:
        message_history: list[dict[str, object]] = _transcript_to_anthropic_history(transcript)
        max_rounds = max_tool_round_trips if tools else 1
        total_usage: dict[str, int] | None = None
        for round_index in range(max_rounds):
            payload = _messages_payload(
                model=model,
                messages=message_history,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=False,
                tools=tools,
            )

            try:
                response = await client.post(url, headers=_build_headers(raw_key), json=payload)
            except httpx.HTTPError as exc:
                raise AppError(
                    status_code=502,
                    code="MODEL_ERROR",
                    message=_http_error_message("请求 Anthropic 失败", exc),
                ) from exc

            if response.status_code >= 400:
                raise AppError(status_code=502, code="MODEL_ERROR", message=_extract_error_message(response))

            try:
                data = response.json()
                content_blocks = _response_content_blocks(data)
                total_usage = _merge_usage(total_usage, _extract_usage(data))
            except AppError:
                raise
            except Exception as exc:
                raise AppError(status_code=502, code="MODEL_ERROR", message="Anthropic 响应格式不正确") from exc

            tool_uses = _extract_tool_uses(content_blocks)
            if tool_uses:
                if tool_executor is None:
                    raise AppError(
                        status_code=500,
                        code="CONFIG_ERROR",
                        message="收到工具调用但未配置 tool_executor",
                    )
                await _run_tool_round(
                    message_history=message_history,
                    assistant_content_blocks=content_blocks,
                    tool_uses=tool_uses,
                    tool_executor=tool_executor,
                )
                continue

            content = _stringify_content(content_blocks)
            if content:
                return ReplyText(content, total_usage)
            raise AppError(status_code=502, code="MODEL_ERROR", message="Anthropic 未返回内容")

    raise AppError(status_code=502, code="MODEL_ERROR", message="模型工具调用次数超出限制")


async def stream_anthropic_reply(
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
    raw_key = decrypt_text(api_key.key_encrypted)
    url = f"{_resolve_base_url(api_key.base_url)}/messages"
    if tools and tool_executor is None:
        raise AppError(status_code=500, code="CONFIG_ERROR", message="启用工具调用时必须提供 tool_executor")

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(90.0, connect=15.0),
        follow_redirects=True,
        trust_env=False,
        http2=False,
    ) as client:
        message_history: list[dict[str, object]] = _transcript_to_anthropic_history(transcript)
        max_rounds = max_tool_round_trips if tools else 1
        total_usage: dict[str, int] | None = None
        for round_index in range(max_rounds):
            payload = _messages_payload(
                model=model,
                messages=message_history,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                tools=tools,
            )

            round_content = ""
            round_emitted_content = ""
            round_content_blocks: list[dict[str, object]] | None = None
            round_tool_uses: list[dict[str, object]] | None = None
            round_text_blocks: list[str] = []
            async for event in _stream_completion_round(
                client,
                url=url,
                api_key=raw_key,
                payload=payload,
                round_index=round_index,
            ):
                event_type = event.get("type")
                total_usage = _merge_usage(
                    total_usage,
                    event.get("usage") if isinstance(event.get("usage"), dict) else None,
                )
                if event_type == "content":
                    content = str(event.get("content") or "")
                    if content:
                        round_content += content
                        round_emitted_content += content
                        yield {"type": "content", "content": content}
                elif event_type in {"thinking_started", "thinking_delta", "thinking_completed", "thinking_redacted"}:
                    yield event
                elif event_type == "tool_uses":
                    content_blocks = event.get("content_blocks")
                    tool_uses = event.get("tool_uses")
                    round_emitted_content = str(event.get("emitted_content") or round_emitted_content)
                    round_content_blocks = content_blocks if isinstance(content_blocks, list) else []
                    round_tool_uses = tool_uses if isinstance(tool_uses, list) else []
                    text_blocks = event.get("round_text_blocks")
                    round_text_blocks = [str(item) for item in text_blocks] if isinstance(text_blocks, list) else []
                elif event_type == "done":
                    round_content = str(event.get("content") or round_content)

            if round_tool_uses:
                if tool_executor is None:
                    raise AppError(
                        status_code=500,
                        code="CONFIG_ERROR",
                        message="收到工具调用但未配置 tool_executor",
                    )
                round_text_joined = "".join(round_text_blocks)
                if round_text_joined:
                    yield {"type": "content_retracted", "content": round_text_joined}
                    yield {"type": "round_commentary", "text": round_text_joined}
                await _run_tool_round(
                    message_history=message_history,
                    assistant_content_blocks=round_content_blocks or [],
                    tool_uses=round_tool_uses,
                    tool_executor=tool_executor,
                    event_callback=tool_event_callback,
                )
                continue

            if round_content:
                if usage_callback is not None:
                    await usage_callback(total_usage)
                if len(round_emitted_content) < len(round_content) and round_content.startswith(round_emitted_content):
                    yield {"type": "content", "content": round_content[len(round_emitted_content):]}
                return
            raise AppError(status_code=502, code="MODEL_ERROR", message="Anthropic 未返回内容")

    raise AppError(status_code=502, code="MODEL_ERROR", message="模型工具调用次数超出限制")


async def test_anthropic_key(*, api_key: ApiKey) -> tuple[bool, str]:
    raw_key = decrypt_text(api_key.key_encrypted)
    url = f"{_resolve_base_url(api_key.base_url)}/models"

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0),
        follow_redirects=True,
        trust_env=False,
        http2=False,
    ) as client:
        try:
            response = await client.get(url, headers=_build_headers(raw_key))
        except httpx.HTTPError as exc:
            return False, _http_error_message("连接 Anthropic 失败", exc)

    if response.status_code >= 400:
        return False, _extract_error_message(response)
    return True, "连接成功"


async def list_anthropic_models(*, api_key: ApiKey) -> list[dict[str, str | None]]:
    raw_key = decrypt_text(api_key.key_encrypted)
    url = f"{_resolve_base_url(api_key.base_url)}/models"

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0),
        follow_redirects=True,
        trust_env=False,
        http2=False,
    ) as client:
        last_exc: httpx.HTTPError | None = None
        for attempt in range(2):
            try:
                response = await client.get(url, headers=_build_headers(raw_key))
                break
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt == 0:
                    await asyncio.sleep(0.2)
                    continue
        else:
            assert last_exc is not None
            raise AppError(
                status_code=502,
                code="MODEL_ERROR",
                message=_http_error_message("请求 Anthropic 模型列表失败", last_exc),
            ) from last_exc

    if response.status_code >= 400:
        raise AppError(status_code=502, code="MODEL_ERROR", message=_extract_error_message(response))

    try:
        payload = response.json()
    except Exception as exc:
        snippet = (response.text or "").strip().replace("\n", " ")[:300]
        details = snippet or "响应体为空或不是合法 JSON"
        raise AppError(
            status_code=502,
            code="MODEL_ERROR",
            message="Anthropic 模型列表响应格式不正确",
            details=details,
        ) from exc

    if isinstance(payload, dict):
        items = payload.get("data")
        if items is None and isinstance(payload.get("models"), list):
            items = payload.get("models")
        if items is None and isinstance(payload.get("items"), list):
            items = payload.get("items")
    elif isinstance(payload, list):
        items = payload
    else:
        items = None

    if not isinstance(items, list):
        raise AppError(
            status_code=502,
            code="MODEL_ERROR",
            message="Anthropic 模型列表响应格式不正确",
            details=f"顶层类型: {type(payload).__name__}",
        )

    models: list[dict[str, str | None]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if not model_id:
            continue
        models.append(
            {
                "id": str(model_id),
                "owned_by": str(item.get("display_name") or "anthropic"),
            }
        )

    models.sort(key=lambda item: item["id"])
    return models
