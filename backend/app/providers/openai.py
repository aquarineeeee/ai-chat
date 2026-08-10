from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from decimal import Decimal
from typing import Any

import httpx

from app.canonical_transcript import CanonicalTranscriptItem
from app.core.encryption import decrypt_text
from app.core.exceptions import AppError
from app.models.api_key import ApiKey


DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
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

    prompt_tokens = _coerce_token_count(usage.get("prompt_tokens"))
    completion_tokens = _coerce_token_count(usage.get("completion_tokens"))
    total_tokens = _coerce_token_count(usage.get("total_tokens"))
    if prompt_tokens is None and completion_tokens is None and total_tokens is None:
        return None

    return {
        "prompt_tokens": prompt_tokens or 0,
        "completion_tokens": completion_tokens or 0,
        "total_tokens": total_tokens or 0,
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


def normalize_base_url(base_url: str | None) -> str | None:
    if base_url is None:
        return None
    normalized = base_url.strip().rstrip("/")
    return normalized or None


def _resolve_base_url(base_url: str | None) -> str:
    return normalize_base_url(base_url) or DEFAULT_OPENAI_BASE_URL


def _build_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept-Encoding": "identity",
    }


def _http_error_message(prefix: str, exc: httpx.HTTPError) -> str:
    detail = str(exc).strip()
    if not detail:
        detail = repr(exc)
    return f"{prefix}: {type(exc).__name__}: {detail}"


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


def _extract_error_message(response: httpx.Response) -> str:
    fallback = f"上游模型服务返回 HTTP {response.status_code}"
    try:
        payload = response.json()
    except Exception:
        payload = None

    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if message:
                return str(message)
        if isinstance(error, str):
            return error

    body = response.text.strip()
    content_type = response.headers.get("content-type", "").lower()
    is_html = "html" in content_type or body.lower().startswith(("<!doctype html", "<html"))
    if is_html:
        reason = response.reason_phrase or "上游服务异常"
        return f"上游模型服务暂时不可用（HTTP {response.status_code} {reason}）。上游返回了 HTML 错误页，请稍后重试或检查 API 基础地址。"
    return body or fallback


def _chat_payload(
    *,
    model: str,
    messages: list[dict[str, object]],
    temperature: Decimal | None,
    max_tokens: int | None,
    stream: bool,
    tools: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": model,
        "messages": messages,
        "stream": stream,
    }
    if temperature is not None:
        payload["temperature"] = float(temperature)
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if stream:
        payload["stream_options"] = {"include_usage": True}
    return payload


def _response_message(data: dict[str, Any]) -> dict[str, Any]:
    choice = (data.get("choices") or [None])[0]
    message = choice.get("message") if isinstance(choice, dict) else None
    if not isinstance(message, dict):
        raise AppError(status_code=502, code="MODEL_ERROR", message="上游模型响应格式不正确")
    return message


def _extract_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    raw_tool_calls = message.get("tool_calls")
    if not isinstance(raw_tool_calls, list):
        return []

    tool_calls: list[dict[str, Any]] = []
    for item in raw_tool_calls:
        if not isinstance(item, dict):
            continue
        function = item.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        arguments = function.get("arguments")
        if not isinstance(name, str) or not isinstance(arguments, str):
            continue
        tool_calls.append(item)
    return tool_calls


def _append_tool_call_delta(
    tool_calls_by_index: dict[int, dict[str, Any]],
    delta_tool_calls: object,
) -> None:
    if not isinstance(delta_tool_calls, list):
        return

    for item in delta_tool_calls:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        if not isinstance(index, int):
            index = len(tool_calls_by_index)

        tool_call = tool_calls_by_index.setdefault(
            index,
            {"id": None, "type": "function", "function": {"name": "", "arguments": ""}},
        )
        tool_id = item.get("id")
        if isinstance(tool_id, str) and tool_id:
            tool_call["id"] = tool_id

        tool_type = item.get("type")
        if isinstance(tool_type, str) and tool_type:
            tool_call["type"] = tool_type

        function = item.get("function")
        if not isinstance(function, dict):
            continue

        function_payload = tool_call.setdefault("function", {"name": "", "arguments": ""})
        name = function.get("name")
        if isinstance(name, str) and name:
            function_payload["name"] += name
        arguments = function.get("arguments")
        if isinstance(arguments, str) and arguments:
            function_payload["arguments"] += arguments


def _finalize_stream_tool_calls(tool_calls_by_index: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    tool_calls: list[dict[str, Any]] = []
    for index in sorted(tool_calls_by_index):
        item = tool_calls_by_index[index]
        function = item.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        arguments = function.get("arguments")
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(arguments, str):
            continue
        tool_calls.append(
            {
                "id": item.get("id") or f"tool-call-{index}",
                "type": item.get("type") or "function",
                "function": {"name": name, "arguments": arguments},
            }
        )
    return tool_calls


async def _create_completion(
    client: httpx.AsyncClient,
    *,
    url: str,
    api_key: str,
    payload: dict[str, object],
) -> httpx.Response:
    try:
        return await client.post(url, headers=_build_headers(api_key), json=payload)
    except httpx.HTTPError as exc:
        raise AppError(
            status_code=502,
            code="MODEL_ERROR",
            message=_http_error_message("请求上游模型服务失败", exc),
        ) from exc


def _assistant_history_message(message: dict[str, Any]) -> dict[str, object]:
    history_message: dict[str, object] = {
        "role": "assistant",
        "content": message.get("content") if message.get("content") is not None else "",
    }
    tool_calls = _extract_tool_calls(message)
    if tool_calls:
        history_message["tool_calls"] = tool_calls
    return history_message


def _transcript_to_openai_messages(transcript: list[CanonicalTranscriptItem]) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = []
    current_assistant: dict[str, object] | None = None

    def flush_assistant() -> None:
        nonlocal current_assistant
        if current_assistant is None:
            return
        content = str(current_assistant.get("content") or "")
        tool_calls = current_assistant.get("tool_calls")
        if not content and not tool_calls:
            current_assistant = None
            return
        messages.append(current_assistant)
        current_assistant = None

    for item in transcript:
        if item.kind == "system_text":
            flush_assistant()
            messages.append({"role": "system", "content": item.text})
            continue

        if item.kind == "user_text":
            flush_assistant()
            messages.append({"role": "user", "content": item.text})
            continue

        if item.kind == "assistant_text":
            if current_assistant is None:
                current_assistant = {"role": "assistant", "content": ""}
            current_assistant["content"] = str(current_assistant.get("content") or "") + item.text
            continue

        if item.kind == "assistant_tool_call":
            if current_assistant is None:
                current_assistant = {"role": "assistant", "content": ""}
            tool_calls = current_assistant.setdefault("tool_calls", [])
            assert isinstance(tool_calls, list)
            tool_calls.append(
                {
                    "id": item.tool_call_id or f"tool-call-{len(tool_calls)}",
                    "type": "function",
                    "function": {
                        "name": item.tool_name,
                        "arguments": item.arguments,
                    },
                }
            )
            continue

        if item.kind == "tool_result":
            flush_assistant()
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": item.tool_call_id,
                    "content": item.result,
                }
            )

    flush_assistant()
    return messages


async def _run_tool_round(
    *,
    message_history: list[dict[str, object]],
    tool_calls: list[dict[str, Any]],
    tool_executor: ToolExecutor,
    assistant_content: str = "",
    event_callback: ToolEventCallback | None = None,
) -> None:
    message_history.append(_assistant_history_message({"tool_calls": tool_calls, "content": assistant_content}))
    for index, tool_call in enumerate(tool_calls):
        function = tool_call["function"]
        tool_name = str(function["name"])
        tool_arguments = str(function["arguments"])
        running_event: dict[str, object] = {
            "name": tool_name,
            "status": "running",
            "arguments": tool_arguments,
        }
        if index == 0 and assistant_content.strip():
            running_event["commentary"] = assistant_content
            running_event["commentary_source"] = "model"
            running_event["commentary_style"] = "progress"
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
        message_history.append(
            {
                "role": "tool",
                "tool_call_id": str(tool_call.get("id") or f"tool-call-{index}"),
                "content": tool_result,
            }
        )


async def _stream_completion_round(
    client: httpx.AsyncClient,
    *,
    url: str,
    api_key: str,
    payload: dict[str, object],
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
            tool_calls_by_index: dict[int, dict[str, Any]] = {}
            saw_tool_calls = False
            usage: dict[str, int] | None = None

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

                usage = _merge_usage(usage, _extract_usage(data))

                error = data.get("error")
                if error:
                    if isinstance(error, dict):
                        message = str(error.get("message") or "上游模型返回错误")
                    else:
                        message = str(error)
                    raise AppError(status_code=502, code="MODEL_ERROR", message=message)

                choices = data.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta") or {}
                if not isinstance(delta, dict):
                    continue

                delta_tool_calls = delta.get("tool_calls")
                if isinstance(delta_tool_calls, list) and delta_tool_calls:
                    saw_tool_calls = True
                    _append_tool_call_delta(tool_calls_by_index, delta_tool_calls)

                content = _stringify_content(delta.get("content"))
                if content:
                    accumulated_content += content
                    if not saw_tool_calls:
                        emitted_content += content
                        yield {"type": "content", "content": content}

            tool_calls = _finalize_stream_tool_calls(tool_calls_by_index)
            if tool_calls:
                yield {
                    "type": "tool_calls",
                    "tool_calls": tool_calls,
                    "content": accumulated_content,
                    "emitted_content": emitted_content,
                    "usage": usage,
                }
            else:
                yield {"type": "done", "content": accumulated_content, "usage": usage}
    except AppError:
        raise
    except httpx.HTTPError as exc:
        raise AppError(
            status_code=502,
            code="MODEL_ERROR",
            message=_http_error_message("请求上游模型服务失败", exc),
        ) from exc


async def create_openai_reply(
    *,
    api_key: ApiKey,
    model: str,
    transcript: list[CanonicalTranscriptItem],
    temperature: Decimal | None,
    max_tokens: int | None,
    tools: list[dict[str, object]] | None = None,
    tool_executor: ToolExecutor | None = None,
    max_tool_round_trips: int = DEFAULT_MAX_TOOL_ROUND_TRIPS,
) -> str:
    raw_key = decrypt_text(api_key.key_encrypted)
    url = f"{_resolve_base_url(api_key.base_url)}/chat/completions"
    if tools and tool_executor is None:
        raise AppError(status_code=500, code="CONFIG_ERROR", message="启用工具调用时必须提供 tool_executor")

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(90.0, connect=15.0),
        follow_redirects=True,
        trust_env=False,
        http2=False,
    ) as client:
        message_history: list[dict[str, object]] = _transcript_to_openai_messages(transcript)
        max_rounds = max_tool_round_trips if tools else 1
        total_usage: dict[str, int] | None = None
        for _ in range(max_rounds):
            payload = _chat_payload(
                model=model,
                messages=message_history,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=False,
                tools=tools,
            )
            response = await _create_completion(
                client,
                url=url,
                api_key=raw_key,
                payload=payload,
            )
            if response.status_code >= 400:
                raise AppError(status_code=502, code="MODEL_ERROR", message=_extract_error_message(response))

            try:
                data = response.json()
                message = _response_message(data)
                total_usage = _merge_usage(total_usage, _extract_usage(data))
            except AppError:
                raise
            except Exception as exc:
                raise AppError(status_code=502, code="MODEL_ERROR", message="上游模型响应格式不正确") from exc

            tool_calls = _extract_tool_calls(message)
            if tool_calls:
                if tool_executor is None:
                    raise AppError(
                        status_code=500,
                        code="CONFIG_ERROR",
                        message="收到工具调用但未配置 tool_executor",
                    )
                await _run_tool_round(
                    message_history=message_history,
                    tool_calls=tool_calls,
                    tool_executor=tool_executor,
                    assistant_content=_stringify_content(message.get("content")),
                )
                continue

            content = _stringify_content(message.get("content"))
            if content:
                return ReplyText(content, total_usage)
            raise AppError(status_code=502, code="MODEL_ERROR", message="上游模型未返回内容")

    raise AppError(status_code=502, code="MODEL_ERROR", message="模型工具调用次数超出限制")


async def stream_openai_reply(
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
    url = f"{_resolve_base_url(api_key.base_url)}/chat/completions"
    if tools and tool_executor is None:
        raise AppError(status_code=500, code="CONFIG_ERROR", message="启用工具调用时必须提供 tool_executor")

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(90.0, connect=15.0),
        follow_redirects=True,
        trust_env=False,
        http2=False,
    ) as client:
        message_history: list[dict[str, object]] = _transcript_to_openai_messages(transcript)
        max_rounds = max_tool_round_trips if tools else 1
        total_usage: dict[str, int] | None = None
        for _ in range(max_rounds):
            payload = _chat_payload(
                model=model,
                messages=message_history,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                tools=tools,
            )

            round_content = ""
            round_emitted_content = ""
            round_tool_calls: list[dict[str, Any]] | None = None
            async for event in _stream_completion_round(
                client,
                url=url,
                api_key=raw_key,
                payload=payload,
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
                elif event_type == "tool_calls":
                    round_content = str(event.get("content") or round_content)
                    round_emitted_content = str(event.get("emitted_content") or round_emitted_content)
                    tool_calls = event.get("tool_calls")
                    round_tool_calls = tool_calls if isinstance(tool_calls, list) else []
                elif event_type == "done":
                    round_content = str(event.get("content") or round_content)

            if round_tool_calls:
                if tool_executor is None:
                    raise AppError(
                        status_code=500,
                        code="CONFIG_ERROR",
                        message="收到工具调用但未配置 tool_executor",
                    )
                await _run_tool_round(
                    message_history=message_history,
                    tool_calls=round_tool_calls,
                    tool_executor=tool_executor,
                    assistant_content=round_content,
                    event_callback=tool_event_callback,
                )
                continue

            if round_content:
                if usage_callback is not None:
                    await usage_callback(total_usage)
                if len(round_emitted_content) < len(round_content) and round_content.startswith(round_emitted_content):
                    yield {"type": "content", "content": round_content[len(round_emitted_content):]}
                return
            raise AppError(status_code=502, code="MODEL_ERROR", message="上游模型未返回内容")

    raise AppError(status_code=502, code="MODEL_ERROR", message="模型工具调用次数超出限制")


async def test_openai_key(*, api_key: ApiKey) -> tuple[bool, str]:
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
            return False, _http_error_message("连接失败", exc)

    if response.status_code >= 400:
        return False, _extract_error_message(response)
    return True, "连接成功"


async def list_openai_models(*, api_key: ApiKey) -> list[dict[str, object]]:
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
                message=_http_error_message("请求上游模型服务失败", last_exc),
            ) from last_exc

    if response.status_code >= 400:
        raise AppError(status_code=502, code="MODEL_ERROR", message=_extract_error_message(response))

    try:
        payload = response.json()
        items = payload.get("data") or []
    except Exception as exc:
        raise AppError(status_code=502, code="MODEL_ERROR", message="上游模型列表响应格式不正确") from exc

    models: list[dict[str, object]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if not model_id:
            continue
        models.append(
            {
                "id": str(model_id),
                "display_name": str(item.get("display_name") or item.get("name")) if item.get("display_name") or item.get("name") else None,
                "owned_by": str(item.get("owned_by")) if item.get("owned_by") is not None else None,
                # OpenRouter-compatible catalogues commonly include per-token
                # pricing here. Preserve it so the cached settings view can
                # render price and cache-hit price without another network call.
                "pricing": item.get("pricing") if isinstance(item.get("pricing"), dict) else None,
            }
        )

    models.sort(key=lambda item: item["id"])
    return models
