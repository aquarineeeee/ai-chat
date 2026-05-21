from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session, get_current_user
from app.models.user import User
from app.schemas.api_key import ApiKeyCreateRequest, ApiKeyResponse, ApiKeyTestResponse, ProviderModelResponse
from app.services.api_keys import create_api_key, delete_api_key, list_api_keys, list_provider_models, test_api_key


router = APIRouter()


@router.get("", response_model=list[ApiKeyResponse])
async def keys_index(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> list[ApiKeyResponse]:
    api_keys = await list_api_keys(session=session, user_id=current_user.id)
    return [ApiKeyResponse.model_validate(item) for item in api_keys]


@router.post("", response_model=ApiKeyResponse, status_code=status.HTTP_201_CREATED)
async def keys_create(
    payload: ApiKeyCreateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> ApiKeyResponse:
    api_key = await create_api_key(session=session, user_id=current_user.id, payload=payload)
    return ApiKeyResponse.model_validate(api_key)


@router.delete("/{api_key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def keys_delete(
    api_key_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> Response:
    await delete_api_key(session=session, user_id=current_user.id, api_key_id=api_key_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{api_key_id}/test", response_model=ApiKeyTestResponse)
async def keys_test(
    api_key_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> ApiKeyTestResponse:
    api_key, success, message = await test_api_key(session=session, user_id=current_user.id, api_key_id=api_key_id)
    return ApiKeyTestResponse(
        success=success,
        message=message,
        api_key=ApiKeyResponse.model_validate(api_key),
    )


@router.get("/providers/{provider}/models", response_model=list[ProviderModelResponse])
async def keys_provider_models(
    provider: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> list[ProviderModelResponse]:
    models = await list_provider_models(session=session, user_id=current_user.id, provider=provider)
    return [ProviderModelResponse.model_validate(item) for item in models]
