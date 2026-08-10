from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import encrypt_text
from app.core.exceptions import AppError
from app.models.mcp import McpServer, McpTool
from app.schemas.mcp import McpHeaderInput, McpServerCreateRequest, McpServerUpdateRequest
from app.services.mcp_client import McpConnection, decrypt_headers, normalize_result

FORBIDDEN_HEADERS = {"host", "content-length", "transfer-encoding", "connection"}
MAX_TOOLS = 128
MAX_SCHEMA_CHARS = 50000


def normalize_server_name(value: str) -> str:
    name = re.sub(r"[^a-z0-9_-]+", "-", value.strip().lower()).strip("-_")
    return name[:110] or "server-" + hashlib.sha256(value.encode()).hexdigest()[:8]


def validate_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise AppError(status_code=422, code="VALIDATION_ERROR", message="MCP 地址必须是 http 或 https URL")
    if parsed.username or parsed.password:
        raise AppError(status_code=422, code="VALIDATION_ERROR", message="MCP 地址不能内嵌账号密码")
    return value.strip()


def normalize_headers(headers: list[McpHeaderInput]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in headers:
        name = item.name.strip()
        if item.delete:
            continue
        if not item.value:
            raise AppError(status_code=422, code="VALIDATION_ERROR", message=f"请求头值不能为空：{name}")
        if name.lower() in FORBIDDEN_HEADERS or "\r" in name or "\n" in name:
            raise AppError(status_code=422, code="VALIDATION_ERROR", message=f"不允许的请求头：{name}")
        if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name):
            raise AppError(status_code=422, code="VALIDATION_ERROR", message=f"请求头名称无效：{name}")
        result[name] = item.value
    return result


def model_tool_name(server_name: str, remote_name: str, used: set[str] | None = None) -> str:
    base = f"mcp__{normalize_server_name(server_name)}__{re.sub(r'[^a-z0-9_-]+', '-', remote_name.strip().lower()).strip('-_') or 'tool'}"
    base = base[:128]
    if used is None or base not in used:
        return base
    return (base[:119] + "__" + hashlib.sha256(f"{server_name}:{remote_name}".encode()).hexdigest()[:8])[:128]


def _headers_response(server: McpServer) -> list[dict[str, str]]:
    headers = decrypt_headers(server.headers_encrypted_json)
    return [{"name": key, "value": "••••" + value[-4:] if len(value) > 4 else "••••"} for key, value in headers.items()]


def serialize_server(server: McpServer) -> dict[str, object]:
    tools = []
    for tool in sorted(server.tools, key=lambda item: item.id):
        try:
            schema = json.loads(tool.input_schema_json)
        except Exception:
            schema = {"type": "object", "properties": {}}
        try:
            annotations = json.loads(tool.annotations_json) if tool.annotations_json else None
        except Exception:
            annotations = None
        tools.append({"id": tool.id, "remote_tool_name": tool.remote_tool_name, "model_tool_name": tool.model_tool_name, "description": tool.description, "input_schema": schema, "annotations": annotations, "enabled": tool.enabled, "requires_approval": tool.requires_approval, "remote_available": tool.remote_available, "synced_at": tool.synced_at})
    return {"id": server.id, "display_name": server.display_name, "server_name": server.server_name, "transport": server.transport, "headers": _headers_response(server), "enabled": server.enabled, "config_version": server.config_version, "tested_config_version": server.tested_config_version, "last_test_status": server.last_test_status, "last_test_message": server.last_test_message, "last_tested_at": server.last_tested_at, "last_successful_sync_at": server.last_successful_sync_at, "tools": tools}


async def list_servers(session: AsyncSession, user_id: int) -> list[McpServer]:
    result = await session.scalars(select(McpServer).where(McpServer.user_id == user_id).order_by(McpServer.id.asc()))
    servers = list(result.all())
    for server in servers:
        await session.refresh(server, attribute_names=["tools"])
    return servers


async def get_server(session: AsyncSession, user_id: int, server_id: int) -> McpServer:
    server = await session.scalar(select(McpServer).where(McpServer.id == server_id, McpServer.user_id == user_id))
    if server is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="MCP 服务不存在")
    await session.refresh(server, attribute_names=["tools"])
    return server


async def create_server(session: AsyncSession, user_id: int, payload: McpServerCreateRequest) -> McpServer:
    name = normalize_server_name(payload.display_name)
    if await session.scalar(select(McpServer.id).where(McpServer.user_id == user_id, McpServer.server_name == name)):
        raise AppError(status_code=409, code="CONFLICT", message="MCP 服务名称已存在")
    headers = normalize_headers(payload.headers)
    server = McpServer(user_id=user_id, display_name=payload.display_name.strip(), server_name=name, url=validate_url(payload.url), transport=payload.transport, headers_encrypted_json=encrypt_text(json.dumps(headers, ensure_ascii=False)) if headers else None, enabled=payload.enabled)
    session.add(server)
    await session.commit()
    await session.refresh(server)
    return await get_server(session, user_id, server.id)


async def update_server(session: AsyncSession, user_id: int, server_id: int, payload: McpServerUpdateRequest) -> McpServer:
    server = await get_server(session, user_id, server_id)
    changed = False
    if payload.display_name is not None and payload.display_name.strip() != server.display_name:
        server.display_name = payload.display_name.strip()
    if payload.url is not None and validate_url(payload.url) != server.url:
        server.url = validate_url(payload.url); changed = True
    if payload.transport is not None and payload.transport != server.transport:
        server.transport = payload.transport; changed = True
    if payload.headers is not None:
        headers = normalize_headers(payload.headers)
        # Masked values are intentionally treated as "not submitted".
        old = decrypt_headers(server.headers_encrypted_json)
        merged = dict(old)
        for item in payload.headers:
            key = item.name.strip()
            if item.delete:
                merged.pop(key, None)
            elif item.value.startswith("••••"):
                merged[key] = old.get(key, "")
            else:
                merged[key] = item.value
        if merged != old:
            server.headers_encrypted_json = encrypt_text(json.dumps(merged, ensure_ascii=False)) if merged else None; changed = True
    if payload.enabled is not None:
        server.enabled = payload.enabled
    if changed:
        server.config_version += 1
        server.tested_config_version = None
        server.last_test_status = "pending"
    await session.commit()
    return await get_server(session, user_id, server_id)


async def test_server(session: AsyncSession, user_id: int, server_id: int) -> McpServer:
    server = await get_server(session, user_id, server_id)
    try:
        async with McpConnection(server.url, decrypt_headers(server.headers_encrypted_json), server.transport) as connection:
            remote_tools = await connection.list_tools()
        used: set[str] = set()
        existing = {tool.remote_tool_name: tool for tool in server.tools}
        seen: set[str] = set()
        now = datetime.utcnow()
        for remote in remote_tools[:MAX_TOOLS]:
            remote_name = str(remote.get("name") or "").strip()
            if not remote_name:
                continue
            seen.add(remote_name)
            tool = existing.get(remote_name) or McpTool(server_id=server.id, remote_tool_name=remote_name, model_tool_name=model_tool_name(server.server_name, remote_name, used), input_schema_json="{}")
            if tool not in server.tools:
                session.add(tool)
            used.add(tool.model_tool_name)
            tool.model_tool_name = model_tool_name(server.server_name, remote_name, used - {tool.model_tool_name})
            tool.description = str(remote.get("description") or "")[:20000]
            schema = remote.get("inputSchema") or {"type": "object", "properties": {}}
            encoded = json.dumps(schema, ensure_ascii=False)
            if len(encoded) > MAX_SCHEMA_CHARS:
                raise AppError(status_code=422, code="MCP_LIMIT", message="工具输入 Schema 超过限制")
            tool.input_schema_json = encoded
            tool.annotations_json = json.dumps(remote.get("annotations"), ensure_ascii=False) if isinstance(remote.get("annotations"), dict) else None
            tool.remote_available = True; tool.synced_at = now
        for remote_name, tool in existing.items():
            if remote_name not in seen:
                tool.remote_available = False; tool.synced_at = now
        server.last_test_status = "success"; server.last_test_message = "连接成功"; server.last_tested_at = now; server.last_successful_sync_at = now; server.tested_config_version = server.config_version
        await session.commit()
    except Exception as exc:
        await session.rollback()
        server = await get_server(session, user_id, server_id)
        server.last_test_status = "error"; server.last_test_message = str(exc)[:500]; server.last_tested_at = datetime.utcnow()
        await session.commit()
    return await get_server(session, user_id, server_id)


async def runtime_snapshot(session: AsyncSession, user_id: int) -> list[dict[str, object]]:
    servers = await list_servers(session, user_id)
    snapshot: list[dict[str, object]] = []
    used: set[str] = set()
    for server in servers:
        if not server.enabled or server.tested_config_version != server.config_version or server.last_successful_sync_at is None:
            continue
        for tool in server.tools:
            if not tool.enabled or not tool.remote_available:
                continue
            try:
                schema = json.loads(tool.input_schema_json)
            except Exception:
                schema = {"type": "object", "properties": {}}
            name = tool.model_tool_name
            if name in used:
                name = model_tool_name(server.server_name, tool.remote_tool_name, used)
            used.add(name)
            snapshot.append({"model_tool_name": name, "server_id": server.id, "server_name": server.server_name, "url": server.url, "transport": server.transport, "headers": decrypt_headers(server.headers_encrypted_json), "remote_tool_name": tool.remote_tool_name, "requires_approval": tool.requires_approval, "definition": {"type": "function", "function": {"name": name, "description": tool.description or tool.remote_tool_name, "parameters": schema}}})
    return snapshot


async def execute_runtime_tool(context: dict[str, object], tool_name: str, arguments: dict[str, object]) -> str:
    mapping = context.get("mcp_tool_map")
    if not isinstance(mapping, dict) or tool_name not in mapping:
        raise AppError(status_code=409, code="MCP_TOOL_UNAVAILABLE", message="MCP 工具已不可用，请重试")
    item = mapping[tool_name]
    if not isinstance(item, dict):
        raise AppError(status_code=409, code="MCP_TOOL_UNAVAILABLE", message="MCP 工具已不可用，请重试")
    connections = context.setdefault("mcp_connections", {})
    key = int(item["server_id"])
    connection = connections.get(key) if isinstance(connections, dict) else None
    if connection is None:
        connection = McpConnection(str(item["url"]), dict(item.get("headers") or {}), str(item.get("transport") or "streamable_http"))
        await connection.__aenter__()
        assert isinstance(connections, dict)
        connections[key] = connection
    result = await connection.call_tool(str(item["remote_tool_name"]), arguments)
    return normalize_result(result)


async def close_runtime_sessions(context: dict[str, object]) -> None:
    connections = context.get("mcp_connections")
    if not isinstance(connections, dict):
        return
    for connection in list(connections.values()):
        try:
            await connection.__aexit__(None, None, None)
        except Exception:
            pass
    connections.clear()
