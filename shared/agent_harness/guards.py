"""Guard and HitlInteraction primitives for the agent harness.

A guard is a workflow-safe async function that runs before a tool's impl/
interaction. Guards may call workflow.execute_activity, await signal-resolved
futures, or spawn child workflows. They return Pass (proceed) or Reject (do
not run; tell Claude why so it can self-correct)."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Awaitable, Callable, Union

if TYPE_CHECKING:
    from shared.agent_harness.ctx import AgentCtx
    from shared.models import ClaudeToolUse, ToolResult


class GuardKind(str, Enum):
    CUSTOMER_CONFIRMATION = "customer_confirmation"
    OPS_CONFIRMATION      = "ops_confirmation"


@dataclass(frozen=True)
class Pass:
    """Guard outcome: proceed to the next guard / impl / interaction."""


@dataclass(frozen=True)
class Reject:
    """Guard outcome: stop dispatching. The reason is surfaced to Claude as a
    non-error tool result so it can self-correct on the next turn."""
    reason: str


GuardOutcome = Union[Pass, Reject]

# A guard is a plain async function. Workflow-safe.
Guard = Callable[["ClaudeToolUse", "AgentCtx"], Awaitable[GuardOutcome]]

# An interaction is a workflow-safe coroutine that returns a ToolResult.
# Used by HITL_INTERACTION tools and by workflow-state-only tools.
HitlInteraction = Callable[["ClaudeToolUse", "AgentCtx"], Awaitable["ToolResult"]]


def guard(kind: GuardKind):
    """Decorator: tag a guard function with its kind. Required so the policy
    validator can recognise it.

    Usage:
        @guard(kind=GuardKind.OPS_CONFIRMATION)
        async def ops_confirmation(tool_use, agent_ctx) -> GuardOutcome: ...
    """
    def decorator(guard_fn: Guard) -> Guard:
        guard_fn.kind = kind  # type: ignore[attr-defined]
        return guard_fn
    return decorator
