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
from shared.agent_harness.registry import (
    TOOL_HANDLERS,
    call_tool_handler,
    register_tool,
)
from shared.agent_harness.ctx import AgentCtx
from shared.agent_harness.dispatch_activity import dispatch_tool_activity
from shared.agent_harness.tool_ctx import ToolCtx, derive_activity_name
from shared.agent_harness.loop import (
    DEFAULT_CLAUDE_RETRY,
    ExecutedTool,
    TurnResult,
    dispatch_tool,
    run_agent_turn,
)
from shared.agent_harness.decorators import (
    OPS_TOOLS,
    REPAIR_TOOLS,
    ops_tool,
    repair_tool,
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
    "OPS_TOOLS",
    "Pass",
    "REPAIR_TOOLS",
    "Reject",
    "TOOL_HANDLERS",
    "ToolCategory",
    "ToolCtx",
    "ToolDef",
    "ToolPolicyError",
    "TurnResult",
    "call_tool_handler",
    "derive_activity_name",
    "dispatch_tool",
    "dispatch_tool_activity",
    "guard",
    "ops_tool",
    "register_tool",
    "repair_tool",
    "run_agent_turn",
    "validate_tool",
]
