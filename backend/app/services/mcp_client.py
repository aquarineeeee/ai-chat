from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.encryption import decrypt_text

MCP_PROTOCOL_VERSION = "2025-11-25"


def _jsonrpc_payload(response: httpx.Response) -> dict[str, Any]:
    try:
        value = response.json()
        if isinstance(value, dict):
            return value
    except ValueError:
        pass
    for block in response.text.split("\n\n"):
        data = [line[5:] for line in block.splitlines() if line.startswith("data:")]
        if data:
            value = json.loads("\n".join(data).strip())
            if isinstance(value, dict):
                return value
    raise RuntimeError("MCP 响应不是合法 JSON-RPC")


@dataclass
class McpConnection:
    url: str
    headers: dict[str, str]
    timeout: float = 30.0
    client: httpx.AsyncClient | None = None
    session_id: str | None = None

    async def __aenter__(self) -> "McpConnection":
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=min(self.timeout, 10.0)),
            follow_redirects=False,
            trust_env=False,
        )
        response = await self._request("initialize", {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "ai-chat", "version": "0.1.0"},
        })
        if response.get("error"):
            raise RuntimeError(str(response["error"]))
        assert self.client is not None
        # initialize responses carry this outside the JSON-RPC body.
        raw = getattr(self, "_last_response", None)
        self.session_id = raw.headers.get("mcp-session-id") if raw is not None else None
        if not self.session_id:
            raise RuntimeError("MCP initialize response 缺少 mcp-session-id")
        return self

    async def _request(self, method: str, params: dict[str, Any], *, request_id: int = 1) -> dict[str, Any]:
        if self.client is None:
            raise RuntimeError("MCP 会话尚未初始化")
        headers = {
            **self.headers,
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
            headers["mcp-protocol-version"] = MCP_PROTOCOL_VERSION
        response = await self.client.post(self.url, headers=headers, json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        self._last_response = response
        if response.status_code >= 400:
            raise RuntimeError(f"MCP HTTP {response.status_code}")
        return _jsonrpc_payload(response)

    async def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            result = await self._request("tools/list", {"cursor": cursor} if cursor else {})
            if result.get("error"):
                raise RuntimeError(str(result["error"]))
            body = result.get("result") or {}
            page = body.get("tools") if isinstance(body, dict) else None
            if not isinstance(page, list):
                raise RuntimeError("MCP tools/list 响应缺少 tools")
            tools.extend(item for item in page if isinstance(item, dict))
            cursor = body.get("nextCursor") if isinstance(body, dict) else None
            if not cursor:
                return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        response = await self._request("tools/call", {"name": name, "arguments": arguments})
        if response.get("error"):
            raise RuntimeError(str(response["error"]))
        result = response.get("result")
        return result if isinstance(result, dict) else {"content": [{"type": "text", "text": str(result)}]}

    async def __aexit__(self, *_: object) -> None:
        if self.client is None:
            return
        if self.session_id:
            try:
                headers = {**self.headers, "mcp-session-id": self.session_id, "mcp-protocol-version": MCP_PROTOCOL_VERSION}
                await self.client.delete(self.url, headers=headers)
            except Exception:
                pass
        await self.client.aclose()


def decrypt_headers(encrypted_json: str | None) -> dict[str, str]:
    if not encrypted_json:
        return {}
    try:
        payload = json.loads(decrypt_text(encrypted_json))
        return {str(k): str(v) for k, v in payload.items()} if isinstance(payload, dict) else {}
    except Exception:
        return {}


def normalize_result(result: dict[str, Any], limit: int = 12000) -> str:
    parts: list[str] = []
    for item in result.get("content", []):
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("text"), str):
            parts.append(item["text"])
        elif item.get("type") in {"image", "audio", "resource"}:
            parts.append(json.dumps({k: item.get(k) for k in ("type", "uri", "mimeType") if k in item}, ensure_ascii=False))
    structured = result.get("structuredContent")
    if structured is not None:
        parts.append(structured if isinstance(structured, str) else json.dumps(structured, ensure_ascii=False))
    text = "\n".join(parts).strip() or "MCP 工具返回空结果。"
    return text[:limit] + ("…" if len(text) > limit else "")
