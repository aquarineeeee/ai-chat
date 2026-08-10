from __future__ import annotations

import types
import unittest
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from app.models.mcp import McpServer
from app.schemas.mcp import McpServerCreateRequest, McpServerResponse
from app.services.mcp_client import McpConnection
from app.services.mcp_registry import create_server, serialize_server


class _Result:
    def __init__(self, value: dict[str, object]) -> None:
        self.value = value

    def model_dump(self, **_: object) -> dict[str, object]:
        return self.value


class _SseTransport:
    def __init__(self) -> None:
        self.closed = False

    async def __aenter__(self) -> tuple[object, object]:
        return object(), object()

    async def __aexit__(self, *_: object) -> None:
        self.closed = True


class _SseSession:
    def __init__(self, *_: object) -> None:
        self.initialized = False
        self.closed = False
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def __aenter__(self) -> "_SseSession":
        return self

    async def __aexit__(self, *_: object) -> None:
        self.closed = True

    async def initialize(self) -> None:
        self.initialized = True

    async def list_tools(self, *, cursor: str | None = None) -> object:
        assert cursor is None
        return types.SimpleNamespace(
            tools=[_Result({"name": "lookup", "inputSchema": {"type": "object"}})],
            nextCursor=None,
        )

    async def call_tool(self, name: str, arguments: dict[str, object]) -> _Result:
        self.calls.append((name, arguments))
        return _Result({"content": [{"type": "text", "text": "ok"}]})


class _CreateSession:
    def __init__(self) -> None:
        self.server: object | None = None

    async def scalar(self, *_: object) -> None:
        return None

    def add(self, server: object) -> None:
        self.server = server

    async def commit(self) -> None:
        return None

    async def refresh(self, server: object) -> None:
        server.id = 42


class McpTransportTests(unittest.IsolatedAsyncioTestCase):
    def test_server_response_does_not_expose_url(self) -> None:
        server = McpServer(
            id=1,
            user_id=7,
            display_name="Private server",
            server_name="private-server",
            url="https://example.test/mcp?token=secret",
            transport="streamable_http",
            enabled=True,
            config_version=1,
        )

        response = McpServerResponse.model_validate(serialize_server(server))

        self.assertNotIn("url", response.model_dump())

    def test_transport_is_limited_to_streamable_http_or_sse(self) -> None:
        self.assertEqual(
            McpServerCreateRequest(display_name="SSE", url="https://example.test/sse", transport="sse").transport,
            "sse",
        )
        with self.assertRaises(ValidationError):
            McpServerCreateRequest(display_name="invalid", url="https://example.test/mcp", transport="stdio")

    async def test_create_server_loads_tools_before_returning(self) -> None:
        db_session = _CreateSession()
        resolved_server = object()
        get_server = AsyncMock(return_value=resolved_server)

        with patch("app.services.mcp_registry.get_server", get_server):
            result = await create_server(
                db_session,
                7,
                McpServerCreateRequest(display_name="SSE", url="https://example.test/sse", transport="sse"),
            )

        self.assertIs(result, resolved_server)
        get_server.assert_awaited_once_with(db_session, 7, 42)

    async def test_sse_transport_initializes_and_uses_the_sdk_session(self) -> None:
        transport = _SseTransport()
        session = _SseSession()

        with (
            patch("app.services.mcp_client.sse_client", return_value=transport) as sse_client,
            patch("app.services.mcp_client.ClientSession", return_value=session),
        ):
            async with McpConnection("https://example.test/sse", {"Authorization": "Bearer token"}, "sse") as connection:
                self.assertTrue(session.initialized)
                self.assertEqual(await connection.list_tools(), [{"name": "lookup", "inputSchema": {"type": "object"}}])
                self.assertEqual(await connection.call_tool("lookup", {"query": "test"}), {"content": [{"type": "text", "text": "ok"}]})

        sse_client.assert_called_once_with(
            "https://example.test/sse",
            headers={"Authorization": "Bearer token"},
            timeout=30.0,
            sse_read_timeout=30.0,
        )
        self.assertEqual(session.calls, [("lookup", {"query": "test"})])
        self.assertTrue(session.closed)
        self.assertTrue(transport.closed)
