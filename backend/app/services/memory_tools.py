from __future__ import annotations

import json
import logging
from typing import Any

from app.services.memory_mcp import NO_MEMORY_RESULTS, _normalize_memory_text, grow_memory, pulse_memory, search_memory, update_memory, write_memory


logger = logging.getLogger(__name__)
MEMORY_SEARCH_TOOL_NAME = "memory_search"
MEMORY_PULSE_TOOL_NAME = "memory_pulse"
MEMORY_WRITE_TOOL_NAME = "memory_write"
MEMORY_GROW_TOOL_NAME = "memory_grow"
MEMORY_UPDATE_TOOL_NAME = "memory_update"
TOOL_ERROR_PREFIX = "工具执行失败："

MEMORY_SEARCH_TOOL_DEFINITION: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": MEMORY_SEARCH_TOOL_NAME,
        "description": "检索与当前用户相关的长期记忆。仅在问题依赖跨会话偏好、历史约束或长期背景时调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "用于检索长期记忆的简洁查询语句，允许留空以触发自动浮现。",
                },
                "max_tokens": {
                    "type": "integer",
                    "description": "breath 最多返回的 token 数，默认 10000。",
                },
                "domain": {
                    "type": "string",
                    "description": "逗号分隔的记忆领域筛选。",
                },
                "valence": {
                    "type": "number",
                    "description": "情绪效价，-1 表示忽略，0~1 表示过滤。",
                },
                "arousal": {
                    "type": "number",
                    "description": "情绪唤醒，-1 表示忽略，0~1 表示过滤。",
                },
                "max_results": {
                    "type": "integer",
                    "description": "最多返回记忆数，默认 20，最大 50。",
                },
                "importance_min": {
                    "type": "integer",
                    "description": "最低重要度，-1 表示忽略。",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    },
}

MEMORY_WRITE_TOOL_DEFINITION: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": MEMORY_WRITE_TOOL_NAME,
        "description": "写入一条长期记忆。仅在信息值得跨会话保留时调用，content 应为提炼后的单条记忆，而不是整段对话转录。",
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
                    "description": "情绪效价，-1 有效。",
                },
                "arousal": {
                    "type": "number",
                    "description": "情绪唤醒，-1 有效。",
                },
            },
            "required": ["content"],
            "additionalProperties": False,
        },
    },
}

MEMORY_PULSE_TOOL_DEFINITION: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": MEMORY_PULSE_TOOL_NAME,
        "description": "Inspect memory system status and list current memory buckets.",
        "parameters": {
            "type": "object",
            "properties": {
                "include_archive": {
                    "type": "boolean",
                    "description": "Whether to include archived memories.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    },
}

MEMORY_GROW_TOOL_DEFINITION: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": MEMORY_GROW_TOOL_NAME,
        "description": "Import a longer note and let the memory service split it into multiple long-term memories.",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The long-form text to import.",
                },
            },
            "required": ["content"],
            "additionalProperties": False,
        },
    },
}

MEMORY_UPDATE_TOOL_DEFINITION: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": MEMORY_UPDATE_TOOL_NAME,
        "description": "更新或删除已有长期记忆。用于修正文案、标签、领域、重要度，或标记 resolved、pinned、digested。",
        "parameters": {
            "type": "object",
            "properties": {
                "bucket_id": {
                    "type": "string",
                    "description": "要更新的记忆桶 ID，必填。",
                },
                "name": {
                    "type": "string",
                    "description": "新的标题或名称。",
                },
                "domain": {
                    "type": "string",
                    "description": "逗号分隔的领域字符串。",
                },
                "valence": {
                    "type": "number",
                    "description": "情绪效价，-1 表示不改，0~1 表示更新。",
                },
                "arousal": {
                    "type": "number",
                    "description": "情绪唤醒，-1 表示不改，0~1 表示更新。",
                },
                "importance": {
                    "type": "integer",
                    "description": "重要度，-1 表示不改。",
                },
                "tags": {
                    "type": "string",
                    "description": "逗号分隔的标签字符串。",
                },
                "resolved": {
                    "type": "integer",
                    "description": "1 设为已解决，0 取消，-1 不改。",
                },
                "pinned": {
                    "type": "integer",
                    "description": "1 设为钉选，0 取消，-1 不改。",
                },
                "digested": {
                    "type": "integer",
                    "description": "1 设为已消化，0 取消，-1 不改。",
                },
                "content": {
                    "type": "string",
                    "description": "更新后的正文。",
                },
                "delete": {
                    "type": "boolean",
                    "description": "是否删除该记忆。",
                },
            },
            "required": ["bucket_id"],
            "additionalProperties": False,
        },
    },
}


def memory_search_tool_definition() -> dict[str, Any]:
    return json.loads(json.dumps(MEMORY_SEARCH_TOOL_DEFINITION))


def memory_write_tool_definition() -> dict[str, Any]:
    return json.loads(json.dumps(MEMORY_WRITE_TOOL_DEFINITION))


def memory_pulse_tool_definition() -> dict[str, Any]:
    return json.loads(json.dumps(MEMORY_PULSE_TOOL_DEFINITION))


def memory_update_tool_definition() -> dict[str, Any]:
    return json.loads(json.dumps(MEMORY_UPDATE_TOOL_DEFINITION))


def memory_grow_tool_definition() -> dict[str, Any]:
    return json.loads(json.dumps(MEMORY_GROW_TOOL_DEFINITION))


def memory_tool_definitions(*, include_grow: bool = False, include_pulse: bool = False) -> list[dict[str, Any]]:
    tools = [
        memory_search_tool_definition(),
    ]
    if include_pulse:
        tools.append(memory_pulse_tool_definition())
    tools.extend([
        memory_write_tool_definition(),
    ])
    if include_grow:
        tools.append(memory_grow_tool_definition())
    tools.append(memory_update_tool_definition())
    return tools


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


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_optional_unit_interval(value: float) -> bool:
    return value == -1 or 0 <= value <= 1


def _is_optional_trace_toggle(value: int) -> bool:
    return value in {-1, 0, 1}


async def execute_memory_tool_call(tool_name: str, arguments_json: str) -> str:
    try:
        payload = _parse_tool_arguments(arguments_json)
    except ValueError as exc:
        return _tool_error(str(exc))

    if tool_name == MEMORY_SEARCH_TOOL_NAME:
        query = payload.get("query", "")
        if not isinstance(query, str):
            return _tool_error("query 必须是字符串")

        max_tokens = payload.get("max_tokens", 10000)
        if not _is_int(max_tokens) or max_tokens <= 0:
            return _tool_error("max_tokens 必须是正整数")

        domain = payload.get("domain", "")
        if not isinstance(domain, str):
            return _tool_error("domain 必须是字符串")

        valence = payload.get("valence", -1)
        if not _is_number(valence):
            return _tool_error("valence 必须是数字")
        valence = float(valence)
        if not _is_optional_unit_interval(valence):
            return _tool_error("valence 必须为 -1 或 0~1")

        arousal = payload.get("arousal", -1)
        if not _is_number(arousal):
            return _tool_error("arousal 必须是数字")
        arousal = float(arousal)
        if not _is_optional_unit_interval(arousal):
            return _tool_error("arousal 必须为 -1 或 0~1")

        max_results = payload.get("max_results", 20)
        if not _is_int(max_results) or not 1 <= max_results <= 50:
            return _tool_error("max_results 必须是 1~50 的整数")

        importance_min = payload.get("importance_min", -1)
        if not _is_int(importance_min) or importance_min < -1:
            return _tool_error("importance_min 必须是大于等于 -1 的整数")

        cleaned_query = _normalize_memory_text(query)
        cleaned_domain = _normalize_memory_text(domain)

        try:
            result = await search_memory(
                query=cleaned_query,
                max_tokens=max_tokens,
                domain=cleaned_domain,
                valence=valence,
                arousal=arousal,
                max_results=max_results,
                importance_min=importance_min,
            )
        except Exception:
            logger.exception("Memory search tool execution failed for query=%r", cleaned_query)
            return _tool_error("记忆检索异常")

        if result:
            return result
        return next(iter(NO_MEMORY_RESULTS))

    if tool_name == MEMORY_PULSE_TOOL_NAME:
        include_archive = payload.get("include_archive", False)
        if not isinstance(include_archive, bool):
            return _tool_error("include_archive 必须是布尔值")

        try:
            return await pulse_memory(include_archive=include_archive)
        except Exception:
            logger.exception("Memory pulse tool execution failed with include_archive=%r", include_archive)
            return _tool_error("记忆状态查询异常")

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
        if not _is_int(importance):
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

    if tool_name == MEMORY_GROW_TOOL_NAME:
        content = payload.get("content")
        if not isinstance(content, str):
            return _tool_error("content 蹇呴』鏄瓧绗︿覆")

        cleaned_content = _normalize_memory_text(content)
        if not cleaned_content:
            return _tool_error("content 涓嶈兘涓虹┖")

        try:
            return await grow_memory(content=cleaned_content)
        except Exception:
            logger.exception("Memory grow tool execution failed for content=%r", cleaned_content)
            return _tool_error("Memory import failed")

    if tool_name == MEMORY_UPDATE_TOOL_NAME:
        bucket_id = payload.get("bucket_id")
        if not isinstance(bucket_id, str):
            return _tool_error("bucket_id 必须是字符串")

        cleaned_bucket_id = _normalize_memory_text(bucket_id)
        if not cleaned_bucket_id:
            return _tool_error("bucket_id 不能为空")

        name = payload.get("name", "")
        if not isinstance(name, str):
            return _tool_error("name 必须是字符串")

        domain = payload.get("domain", "")
        if not isinstance(domain, str):
            return _tool_error("domain 必须是字符串")

        valence = payload.get("valence", -1)
        if not _is_number(valence):
            return _tool_error("valence 必须是数字")
        valence = float(valence)
        if not _is_optional_unit_interval(valence):
            return _tool_error("valence 必须为 -1 或 0~1")

        arousal = payload.get("arousal", -1)
        if not _is_number(arousal):
            return _tool_error("arousal 必须是数字")
        arousal = float(arousal)
        if not _is_optional_unit_interval(arousal):
            return _tool_error("arousal 必须为 -1 或 0~1")

        importance = payload.get("importance", -1)
        if not _is_int(importance) or importance < -1:
            return _tool_error("importance 必须是大于等于 -1 的整数")

        tags = payload.get("tags", "")
        if not isinstance(tags, str):
            return _tool_error("tags 必须是字符串")

        resolved = payload.get("resolved", -1)
        if not _is_int(resolved) or not _is_optional_trace_toggle(resolved):
            return _tool_error("resolved 必须为 -1、0 或 1")

        pinned = payload.get("pinned", -1)
        if not _is_int(pinned) or not _is_optional_trace_toggle(pinned):
            return _tool_error("pinned 必须为 -1、0 或 1")

        digested = payload.get("digested", -1)
        if not _is_int(digested) or not _is_optional_trace_toggle(digested):
            return _tool_error("digested 必须为 -1、0 或 1")

        content = payload.get("content", "")
        if not isinstance(content, str):
            return _tool_error("content 必须是字符串")

        delete = payload.get("delete", False)
        if not isinstance(delete, bool):
            return _tool_error("delete 必须是布尔值")

        try:
            return await update_memory(
                bucket_id=cleaned_bucket_id,
                name=_normalize_memory_text(name),
                domain=_normalize_memory_text(domain),
                valence=valence,
                arousal=arousal,
                importance=importance,
                tags=_normalize_memory_text(tags),
                resolved=resolved,
                pinned=pinned,
                digested=digested,
                content=_normalize_memory_text(content),
                delete=delete,
            )
        except Exception:
            logger.exception("Memory update tool execution failed for bucket_id=%r", cleaned_bucket_id)
            return _tool_error("记忆更新异常")

    return _tool_error(f"未知工具 {tool_name!r}")
