from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.sse import sse_client

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
    transport: str = "streamable_http"
    timeout: float = 30.0
    client: httpx.AsyncClient | None = None
    session_id: str | None = None
    sse_context: Any = None
    sse_session: ClientSession | None = None

    async def __aenter__(self) -> "McpConnection":
        if self.transport == "sse":
            self.sse_context = sse_client(
                self.url,
                headers=self.headers,
                timeout=self.timeout,
                sse_read_timeout=self.timeout,
            )
            read_stream, write_stream = await self.sse_context.__aenter__()
            self.sse_session = ClientSession(read_stream, write_stream)
            await self.sse_session.__aenter__()
            await self.sse_session.initialize()
            return self
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
            if self.sse_session is not None:
                body = await self.sse_session.list_tools(cursor=cursor)
                tools.extend(tool.model_dump(by_alias=True, mode="json") for tool in body.tools)
                cursor = body.nextCursor
                if not cursor:
                    return tools
                continue
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
        if self.sse_session is not None:
            return (await self.sse_session.call_tool(name, arguments)).model_dump(by_alias=True, mode="json")
        response = await self._request("tools/call", {"name": name, "arguments": arguments})
        if response.get("error"):
            raise RuntimeError(str(response["error"]))
        result = response.get("result")
        return result if isinstance(result, dict) else {"content": [{"type": "text", "text": str(result)}]}

    async def __aexit__(self, *_: object) -> None:
        if self.sse_session is not None:
            try:
                await self.sse_session.__aexit__(None, None, None)
            finally:
                self.sse_session = None
                if self.sse_context is not None:
                    await self.sse_context.__aexit__(None, None, None)
                    self.sse_context = None
            return
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
