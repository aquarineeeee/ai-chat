from __future__ import annotations

from app.core.exceptions import AppError
from app.models.conversation import Conversation


def generate_mock_reply(
    *,
    conversation: Conversation,
    content: str,
    model: str | None,
) -> str:
    normalized = content.strip()
    if not normalized:
        raise AppError(status_code=422, code="VALIDATION_ERROR", message="消息内容不能为空")

    if normalized.lower().startswith("/fail"):
        raise AppError(status_code=502, code="MODEL_ERROR", message="Mock provider simulated a failure")

    prompt_note = f" System prompt is set ({len(conversation.system_prompt)} chars)." if conversation.system_prompt else ""
    return (
        f"Mock reply from {conversation.provider or 'mock'}"
        f"/{model or conversation.model or 'mock-model'}: "
        f"{normalized}{prompt_note}"
    )
