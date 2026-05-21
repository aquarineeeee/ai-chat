from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import encrypt_text
from app.core.exceptions import AppError
from app.models.api_key import ApiKey
from app.providers.openai_compatible import normalize_base_url, test_openai_compatible_key
from app.schemas.api_key import ApiKeyCreateRequest


SUPPORTED_KEY_PROVIDERS = {"openai"}


def _normalize_provider(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized not in SUPPORTED_KEY_PROVIDERS:
        raise AppError(status_code=422, code="VALIDATION_ERROR", message=f"暂不支持 provider '{provider}'")
    return normalized


async def list_api_keys(session: AsyncSession, user_id: int) -> list[ApiKey]:
    result = await session.scalars(
        select(ApiKey)
        .where(ApiKey.user_id == user_id)
        .order_by(ApiKey.updated_at.desc(), ApiKey.id.desc())
    )
    return list(result.all())


async def get_api_key(session: AsyncSession, user_id: int, api_key_id: int) -> ApiKey:
    api_key = await session.scalar(
        select(ApiKey).where(
            ApiKey.id == api_key_id,
            ApiKey.user_id == user_id,
        )
    )
    if api_key is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="API Key 不存在")
    return api_key


async def create_api_key(session: AsyncSession, user_id: int, payload: ApiKeyCreateRequest) -> ApiKey:
    provider = _normalize_provider(payload.provider)
    raw_key = payload.api_key.strip()
    if not raw_key:
        raise AppError(status_code=422, code="VALIDATION_ERROR", message="API Key 不能为空")

    api_key = ApiKey(
        user_id=user_id,
        provider=provider,
        display_name=payload.display_name.strip(),
        base_url=normalize_base_url(payload.base_url),
        key_encrypted=encrypt_text(raw_key),
        key_last_four=raw_key[-4:] if len(raw_key) >= 4 else raw_key,
    )
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)
    return api_key


async def delete_api_key(session: AsyncSession, user_id: int, api_key_id: int) -> None:
    api_key = await get_api_key(session=session, user_id=user_id, api_key_id=api_key_id)
    await session.delete(api_key)
    await session.commit()


async def get_preferred_api_key(session: AsyncSession, user_id: int, provider: str) -> ApiKey:
    normalized = _normalize_provider(provider)
    api_key = await session.scalar(
        select(ApiKey)
        .where(
            ApiKey.user_id == user_id,
            ApiKey.provider == normalized,
        )
        .order_by(ApiKey.updated_at.desc(), ApiKey.id.desc())
    )
    if api_key is None:
        raise AppError(status_code=400, code="MISSING_API_KEY", message=f"请先为 provider '{normalized}' 配置 API Key")
    return api_key


async def test_api_key(session: AsyncSession, user_id: int, api_key_id: int) -> tuple[ApiKey, bool, str]:
    api_key = await get_api_key(session=session, user_id=user_id, api_key_id=api_key_id)

    success, message = await test_openai_compatible_key(api_key=api_key)
    api_key.last_tested_at = datetime.now(timezone.utc).replace(tzinfo=None)
    api_key.last_test_status = "success" if success else "failed"
    api_key.last_test_message = message
    await session.commit()
    await session.refresh(api_key)
    return api_key, success, message
