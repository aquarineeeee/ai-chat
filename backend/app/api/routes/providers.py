from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session, get_current_user
from app.models.user import User
from app.schemas.provider import (
    ProviderModelCreateRequest,
    ProviderModelResponse,
    ProviderModelUpdateRequest,
    ProviderPresetResponse,
    ProviderResponse,
    ProviderTestResponse,
    ProviderCreateRequest,
    ProviderUpdateRequest,
)
from app.services.providers import (
    add_model,
    create_provider,
    delete_model,
    delete_provider,
    list_models,
    list_provider_presets,
    list_providers,
    sync_models,
    test_provider,
    update_model,
    update_provider,
)

router = APIRouter()


@router.get("/provider-presets", response_model=list[ProviderPresetResponse])
async def provider_presets() -> list[ProviderPresetResponse]:
    return [ProviderPresetResponse.model_validate(item) for item in await list_provider_presets()]


@router.get("/providers", response_model=list[ProviderResponse])
async def providers_index(current_user: User = Depends(get_current_user), session: AsyncSession = Depends(db_session)) -> list[ProviderResponse]:
    return [ProviderResponse.model_validate(item) for item in await list_providers(session, current_user.id)]


@router.post("/providers", response_model=ProviderResponse, status_code=status.HTTP_201_CREATED)
async def providers_create(payload: ProviderCreateRequest, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(db_session)) -> ProviderResponse:
    return ProviderResponse.model_validate(await create_provider(session, current_user.id, payload))


@router.patch("/providers/{provider_id}", response_model=ProviderResponse)
async def providers_update(provider_id: int, payload: ProviderUpdateRequest, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(db_session)) -> ProviderResponse:
    return ProviderResponse.model_validate(await update_provider(session, current_user.id, provider_id, payload))


@router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def providers_delete(provider_id: int, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(db_session)) -> Response:
    await delete_provider(session, current_user.id, provider_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/providers/{provider_id}/test", response_model=ProviderTestResponse)
async def providers_test(provider_id: int, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(db_session)) -> ProviderTestResponse:
    provider, success, message = await test_provider(session, current_user.id, provider_id)
    return ProviderTestResponse(success=success, message=message, provider=ProviderResponse.model_validate(provider))


@router.get("/providers/{provider_id}/models", response_model=list[ProviderModelResponse])
async def provider_models(provider_id: int, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(db_session)) -> list[ProviderModelResponse]:
    return [ProviderModelResponse.model_validate(item) for item in await list_models(session, current_user.id, provider_id)]


@router.post("/providers/{provider_id}/models/sync", response_model=list[ProviderModelResponse])
async def provider_models_sync(provider_id: int, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(db_session)) -> list[ProviderModelResponse]:
    return [ProviderModelResponse.model_validate(item) for item in await sync_models(session, current_user.id, provider_id)]


@router.post("/providers/{provider_id}/models", response_model=ProviderModelResponse, status_code=status.HTTP_201_CREATED)
async def provider_model_create(provider_id: int, payload: ProviderModelCreateRequest, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(db_session)) -> ProviderModelResponse:
    return ProviderModelResponse.model_validate(await add_model(session, current_user.id, provider_id, payload))


@router.patch("/providers/{provider_id}/models/{model_id}", response_model=ProviderModelResponse)
async def provider_model_update(provider_id: int, model_id: str, payload: ProviderModelUpdateRequest, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(db_session)) -> ProviderModelResponse:
    return ProviderModelResponse.model_validate(await update_model(session, current_user.id, provider_id, model_id, payload))


@router.delete("/providers/{provider_id}/models/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def provider_model_delete(provider_id: int, model_id: str, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(db_session)) -> Response:
    await delete_model(session, current_user.id, provider_id, model_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
