from __future__ import annotations

import unittest

from app.core.exceptions import AppError
from app.models.message import MessageRole
from app.services.markdown_import import parse_markdown_conversation


class MarkdownImportParserTests(unittest.TestCase):
    def test_parse_standard_markdown_conversation(self) -> None:
        content = """# 示例对话

## System
你是一个有帮助的助手。

## User
你好

## Assistant
你好，有什么可以帮你？
"""

        parsed = parse_markdown_conversation(content, filename="ignored.md")

        self.assertEqual(parsed.title, "示例对话")
        self.assertEqual(parsed.system_prompt, "你是一个有帮助的助手。")
        self.assertEqual([item.role for item in parsed.messages], [MessageRole.USER, MessageRole.ASSISTANT])
        self.assertEqual(parsed.messages[0].content, "你好")
        self.assertEqual(parsed.messages[1].content, "你好，有什么可以帮你？")
        self.assertEqual(parsed.warnings, [])
        self.assertEqual(parsed.ignored_count, 0)


    def test_parse_chinese_roles_and_code_fences(self) -> None:
        content = """## 系统
请用简洁的中文回答。

## 用户
下面这段内容只是代码：

```md
## Assistant
这里不能被识别成新消息。
```

### 助手
明白。

### 助手
补充一下。
"""

        parsed = parse_markdown_conversation(content, filename="history.markdown")

        self.assertEqual(parsed.title, "history")
        self.assertEqual(parsed.system_prompt, "请用简洁的中文回答。")
        self.assertEqual(
            [item.role for item in parsed.messages],
            [MessageRole.USER, MessageRole.ASSISTANT, MessageRole.ASSISTANT],
        )
        self.assertIn("## Assistant", parsed.messages[0].content)
        self.assertEqual(parsed.warnings, ["检测到连续 assistant 消息，已按原顺序导入"])


    def test_parse_merges_non_initial_system_and_ignores_empty_blocks(self) -> None:
        content = """# Title

## User
first

## System

## Assistant
second

## System
later prompt
"""

        parsed = parse_markdown_conversation(content, filename="ignored.md")

        self.assertEqual(parsed.title, "Title")
        self.assertEqual(parsed.system_prompt, "later prompt")
        self.assertEqual([item.role for item in parsed.messages], [MessageRole.USER, MessageRole.ASSISTANT])
        self.assertEqual(parsed.ignored_count, 1)
        self.assertEqual(parsed.warnings, ["检测到非开头的 system 消息，已合并到 system_prompt"])


    def test_parse_requires_valid_messages(self) -> None:
        content = """## System
only system
"""

        with self.assertRaises(AppError) as exc_info:
            parse_markdown_conversation(content, filename="only-system.md")

        self.assertEqual(exc_info.exception.code, "INVALID_MARKDOWN")


    def test_parse_rejects_too_many_messages(self) -> None:
        content = "\n\n".join(f"## User\nmessage {index}" for index in range(501))

        with self.assertRaises(AppError) as exc_info:
            parse_markdown_conversation(content, filename="too-many.md")

        self.assertEqual(exc_info.exception.code, "MESSAGE_LIMIT_EXCEEDED")


if __name__ == "__main__":
    unittest.main()
