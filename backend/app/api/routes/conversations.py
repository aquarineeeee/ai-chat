from __future__ import annotations

from fastapi import APIRouter, Depends, File, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session, get_current_user
from app.models.user import User
from app.schemas.conversation import (
    ConversationCreate,
    ConversationImportResponse,
    ConversationResponse,
    ConversationUpdate,
)
from app.services.conversations import (
    create_conversation,
    delete_conversation,
    get_conversation,
    import_markdown_conversation,
    list_conversations,
    update_conversation,
)


router = APIRouter()


@router.get("", response_model=list[ConversationResponse])
async def conversations_index(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> list[ConversationResponse]:
    conversations = await list_conversations(session=session, user_id=current_user.id)
    return [ConversationResponse.model_validate(item) for item in conversations]


@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
async def conversations_create(
    payload: ConversationCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> ConversationResponse:
    conversation = await create_conversation(session=session, user_id=current_user.id, payload=payload)
    return ConversationResponse.model_validate(conversation)


@router.post("/import-md", response_model=ConversationImportResponse, status_code=status.HTTP_201_CREATED)
async def conversations_import_markdown(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> ConversationImportResponse:
    file_bytes = await file.read()
    result = await import_markdown_conversation(
        session=session,
        user_id=current_user.id,
        filename=file.filename or "import.md",
        file_bytes=file_bytes,
    )
    return ConversationImportResponse.model_validate(result)


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def conversations_show(
    conversation_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> ConversationResponse:
    conversation = await get_conversation(session=session, user_id=current_user.id, conversation_id=conversation_id)
    return ConversationResponse.model_validate(conversation)


@router.put("/{conversation_id}", response_model=ConversationResponse)
async def conversations_update(
    conversation_id: int,
    payload: ConversationUpdate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> ConversationResponse:
    conversation = await update_conversation(
        session=session,
        user_id=current_user.id,
        conversation_id=conversation_id,
        payload=payload,
    )
    return ConversationResponse.model_validate(conversation)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def conversations_delete(
    conversation_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> Response:
    await delete_conversation(session=session, user_id=current_user.id, conversation_id=conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
