"""Shared parallel tool-use executor.

This is workflow-safe code (no I/O of its own) — it just gathers a list of
per-tool dispatch coroutines and shapes their results back into ToolResults.
Used by both OpsAgentConversationWorkflow and OrderRepairWorkflow so that
'execute Claude's tool calls' has one consistent shape.

Per-tool durability: each dispatch coroutine is responsible for its own
activity scheduling. asyncio.gather schedules them concurrently but each
activity is an independent durable unit in workflow history. There is no
mega-activity that runs all tools as a single atomic block.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from shared.models import ClaudeToolUse, ToolResult

ActivityDispatch = Callable[
    [ClaudeToolUse, dict[str, asyncio.Future[str]]],
    Awaitable[ToolResult],
]


async def execute_tool_uses(
    tool_uses: list[ClaudeToolUse],
    *,
    pending_actions: dict[str, asyncio.Future[str]],
    activity_dispatch: ActivityDispatch,
) -> list[ToolResult]:
    """Run all tool_uses concurrently. Returns results in input order.

    Any exception raised by activity_dispatch (e.g. ActivityError after retries)
    is converted to a ToolResult(is_error=True). The agent sees the error in the
    next round-trip and is expected to self-correct.
    """

    async def run_one(tu: ClaudeToolUse) -> ToolResult:
        try:
            return await activity_dispatch(tu, pending_actions)
        except Exception as e:
            return ToolResult(
                tool_use_id=tu.id,
                content=f"Tool '{tu.name}' failed: {e}",
                is_error=True,
            )

    return await asyncio.gather(*(run_one(tu) for tu in tool_uses))


def to_tool_results_message(results: list[ToolResult]) -> dict:
    """Build the Anthropic-API tool_result user-message content block."""
    return {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": r.tool_use_id,
                "content": r.content,
                "is_error": r.is_error,
            }
            for r in results
        ],
    }
