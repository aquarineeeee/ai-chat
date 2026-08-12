from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import db_session, get_current_user
from app.core.exceptions import AppError
from app.models.user import User
from app.schemas.project import ProjectCreate, ProjectResponse, ProjectToolInput, ProjectToolUpdate, ProjectUpdate
from app.services.projects import add_project_tool, create_project, delete_project, list_projects, remove_project_tool, serialize_project, update_project, update_project_tool

router = APIRouter(prefix="/projects")

@router.get("", response_model=list[ProjectResponse])
async def projects_index(current_user: User = Depends(get_current_user), session: AsyncSession = Depends(db_session)):
    return [ProjectResponse.model_validate(item) for item in await list_projects(session, current_user.id)]

@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def projects_create(payload: ProjectCreate, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(db_session)):
    return ProjectResponse.model_validate(serialize_project(await create_project(session, current_user.id, payload)))

@router.get("/{project_id}", response_model=ProjectResponse)
async def projects_show(project_id: int, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(db_session)):
    from app.services.projects import get_project
    return ProjectResponse.model_validate(serialize_project(await get_project(session, current_user.id, project_id)))

@router.put("/{project_id}", response_model=ProjectResponse)
async def projects_update(project_id: int, payload: ProjectUpdate, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(db_session)):
    return ProjectResponse.model_validate(serialize_project(await update_project(session, current_user.id, project_id, payload)))

@router.delete("/{project_id}", response_model=dict)
async def projects_delete(project_id: int, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(db_session)):
    return {"deleted_conversation_count": await delete_project(session, current_user.id, project_id)}

@router.post("/{project_id}/tools", response_model=ProjectResponse)
async def projects_tool_add(project_id: int, payload: ProjectToolInput, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(db_session)):
    return ProjectResponse.model_validate(serialize_project(await add_project_tool(session, current_user.id, project_id, payload.mcp_tool_id, payload.requires_approval)))

@router.put("/{project_id}/tools/{tool_id}", response_model=ProjectResponse)
async def projects_tool_update(project_id: int, tool_id: int, payload: ProjectToolUpdate, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(db_session)):
    return ProjectResponse.model_validate(serialize_project(await update_project_tool(session, current_user.id, project_id, tool_id, payload)))

@router.delete("/{project_id}/tools/{tool_id}", status_code=204)
async def projects_tool_delete(project_id: int, tool_id: int, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(db_session)):
    await remove_project_tool(session, current_user.id, project_id, tool_id)
    return Response(status_code=204)
