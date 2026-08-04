from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import decrypt_text, encrypt_text
from app.core.exceptions import AppError
from app.models.provider import ProviderInstance, ProviderModel
from app.providers.anthropic import list_anthropic_models, normalize_anthropic_base_url, test_anthropic_key
from app.providers.openai import list_openai_models, normalize_base_url, test_openai_key
from app.providers.registry import get_adapter, list_adapters
from app.schemas.provider import ProviderCreateRequest, ProviderModelCreateRequest, ProviderModelUpdateRequest, ProviderUpdateRequest

PRESETS = {
    "openai": ("OpenAI", "openai_responses"),
    "openrouter": ("OpenRouter", "openai_chat_completions"),
    "anthropic": ("Anthropic", "anthropic_messages"),
    "gemini": ("Gemini", "google_gemini_generate_content"),
    "custom": ("自定义服务商", "openai_chat_completions"),
}


def _credentials(instance: ProviderInstance) -> str | None:
    if not instance.credentials_encrypted_json:
        return None
    try:
        payload = json.loads(decrypt_text(instance.credentials_encrypted_json))
        return str(payload.get("api_key")) if payload.get("api_key") else None
    except Exception:
        return None


def _validate_adapter(adapter_id: str) -> None:
    try:
        get_adapter(adapter_id)
    except ValueError as exc:
        raise AppError(status_code=422, code="VALIDATION_ERROR", message=str(exc)) from exc


async def list_provider_presets() -> list[dict[str, object]]:
    adapters = [item.id for item in list_adapters()]
    return [{"id": key, "display_name": value[0], "default_adapter_id": value[1], "adapters": adapters} for key, value in PRESETS.items()]


async def list_providers(session: AsyncSession, user_id: int) -> list[ProviderInstance]:
    result = await session.scalars(select(ProviderInstance).where(ProviderInstance.user_id == user_id).order_by(ProviderInstance.is_default.desc(), ProviderInstance.updated_at.desc(), ProviderInstance.id.desc()))
    return list(result.all())


async def get_provider(session: AsyncSession, user_id: int, provider_id: int) -> ProviderInstance:
    item = await session.scalar(select(ProviderInstance).where(ProviderInstance.id == provider_id, ProviderInstance.user_id == user_id))
    if item is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="服务商不存在")
    return item


async def get_preferred_provider_instance(
    session: AsyncSession,
    user_id: int,
    preset_id: str,
) -> ProviderInstance | None:
    """Return the enabled instance that backs a runtime provider name."""
    result = await session.scalars(
        select(ProviderInstance)
        .where(
            ProviderInstance.user_id == user_id,
            ProviderInstance.preset_id == preset_id,
            ProviderInstance.enabled.is_(True),
        )
        .order_by(ProviderInstance.is_default.desc(), ProviderInstance.updated_at.desc(), ProviderInstance.id.desc())
        .limit(1)
    )
    return result.first()


async def create_provider(session: AsyncSession, user_id: int, payload: ProviderCreateRequest) -> ProviderInstance:
    preset = PRESETS.get(payload.preset_id.strip().lower())
    if preset is None:
        raise AppError(status_code=422, code="VALIDATION_ERROR", message="不支持的服务商预设")
    adapter_id = payload.default_adapter_id or preset[1]
    _validate_adapter(adapter_id)
    key = (payload.api_key or "").strip()
    instance = ProviderInstance(
        user_id=user_id,
        preset_id=payload.preset_id.strip().lower(),
        display_name=payload.display_name.strip(),
        default_adapter_id=adapter_id,
        default_model_id=payload.default_model_id,
        base_url=normalize_anthropic_base_url(payload.base_url) if adapter_id == "anthropic_messages" else normalize_base_url(payload.base_url),
        credentials_encrypted_json=encrypt_text(json.dumps({"api_key": key})) if key else None,
        credential_hint=key[-4:] if key else None,
        settings_json=json.dumps(payload.settings or {}),
        is_default=not bool(await session.scalar(select(ProviderInstance.id).where(ProviderInstance.user_id == user_id))),
    )
    session.add(instance)
    await session.commit()
    await session.refresh(instance)
    return instance


async def update_provider(session: AsyncSession, user_id: int, provider_id: int, payload: ProviderUpdateRequest) -> ProviderInstance:
    instance = await get_provider(session, user_id, provider_id)
    data = payload.model_dump(exclude_unset=True)
    if "default_adapter_id" in data and data["default_adapter_id"]:
        _validate_adapter(data["default_adapter_id"])
        instance.default_adapter_id = data.pop("default_adapter_id")
    if "api_key" in data:
        key = (data.pop("api_key") or "").strip()
        if key:
            instance.credentials_encrypted_json = encrypt_text(json.dumps({"api_key": key}))
            instance.credential_hint = key[-4:]
    if "settings" in data:
        instance.settings_json = json.dumps(data.pop("settings") or {})
    if "base_url" in data:
        instance.base_url = normalize_base_url(data.pop("base_url"))
    for key, value in data.items():
        setattr(instance, key, value)
    if payload.is_default:
        await session.execute(ProviderInstance.__table__.update().where(ProviderInstance.user_id == user_id).values(is_default=False))
        instance.is_default = True
    await session.commit()
    await session.refresh(instance)
    return instance


async def delete_provider(session: AsyncSession, user_id: int, provider_id: int) -> None:
    instance = await get_provider(session, user_id, provider_id)
    await session.delete(instance)
    await session.commit()


def _api_key_proxy(instance: ProviderInstance):
    key = _credentials(instance)
    if not key:
        raise AppError(status_code=400, code="MISSING_API_KEY", message="请先配置凭据")
    return SimpleNamespace(key_encrypted=encrypt_text(key), base_url=instance.base_url, provider=instance.preset_id)


async def get_generation_connection(session: AsyncSession, user_id: int, provider_id: int):
    """Return the connection metadata used by the selected provider adapter.

    Provider adapters share the encrypted-key and base-URL transport seam so
    provider configuration remains independent from the request protocol.
    """
    instance = await get_provider(session, user_id, provider_id)
    if not instance.enabled:
        raise AppError(status_code=409, code="PROVIDER_DISABLED", message="服务商已禁用")
    return instance, _api_key_proxy(instance)


async def test_provider(session: AsyncSession, user_id: int, provider_id: int) -> tuple[ProviderInstance, bool, str]:
    instance = await get_provider(session, user_id, provider_id)
    proxy = _api_key_proxy(instance)
    if instance.default_adapter_id == "anthropic_messages":
        success, message = await test_anthropic_key(api_key=proxy)
    else:
        success, message = await test_openai_key(api_key=proxy)
    instance.last_tested_at = datetime.now(timezone.utc).replace(tzinfo=None)
    instance.last_test_status = "success" if success else "failed"
    instance.last_test_message = message
    await session.commit()
    await session.refresh(instance)
    return instance, success, message


async def list_models(session: AsyncSession, user_id: int, provider_id: int) -> list[ProviderModel]:
    await get_provider(session, user_id, provider_id)
    result = await session.scalars(select(ProviderModel).where(ProviderModel.provider_instance_id == provider_id).order_by(ProviderModel.model_id))
    return list(result.all())


async def sync_models(session: AsyncSession, user_id: int, provider_id: int) -> list[ProviderModel]:
    instance = await get_provider(session, user_id, provider_id)
    proxy = _api_key_proxy(instance)
    if instance.default_adapter_id == "anthropic_messages":
        remote = await list_anthropic_models(api_key=proxy)
    else:
        remote = await list_openai_models(api_key=proxy)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    seen: set[str] = set()
    for item in remote:
        model_id = str(item.get("id"))
        if not model_id:
            continue
        seen.add(model_id)
        model = await session.scalar(select(ProviderModel).where(ProviderModel.provider_instance_id == provider_id, ProviderModel.model_id == model_id))
        remote_display_name = (
            item.get("owned_by")
            if instance.default_adapter_id == "anthropic_messages"
            else item.get("display_name") or model_id
        )
        if model is None:
            model = ProviderModel(provider_instance_id=provider_id, model_id=model_id, remote_display_name=remote_display_name, last_seen_at=now)
            session.add(model)
        else:
            model.remote_display_name = remote_display_name
            model.remote_available = True
            model.last_seen_at = now
    existing = await session.scalars(select(ProviderModel).where(ProviderModel.provider_instance_id == provider_id))
    for model in existing:
        if model.model_id not in seen and not model.is_manual:
            model.remote_available = False
    await session.commit()
    return await list_models(session, user_id, provider_id)


async def add_model(session: AsyncSession, user_id: int, provider_id: int, payload: ProviderModelCreateRequest) -> ProviderModel:
    await get_provider(session, user_id, provider_id)
    existing = await session.scalar(select(ProviderModel).where(ProviderModel.provider_instance_id == provider_id, ProviderModel.model_id == payload.model_id))
    if existing:
        raise AppError(status_code=409, code="CONFLICT", message="模型已存在")
    _validate_adapter(payload.adapter_override) if payload.adapter_override else None
    model = ProviderModel(provider_instance_id=provider_id, model_id=payload.model_id.strip(), display_name_override=payload.display_name_override, adapter_override=payload.adapter_override, enabled=payload.enabled, is_manual=True)
    session.add(model)
    await session.commit()
    await session.refresh(model)
    return model


async def update_model(session: AsyncSession, user_id: int, provider_id: int, model_id: str, payload: ProviderModelUpdateRequest) -> ProviderModel:
    await get_provider(session, user_id, provider_id)
    model = await session.scalar(select(ProviderModel).where(ProviderModel.provider_instance_id == provider_id, ProviderModel.model_id == model_id))
    if model is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="模型不存在")
    if payload.adapter_override:
        _validate_adapter(payload.adapter_override)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(model, key, value)
    await session.commit()
    await session.refresh(model)
    return model


async def delete_model(session: AsyncSession, user_id: int, provider_id: int, model_id: str) -> None:
    await get_provider(session, user_id, provider_id)
    model = await session.scalar(select(ProviderModel).where(ProviderModel.provider_instance_id == provider_id, ProviderModel.model_id == model_id))
    if model is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="模型不存在")
    await session.delete(model)
    await session.commit()
