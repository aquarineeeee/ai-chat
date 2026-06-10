from __future__ import annotations

import json
import logging
from typing import Any

from app.services.memory_mcp import NO_MEMORY_RESULTS, _normalize_memory_text, search_memory, write_memory


logger = logging.getLogger(__name__)
MEMORY_SEARCH_TOOL_NAME = "memory_search"
MEMORY_WRITE_TOOL_NAME = "memory_write"
TOOL_ERROR_PREFIX = "工具执行失败："

MEMORY_SEARCH_TOOL_DEFINITION: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": MEMORY_SEARCH_TOOL_NAME,
        "description": (
            "检索与当前用户相关的长期记忆。仅在问题依赖跨会话偏好、历史约束或长期背景时调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "用于检索长期记忆的简洁查询语句。",
                }
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}

MEMORY_WRITE_TOOL_DEFINITION: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": MEMORY_WRITE_TOOL_NAME,
        "description": (
            "写入一条长期记忆。仅在信息值得跨会话保留时调用，content 应为提炼后的单条记忆，而不是整段对话转录。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "记忆正文，必填。",
                },
                "tags": {
                    "type": "string",
                    "description": "逗号分隔的标签字符串。",
                },
                "importance": {
                    "type": "integer",
                    "description": "重要度，通常为 1-10。",
                },
                "pinned": {
                    "type": "boolean",
                    "description": "是否钉选为永久记忆。",
                },
                "feel": {
                    "type": "boolean",
                    "description": "是否作为 feel 类型写入。",
                },
                "source_bucket": {
                    "type": "string",
                    "description": "源记忆桶 ID，通常在 feel=true 时使用。",
                },
                "valence": {
                    "type": "number",
                    "description": "情绪效价，0-1 有效。",
                },
                "arousal": {
                    "type": "number",
                    "description": "情绪唤醒，0-1 有效。",
                },
            },
            "required": ["content"],
            "additionalProperties": False,
        },
    },
}


def memory_search_tool_definition() -> dict[str, Any]:
    return json.loads(json.dumps(MEMORY_SEARCH_TOOL_DEFINITION))


def memory_write_tool_definition() -> dict[str, Any]:
    return json.loads(json.dumps(MEMORY_WRITE_TOOL_DEFINITION))


def memory_tool_definitions() -> list[dict[str, Any]]:
    return [
        memory_search_tool_definition(),
        memory_write_tool_definition(),
    ]


def memory_search_tools() -> list[dict[str, Any]]:
    return memory_tool_definitions()


def _tool_error(message: str) -> str:
    return f"{TOOL_ERROR_PREFIX}{message}"


def _parse_tool_arguments(arguments_json: str) -> dict[str, Any]:
    try:
        payload = json.loads(arguments_json or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("参数不是合法 JSON") from exc

    if not isinstance(payload, dict):
        raise ValueError("参数必须是对象")
    return payload


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


async def execute_memory_tool_call(tool_name: str, arguments_json: str) -> str:
    try:
        payload = _parse_tool_arguments(arguments_json)
    except ValueError as exc:
        return _tool_error(str(exc))

    if tool_name == MEMORY_SEARCH_TOOL_NAME:
        query = payload.get("query")
        if not isinstance(query, str):
            return _tool_error("query 必须是字符串")

        cleaned_query = _normalize_memory_text(query)
        if not cleaned_query:
            return _tool_error("query 不能为空")

        try:
            result = await search_memory(query=cleaned_query)
        except Exception:
            logger.exception("Memory search tool execution failed for query=%r", cleaned_query)
            return _tool_error("记忆检索异常")

        if result:
            return result
        return next(iter(NO_MEMORY_RESULTS))

    if tool_name == MEMORY_WRITE_TOOL_NAME:
        content = payload.get("content")
        if not isinstance(content, str):
            return _tool_error("content 必须是字符串")

        cleaned_content = _normalize_memory_text(content)
        if not cleaned_content:
            return _tool_error("content 不能为空")

        tags = payload.get("tags", "")
        if not isinstance(tags, str):
            return _tool_error("tags 必须是字符串")

        importance = payload.get("importance", 5)
        if not isinstance(importance, int) or isinstance(importance, bool):
            return _tool_error("importance 必须是整数")

        pinned = payload.get("pinned", False)
        if not isinstance(pinned, bool):
            return _tool_error("pinned 必须是布尔值")

        feel = payload.get("feel", False)
        if not isinstance(feel, bool):
            return _tool_error("feel 必须是布尔值")

        source_bucket = payload.get("source_bucket", "")
        if not isinstance(source_bucket, str):
            return _tool_error("source_bucket 必须是字符串")

        valence = payload.get("valence", -1)
        if not _is_number(valence):
            return _tool_error("valence 必须是数字")

        arousal = payload.get("arousal", -1)
        if not _is_number(arousal):
            return _tool_error("arousal 必须是数字")

        try:
            return await write_memory(
                content=cleaned_content,
                tags=_normalize_memory_text(tags),
                importance=importance,
                pinned=pinned,
                feel=feel,
                source_bucket=_normalize_memory_text(source_bucket),
                valence=float(valence),
                arousal=float(arousal),
            )
        except Exception:
            logger.exception("Memory write tool execution failed for content=%r", cleaned_content)
            return _tool_error("记忆写入异常")

    return _tool_error(f"未知工具 {tool_name!r}")
