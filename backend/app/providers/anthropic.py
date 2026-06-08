from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from decimal import Decimal

import httpx

from app.core.encryption import decrypt_text
from app.core.exceptions import AppError
from app.models.api_key import ApiKey


DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_ANTHROPIC_MAX_TOKENS = 2000


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


def _convert_messages(messages: list[dict[str, str]]) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    system_parts: list[str] = []
    anthropic_messages: list[dict[str, str]] = []

    for message in messages:
        role = message.get("role")
        content = _stringify_content(message.get("content", "")).strip()
        if not content:
            continue
        if role == "system":
            system_parts.append(content)
            continue
        if role not in {"user", "assistant"}:
            continue
        anthropic_messages.append({"role": role, "content": content})

    return _anthropic_system_blocks("\n\n".join(system_parts)), anthropic_messages


def _messages_payload(
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: Decimal | None,
    max_tokens: int | None,
    stream: bool,
) -> dict[str, object]:
    system, anthropic_messages = _convert_messages(messages)
    if not anthropic_messages:
        raise AppError(status_code=422, code="VALIDATION_ERROR", message="Anthropic 请求至少需要一条 user/assistant 消息")

    payload: dict[str, object] = {
        "model": model,
        "messages": anthropic_messages,
        "max_tokens": max_tokens or DEFAULT_ANTHROPIC_MAX_TOKENS,
        "stream": stream,
    }
    if system:
        payload["system"] = system
    if temperature is not None:
        payload["temperature"] = float(temperature)
    return payload


async def create_anthropic_reply(
    *,
    api_key: ApiKey,
    model: str,
    messages: list[dict[str, str]],
    temperature: Decimal | None,
    max_tokens: int | None,
) -> str:
    raw_key = decrypt_text(api_key.key_encrypted)
    payload = _messages_payload(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=False,
    )
    url = f"{_resolve_base_url(api_key.base_url)}/messages"

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(90.0, connect=15.0),
        follow_redirects=True,
        trust_env=False,
        http2=False,
    ) as client:
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
        content = _stringify_content(data.get("content"))
    except Exception as exc:
        raise AppError(status_code=502, code="MODEL_ERROR", message="Anthropic 响应格式不正确") from exc

    if not content:
        raise AppError(status_code=502, code="MODEL_ERROR", message="Anthropic 未返回内容")
    return content


async def stream_anthropic_reply(
    *,
    api_key: ApiKey,
    model: str,
    messages: list[dict[str, str]],
    temperature: Decimal | None,
    max_tokens: int | None,
) -> AsyncIterator[str]:
    raw_key = decrypt_text(api_key.key_encrypted)
    payload = _messages_payload(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
    )
    url = f"{_resolve_base_url(api_key.base_url)}/messages"

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(90.0, connect=15.0),
        follow_redirects=True,
        trust_env=False,
        http2=False,
    ) as client:
        try:
            async with client.stream("POST", url, headers=_build_headers(raw_key), json=payload) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    fallback = httpx.Response(
                        status_code=response.status_code,
                        headers=response.headers,
                        content=body,
                        request=response.request,
                    )
                    raise AppError(status_code=502, code="MODEL_ERROR", message=_extract_error_message(fallback))

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

                    if event_type != "content_block_delta":
                        continue
                    delta = data.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        text = str(delta.get("text") or "")
                        if text:
                            yield text
        except AppError:
            raise
        except httpx.HTTPError as exc:
            raise AppError(
                status_code=502,
                code="MODEL_ERROR",
                message=_http_error_message("请求 Anthropic 失败", exc),
            ) from exc


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
