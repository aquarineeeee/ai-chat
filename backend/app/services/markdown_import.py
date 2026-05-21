from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import re

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole, MessageStatus


MAX_IMPORT_FILE_SIZE = 5 * 1024 * 1024
MAX_IMPORT_MESSAGES = 500
IMPORT_FALLBACK_TITLE = "导入的对话"
NON_INITIAL_SYSTEM_WARNING = "检测到非开头的 system 消息，已合并到 system_prompt"
CONSECUTIVE_ROLE_WARNING = "检测到连续 {role} 消息，已按原顺序导入"

HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*$")
BOLD_ROLE_RE = re.compile(r"^\s*\*\*(.+?)\*\*\s*:?\s*(.*)$")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")

ROLE_PATTERNS: list[tuple[re.Pattern[str], MessageRole]] = [
    (re.compile(r"^(?:user|human)(?:\s*(?:[-—–|(:：（].*)?)?$", re.IGNORECASE), MessageRole.USER),
    (re.compile(r"^(?:assistant|ai|chatgpt)(?:\s*(?:[-—–|(:：（].*)?)?$", re.IGNORECASE), MessageRole.ASSISTANT),
    (re.compile(r"^(?:system)(?:\s*(?:[-—–|(:：（].*)?)?$", re.IGNORECASE), MessageRole.SYSTEM),
    (re.compile(r"^(?:用户|我)(?:\s*(?:[-—–|(:：（].*)?)?$"), MessageRole.USER),
    (re.compile(r"^(?:助手)(?:\s*(?:[-—–|(:：（].*)?)?$"), MessageRole.ASSISTANT),
    (re.compile(r"^(?:系统)(?:\s*(?:[-—–|(:：（].*)?)?$"), MessageRole.SYSTEM),
]

settings = get_settings()


@dataclass(slots=True)
class ParsedImportMessage:
    role: MessageRole
    content: str


@dataclass(slots=True)
class ParsedMarkdownConversation:
    title: str
    system_prompt: str | None
    messages: list[ParsedImportMessage]
    warnings: list[str]
    ignored_count: int


async def import_markdown_conversation(
    *,
    session: AsyncSession,
    user_id: int,
    filename: str,
    file_bytes: bytes,
) -> dict[str, object]:
    decoded = decode_markdown_bytes(filename=filename, file_bytes=file_bytes)
    parsed = parse_markdown_conversation(decoded, filename=filename)
    conversation: Conversation | None = None

    try:
        conversation = Conversation(
            user_id=user_id,
            title=parsed.title,
            system_prompt=parsed.system_prompt,
            provider=settings.default_provider,
            model=settings.default_model,
            temperature=Decimal(str(settings.default_temperature)),
            max_tokens=settings.default_max_tokens,
        )
        session.add(conversation)
        await session.flush()

        parent_id: int | None = None
        for item in parsed.messages:
            message = Message(
                conversation_id=conversation.id,
                parent_id=parent_id,
                role=item.role,
                content=item.content,
                status=MessageStatus.COMPLETED,
            )
            session.add(message)
            await session.flush()
            parent_id = message.id

        conversation.current_leaf_message_id = parent_id
        await session.commit()
    except SQLAlchemyError as exc:
        await session.rollback()
        raise AppError(status_code=500, code="DATABASE_WRITE_FAILED", message="数据库写入失败") from exc
    except Exception:
        await session.rollback()
        raise

    if conversation is None:
        raise AppError(status_code=500, code="INTERNAL_ERROR", message="导入会话创建失败")

    await session.refresh(conversation)
    return {
        "conversation": conversation,
        "message_count": len(parsed.messages),
        "ignored_count": parsed.ignored_count,
        "warnings": parsed.warnings,
    }


def decode_markdown_bytes(*, filename: str, file_bytes: bytes) -> str:
    validate_import_file(filename=filename, file_bytes=file_bytes)

    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue

    raise AppError(status_code=422, code="INVALID_ENCODING", message="无法识别文件编码，请使用 UTF-8 或 GBK")


def validate_import_file(*, filename: str, file_bytes: bytes) -> None:
    suffix = Path(filename).suffix.lower()
    if suffix not in {".md", ".markdown"}:
        raise AppError(status_code=422, code="VALIDATION_ERROR", message="只能导入 .md 或 .markdown 文件")
    if not file_bytes:
        raise AppError(status_code=422, code="EMPTY_FILE", message="导入文件为空")
    if len(file_bytes) > MAX_IMPORT_FILE_SIZE:
        raise AppError(status_code=413, code="FILE_TOO_LARGE", message="导入文件不能超过 5MB")


def parse_markdown_conversation(content: str, *, filename: str) -> ParsedMarkdownConversation:
    title: str | None = None
    system_blocks: list[str] = []
    messages: list[ParsedImportMessage] = []
    warnings: list[str] = []
    ignored_count = 0
    pending_role: MessageRole | None = None
    pending_lines: list[str] = []
    seen_non_system_message = False
    in_fence = False
    fence_marker = ""
    fence_length = 0

    def add_warning(message: str) -> None:
        if message not in warnings:
            warnings.append(message)

    def finalize_pending() -> None:
        nonlocal ignored_count, pending_lines, pending_role, seen_non_system_message

        if pending_role is None:
            return

        body = "\n".join(pending_lines).strip()
        role = pending_role
        pending_role = None
        pending_lines = []

        if not body:
            ignored_count += 1
            return

        if role == MessageRole.SYSTEM:
            if seen_non_system_message:
                add_warning(NON_INITIAL_SYSTEM_WARNING)
            system_blocks.append(body)
            return

        messages.append(ParsedImportMessage(role=role, content=body))
        seen_non_system_message = True

    for raw_line in content.splitlines():
        stripped = raw_line.strip()

        if in_fence:
            if _is_fence_close(raw_line, fence_marker=fence_marker, fence_length=fence_length):
                in_fence = False
                fence_marker = ""
                fence_length = 0
            if pending_role is not None:
                pending_lines.append(raw_line)
            continue

        fence = _fence_token(raw_line)
        if fence is not None:
            in_fence = True
            fence_marker = fence[0]
            fence_length = len(fence)
            if pending_role is not None:
                pending_lines.append(raw_line)
            continue

        heading = HEADING_RE.match(raw_line)
        if heading and len(heading.group(1)) == 1 and title is None and pending_role is None:
            title = heading.group(2).strip()
            continue

        role, inline_content = _match_role_separator(raw_line)
        if role is not None:
            finalize_pending()
            pending_role = role
            pending_lines = [inline_content] if inline_content else []
            continue

        if pending_role is not None:
            pending_lines.append(raw_line)
        elif stripped:
            continue

    finalize_pending()

    if not messages:
        raise AppError(status_code=422, code="INVALID_MARKDOWN", message="没有识别到有效的 user 或 assistant 消息")
    if len(messages) > MAX_IMPORT_MESSAGES:
        raise AppError(status_code=422, code="MESSAGE_LIMIT_EXCEEDED", message="导入消息数量不能超过 500 条")

    for previous, current in zip(messages, messages[1:]):
        if previous.role == current.role:
            add_warning(CONSECUTIVE_ROLE_WARNING.format(role=current.role.value))

    resolved_title = _resolve_title(title=title, filename=filename, messages=messages)
    system_prompt = "\n\n".join(system_blocks) if system_blocks else None
    return ParsedMarkdownConversation(
        title=resolved_title,
        system_prompt=system_prompt,
        messages=messages,
        warnings=warnings,
        ignored_count=ignored_count,
    )


def _match_role_separator(line: str) -> tuple[MessageRole | None, str | None]:
    heading = HEADING_RE.match(line)
    if heading and len(heading.group(1)) >= 2:
        return _normalize_role(heading.group(2)), None

    bold = BOLD_ROLE_RE.match(line)
    if not bold:
        return None, None

    role = _normalize_role(bold.group(1))
    if role is None:
        return None, None

    inline_content = bold.group(2).strip()
    return role, inline_content or None


def _normalize_role(label: str) -> MessageRole | None:
    cleaned = re.sub(r"\s+", " ", label.strip().rstrip(":：")).strip()
    for pattern, role in ROLE_PATTERNS:
        if pattern.match(cleaned):
            return role
    return None


def _resolve_title(
    *,
    title: str | None,
    filename: str,
    messages: list[ParsedImportMessage],
) -> str:
    for candidate in (
        title,
        Path(filename).stem,
        _user_title_candidate(messages),
        IMPORT_FALLBACK_TITLE,
    ):
        if candidate:
            return candidate[:255]
    return IMPORT_FALLBACK_TITLE


def _user_title_candidate(messages: list[ParsedImportMessage]) -> str | None:
    for message in messages:
        if message.role != MessageRole.USER:
            continue
        compact = " ".join(message.content.split())
        if compact:
            return compact[:40]
    return None


def _fence_token(line: str) -> str | None:
    match = FENCE_RE.match(line)
    if not match:
        return None
    return match.group(1)


def _is_fence_close(line: str, *, fence_marker: str, fence_length: int) -> bool:
    stripped = line.lstrip()
    if not stripped.startswith(fence_marker * fence_length):
        return False
    return stripped[: fence_length + 1] == fence_marker * fence_length or stripped.startswith(fence_marker * fence_length)
