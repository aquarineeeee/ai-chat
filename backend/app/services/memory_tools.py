from __future__ import annotations

import json
import logging
from typing import Any

from app.services.memory_mcp import NO_MEMORY_RESULTS, _normalize_memory_text, search_memory


logger = logging.getLogger(__name__)
MEMORY_SEARCH_TOOL_NAME = "memory_search"
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


def memory_search_tool_definition() -> dict[str, Any]:
    return json.loads(json.dumps(MEMORY_SEARCH_TOOL_DEFINITION))


def memory_search_tools() -> list[dict[str, Any]]:
    return [memory_search_tool_definition()]


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


async def execute_memory_tool_call(tool_name: str, arguments_json: str) -> str:
    if tool_name != MEMORY_SEARCH_TOOL_NAME:
        return _tool_error(f"未知工具 {tool_name!r}")

    try:
        payload = _parse_tool_arguments(arguments_json)
    except ValueError as exc:
        return _tool_error(str(exc))

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
