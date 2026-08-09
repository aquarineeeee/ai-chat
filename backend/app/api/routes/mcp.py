from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session, get_current_user
from app.models.user import User
from app.schemas.mcp import McpServerCreateRequest, McpServerResponse, McpServerUpdateRequest, McpToolUpdateRequest
from app.services.mcp_registry import create_server, get_server, list_servers, serialize_server, test_server, update_server

router = APIRouter(prefix="/mcp")

@router.get("/servers", response_model=list[McpServerResponse])
async def servers_index(current_user: User = Depends(get_current_user), session: AsyncSession = Depends(db_session)):
    return [McpServerResponse.model_validate(serialize_server(item)) for item in await list_servers(session, current_user.id)]

@router.post("/servers", response_model=McpServerResponse, status_code=201)
async def servers_create(payload: McpServerCreateRequest, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(db_session)):
    return McpServerResponse.model_validate(serialize_server(await create_server(session, current_user.id, payload)))

@router.patch("/servers/{server_id}", response_model=McpServerResponse)
async def servers_update(server_id: int, payload: McpServerUpdateRequest, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(db_session)):
    return McpServerResponse.model_validate(serialize_server(await update_server(session, current_user.id, server_id, payload)))

@router.delete("/servers/{server_id}", status_code=204)
async def servers_delete(server_id: int, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(db_session)):
    server = await get_server(session, current_user.id, server_id)
    await session.delete(server)
    await session.commit()
    return Response(status_code=204)

@router.post("/servers/{server_id}/test", response_model=McpServerResponse)
async def servers_test(server_id: int, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(db_session)):
    return McpServerResponse.model_validate(serialize_server(await test_server(session, current_user.id, server_id)))

@router.patch("/servers/{server_id}/tools/{tool_id}", response_model=McpServerResponse)
async def tools_update(server_id: int, tool_id: int, payload: McpToolUpdateRequest, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(db_session)):
    server = await get_server(session, current_user.id, server_id)
    tool = next((item for item in server.tools if item.id == tool_id), None)
    if tool is None:
        from app.core.exceptions import AppError
        raise AppError(status_code=404, code="NOT_FOUND", message="MCP 工具不存在")
    if payload.enabled is not None:
        tool.enabled = payload.enabled
    if payload.requires_approval is not None:
        tool.requires_approval = payload.requires_approval
    await session.commit()
    return McpServerResponse.model_validate(serialize_server(await get_server(session, current_user.id, server_id)))
