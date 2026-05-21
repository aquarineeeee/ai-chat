from __future__ import annotations

import json
from collections.abc import AsyncIterator
from decimal import Decimal

import httpx

from app.core.encryption import decrypt_text
from app.core.exceptions import AppError
from app.models.api_key import ApiKey


DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


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
    try:
        payload = response.json()
    except Exception:
        return response.text or f"涓婃父鏈嶅姟杩斿洖 HTTP {response.status_code}"

    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if message:
            return str(message)
    if isinstance(error, str):
        return error
    return response.text or f"涓婃父鏈嶅姟杩斿洖 HTTP {response.status_code}"


def _chat_payload(
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: Decimal | None,
    max_tokens: int | None,
    stream: bool,
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
    return payload


async def create_openai_compatible_reply(
    *,
    api_key: ApiKey,
    model: str,
    messages: list[dict[str, str]],
    temperature: Decimal | None,
    max_tokens: int | None,
) -> str:
    raw_key = decrypt_text(api_key.key_encrypted)
    payload = _chat_payload(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=False,
    )
    url = f"{_resolve_base_url(api_key.base_url)}/chat/completions"

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(90.0, connect=15.0),
        follow_redirects=True,
        trust_env=False,
        http2=False,
    ) as client:
        try:
            response = await client.post(url, headers=_build_headers(raw_key), json=payload)
        except httpx.HTTPError as exc:
            raise AppError(status_code=502, code="MODEL_ERROR", message=f"璇锋眰涓婃父妯″瀷鏈嶅姟澶辫触: {exc}") from exc

    if response.status_code >= 400:
        raise AppError(status_code=502, code="MODEL_ERROR", message=_extract_error_message(response))

    try:
        data = response.json()
        choice = (data.get("choices") or [])[0]
        message = choice.get("message") or {}
        content = _stringify_content(message.get("content"))
    except Exception as exc:
        raise AppError(status_code=502, code="MODEL_ERROR", message="上游模型响应格式不正确") from exc

    if not content:
        raise AppError(status_code=502, code="MODEL_ERROR", message="涓婃父妯″瀷杩斿洖浜嗙┖鍐呭")
    return content


async def stream_openai_compatible_reply(
    *,
    api_key: ApiKey,
    model: str,
    messages: list[dict[str, str]],
    temperature: Decimal | None,
    max_tokens: int | None,
) -> AsyncIterator[str]:
    raw_key = decrypt_text(api_key.key_encrypted)
    payload = _chat_payload(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
    )
    url = f"{_resolve_base_url(api_key.base_url)}/chat/completions"

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

                    error = data.get("error")
                    if error:
                        if isinstance(error, dict):
                            message = str(error.get("message") or "涓婃父妯″瀷杩斿洖閿欒")
                        else:
                            message = str(error)
                        raise AppError(status_code=502, code="MODEL_ERROR", message=message)

                    choices = data.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = _stringify_content(delta.get("content"))
                    if content:
                        yield content
        except AppError:
            raise
        except httpx.HTTPError as exc:
            raise AppError(status_code=502, code="MODEL_ERROR", message=f"璇锋眰涓婃父妯″瀷鏈嶅姟澶辫触: {exc}") from exc


async def test_openai_compatible_key(*, api_key: ApiKey) -> tuple[bool, str]:
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
            return False, f"连接失败: {exc}"

    if response.status_code >= 400:
        return False, _extract_error_message(response)
    return True, "连接成功"


async def list_openai_compatible_models(*, api_key: ApiKey) -> list[dict[str, str | None]]:
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
            raise AppError(status_code=502, code="MODEL_ERROR", message=f"请求上游模型服务失败: {exc}") from exc

    if response.status_code >= 400:
        raise AppError(status_code=502, code="MODEL_ERROR", message=_extract_error_message(response))

    try:
        payload = response.json()
        items = payload.get("data") or []
    except Exception as exc:
        raise AppError(status_code=502, code="MODEL_ERROR", message="上游模型列表响应格式不正确") from exc

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
                "owned_by": str(item.get("owned_by")) if item.get("owned_by") is not None else None,
            }
        )

    models.sort(key=lambda item: item["id"])
    return models
