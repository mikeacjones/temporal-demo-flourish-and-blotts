"""Declarative tool harness for agentic workflows.

Public surface re-exported here so consumers don't need to know the internal
file layout. All names are workflow-safe (no I/O at import time)."""

from shared.agent_harness.tooldef import ToolDef, ToolCategory
from shared.agent_harness.guards import (
    Guard,
    GuardKind,
    GuardOutcome,
    HitlInteraction,
    Pass,
    Reject,
    guard,
)
from shared.agent_harness.policy import (
    CATEGORY_POLICIES,
    HUMAN_CONFIRMATION_KINDS,
    ToolPolicyError,
    validate_tool,
)
from shared.agent_harness.registry import register_tool
from shared.agent_harness.ctx import AgentCtx
from shared.agent_harness.loop import (
    DEFAULT_CLAUDE_RETRY,
    ExecutedTool,
    TurnResult,
    dispatch_tool,
    run_agent_turn,
)

__all__ = [
    "AgentCtx",
    "CATEGORY_POLICIES",
    "DEFAULT_CLAUDE_RETRY",
    "ExecutedTool",
    "Guard",
    "GuardKind",
    "GuardOutcome",
    "HUMAN_CONFIRMATION_KINDS",
    "HitlInteraction",
    "Pass",
    "Reject",
    "ToolCategory",
    "ToolDef",
    "ToolPolicyError",
    "TurnResult",
    "dispatch_tool",
    "guard",
    "register_tool",
    "run_agent_turn",
    "validate_tool",
]
