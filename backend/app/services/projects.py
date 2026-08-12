from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import AppError
from app.models.conversation import Conversation
from app.models.mcp import McpServer, McpTool
from app.models.project import Project, ProjectMcpTool
from app.schemas.project import ProjectCreate, ProjectToolInput, ProjectToolUpdate, ProjectUpdate


async def get_project(session: AsyncSession, user_id: int, project_id: int) -> Project:
    project = await session.scalar(select(Project).where(Project.id == project_id, Project.user_id == user_id).options(selectinload(Project.tools).selectinload(ProjectMcpTool.mcp_tool)))
    if project is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="项目不存在")
    return project


async def _validate_tools(session: AsyncSession, user_id: int, tools: list[ProjectToolInput]) -> dict[int, McpTool]:
    ids = {item.mcp_tool_id for item in tools}
    if not ids:
        return {}
    rows = list((await session.scalars(select(McpTool).join(McpServer).where(McpTool.id.in_(ids), McpServer.user_id == user_id))).all())
    found = {row.id: row for row in rows}
    if len(found) != len(ids):
        raise AppError(status_code=404, code="NOT_FOUND", message="MCP 工具不存在或不属于当前用户")
    return found


def serialize_project(project: Project, conversation_count: int = 0) -> dict[str, object]:
    return {
        "id": project.id, "user_id": project.user_id, "name": project.name,
        "system_prompt": project.system_prompt, "default_model_id": project.default_model_id,
        "conversation_count": conversation_count,
        "tools": [{"mcp_tool_id": item.mcp_tool_id, "requires_approval": item.requires_approval,
                    "model_tool_name": item.mcp_tool.model_tool_name, "remote_tool_name": item.mcp_tool.remote_tool_name,
                    "description": item.mcp_tool.description, "enabled": item.mcp_tool.enabled,
                    "remote_available": item.mcp_tool.remote_available} for item in project.tools if item.mcp_tool is not None],
        "created_at": project.created_at, "updated_at": project.updated_at,
    }


async def list_projects(session: AsyncSession, user_id: int) -> list[dict[str, object]]:
    projects = list((await session.scalars(select(Project).where(Project.user_id == user_id).order_by(Project.updated_at.desc(), Project.id.desc()).options(selectinload(Project.tools).selectinload(ProjectMcpTool.mcp_tool)))).all())
    counts = dict((await session.execute(select(Conversation.project_id, func.count(Conversation.id)).where(Conversation.user_id == user_id, Conversation.project_id.is_not(None)).group_by(Conversation.project_id))).all())
    return [serialize_project(item, int(counts.get(item.id, 0))) for item in projects]


async def create_project(session: AsyncSession, user_id: int, payload: ProjectCreate) -> Project:
    tool_map = await _validate_tools(session, user_id, payload.tools)
    project = Project(user_id=user_id, name=payload.name.strip(), system_prompt=payload.system_prompt, default_model_id=payload.default_model_id)
    session.add(project)
    await session.flush()
    for item in payload.tools:
        tool = tool_map[item.mcp_tool_id]
        if tool.enabled and tool.remote_available:
            session.add(ProjectMcpTool(project_id=project.id, mcp_tool_id=tool.id, requires_approval=item.requires_approval))
    await session.commit()
    return await get_project(session, user_id, project.id)


async def update_project(session: AsyncSession, user_id: int, project_id: int, payload: ProjectUpdate) -> Project:
    project = await get_project(session, user_id, project_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, key, value)
    await session.commit()
    return await get_project(session, user_id, project_id)


async def add_project_tool(session: AsyncSession, user_id: int, project_id: int, tool_id: int, requires_approval: bool = True) -> Project:
    project = await get_project(session, user_id, project_id)
    tool_map = await _validate_tools(session, user_id, [ProjectToolInput(mcp_tool_id=tool_id, requires_approval=requires_approval)])
    tool = tool_map[tool_id]
    if not tool.enabled or not tool.remote_available:
        raise AppError(status_code=409, code="MCP_TOOL_UNAVAILABLE", message="只能添加当前可用的 MCP 工具")
    if any(item.mcp_tool_id == tool_id for item in project.tools):
        raise AppError(status_code=409, code="CONFLICT", message="项目已添加该工具")
    session.add(ProjectMcpTool(project_id=project_id, mcp_tool_id=tool_id, requires_approval=requires_approval))
    await session.commit()
    return await get_project(session, user_id, project_id)


async def update_project_tool(session: AsyncSession, user_id: int, project_id: int, tool_id: int, payload: ProjectToolUpdate) -> Project:
    project = await get_project(session, user_id, project_id)
    item = next((value for value in project.tools if value.mcp_tool_id == tool_id), None)
    if item is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="项目工具不存在")
    if payload.requires_approval is not None:
        item.requires_approval = payload.requires_approval
    await session.commit()
    return await get_project(session, user_id, project_id)


async def remove_project_tool(session: AsyncSession, user_id: int, project_id: int, tool_id: int) -> None:
    project = await get_project(session, user_id, project_id)
    item = next((value for value in project.tools if value.mcp_tool_id == tool_id), None)
    if item is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="项目工具不存在")
    await session.delete(item)
    await session.commit()


async def delete_project(session: AsyncSession, user_id: int, project_id: int) -> int:
    project = await get_project(session, user_id, project_id)
    count = int((await session.scalar(select(func.count(Conversation.id)).where(Conversation.project_id == project_id))) or 0)
    await session.delete(project)
    await session.commit()
    return count
