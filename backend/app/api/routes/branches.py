from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session, get_current_user
from app.models.user import User
from app.schemas.branch import BranchCreate, BranchResponse, BranchUpdate
from app.schemas.message import ConversationMessagesResponse
from app.services.branches import (
    activate_conversation_branch,
    archive_conversation_branch,
    create_conversation_branch,
    delete_conversation_branch,
    list_conversation_branches,
    update_conversation_branch,
)
from app.services.messages import list_conversation_messages


router = APIRouter()


@router.get("/conversations/{conversation_id}/branches", response_model=list[BranchResponse])
async def branches_index(
    conversation_id: int,
    include_archived: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> list[BranchResponse]:
    branches = await list_conversation_branches(
        session=session,
        user_id=current_user.id,
        conversation_id=conversation_id,
        include_archived=include_archived,
    )
    return [BranchResponse.model_validate(branch) for branch in branches]


@router.post("/conversations/{conversation_id}/branches", response_model=BranchResponse)
async def branches_create(
    conversation_id: int,
    payload: BranchCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> BranchResponse:
    branch = await create_conversation_branch(
        session=session,
        user_id=current_user.id,
        conversation_id=conversation_id,
        payload=payload,
    )
    return BranchResponse.model_validate(branch)


@router.put("/conversations/{conversation_id}/branches/{branch_id}", response_model=BranchResponse)
async def branches_update(
    conversation_id: int,
    branch_id: int,
    payload: BranchUpdate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> BranchResponse:
    branch = await update_conversation_branch(
        session=session,
        user_id=current_user.id,
        conversation_id=conversation_id,
        branch_id=branch_id,
        payload=payload,
    )
    return BranchResponse.model_validate(branch)


@router.post("/conversations/{conversation_id}/branches/{branch_id}/activate", response_model=ConversationMessagesResponse)
async def branches_activate(
    conversation_id: int,
    branch_id: int,
    limit: int | None = Query(default=None, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> ConversationMessagesResponse:
    branch = await activate_conversation_branch(
        session=session,
        user_id=current_user.id,
        conversation_id=conversation_id,
        branch_id=branch_id,
    )
    return await list_conversation_messages(
        session=session,
        user_id=current_user.id,
        conversation_id=conversation_id,
        leaf_message_id=branch.current_leaf_message_id,
        limit=limit,
    )


@router.post("/conversations/{conversation_id}/branches/{branch_id}/archive", response_model=BranchResponse)
async def branches_archive(
    conversation_id: int,
    branch_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> BranchResponse:
    branch = await archive_conversation_branch(
        session=session,
        user_id=current_user.id,
        conversation_id=conversation_id,
        branch_id=branch_id,
    )
    return BranchResponse.model_validate(branch)


@router.delete("/conversations/{conversation_id}/branches/{branch_id}", status_code=status.HTTP_204_NO_CONTENT)
async def branches_delete(
    conversation_id: int,
    branch_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> Response:
    await delete_conversation_branch(
        session=session,
        user_id=current_user.id,
        conversation_id=conversation_id,
        branch_id=branch_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
