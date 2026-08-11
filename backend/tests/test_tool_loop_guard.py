from __future__ import annotations

import unittest

from app.core.exceptions import AppError
from app.providers.anthropic import DEFAULT_MAX_TOOL_ROUND_TRIPS as ANTHROPIC_MAX_TOOL_ROUNDS
from app.providers.openai import DEFAULT_MAX_TOOL_ROUND_TRIPS, ToolCallLoopGuard


class ToolCallLoopGuardTests(unittest.TestCase):
    def test_default_tool_round_limit_is_32_for_all_supported_adapters(self) -> None:
        self.assertEqual(DEFAULT_MAX_TOOL_ROUND_TRIPS, 32)
        self.assertEqual(ANTHROPIC_MAX_TOOL_ROUNDS, 32)

    def test_rejects_fourth_consecutive_identical_call(self) -> None:
        guard = ToolCallLoopGuard()
        for _ in range(3):
            guard.observe("lookup", '{"query":"same"}')

        with self.assertRaises(AppError) as raised:
            guard.observe("lookup", '{"query":"same"}')

        self.assertEqual(raised.exception.code, "MODEL_ERROR")

    def test_different_call_resets_the_repeat_counter(self) -> None:
        guard = ToolCallLoopGuard()
        for _ in range(3):
            guard.observe("lookup", '{"query":"same"}')
        guard.observe("lookup", '{"query":"different"}')
        guard.observe("lookup", '{"query":"same"}')


if __name__ == "__main__":
    unittest.main()
