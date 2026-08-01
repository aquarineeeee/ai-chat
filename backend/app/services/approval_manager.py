from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    approved: bool
    reviewer_id: int
    comment: str | None = None


class InProcessApprovalManager:
    def __init__(self) -> None:
        self._pending: dict[tuple[int, str], asyncio.Future[ApprovalDecision]] = {}

    def register(self, *, run_id: int, tool_call_ref: str) -> None:
        key = (run_id, tool_call_ref)
        future = self._pending.get(key)
        if future is not None and not future.done():
            return
        self._pending[key] = asyncio.get_running_loop().create_future()

    async def wait_for_decision(self, *, run_id: int, tool_call_ref: str) -> ApprovalDecision:
        key = (run_id, tool_call_ref)
        future = self._pending.get(key)
        if future is None:
            future = asyncio.get_running_loop().create_future()
            self._pending[key] = future
        try:
            return await future
        finally:
            self._pending.pop(key, None)

    def resolve(
        self,
        *,
        run_id: int,
        tool_call_ref: str,
        decision: ApprovalDecision,
    ) -> bool:
        future = self._pending.get((run_id, tool_call_ref))
        if future is None or future.done():
            return False
        future.set_result(decision)
        return True

    def has_pending(self, *, run_id: int, tool_call_ref: str) -> bool:
        future = self._pending.get((run_id, tool_call_ref))
        return future is not None and not future.done()

    async def shutdown(self) -> None:
        pending = list(self._pending.values())
        self._pending = {}
        for future in pending:
            if future.done():
                continue
            future.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


approval_manager = InProcessApprovalManager()
