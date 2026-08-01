from __future__ import annotations

import asyncio
import unittest

from app.services.approval_manager import ApprovalDecision, InProcessApprovalManager


class ApprovalManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_register_wait_and_resolve_round_trip(self) -> None:
        manager = InProcessApprovalManager()
        manager.register(run_id=7, tool_call_ref="tc_1")

        async def wait_for_result():
            return await manager.wait_for_decision(run_id=7, tool_call_ref="tc_1")

        task = asyncio.create_task(wait_for_result())
        await asyncio.sleep(0)
        resolved = manager.resolve(
            run_id=7,
            tool_call_ref="tc_1",
            decision=ApprovalDecision(approved=True, reviewer_id=9, comment="ok"),
        )
        result = await task

        self.assertTrue(resolved)
        self.assertTrue(result.approved)
        self.assertEqual(result.reviewer_id, 9)
        self.assertEqual(result.comment, "ok")

    async def test_resolve_returns_false_when_not_pending(self) -> None:
        manager = InProcessApprovalManager()
        resolved = manager.resolve(
            run_id=7,
            tool_call_ref="tc_missing",
            decision=ApprovalDecision(approved=False, reviewer_id=9, comment=None),
        )
        self.assertFalse(resolved)


if __name__ == "__main__":
    unittest.main()
