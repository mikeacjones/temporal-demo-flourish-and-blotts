"""ToolDef registration — a thin validating pass-through.

There is intentionally no global registry dict. Two ToolDefs may share the
same wire-name (e.g. substitute_item exists in both REPAIR_TOOLS and
OPS_TOOLS with different governance and impls). Each agent's tools list
is what gets passed to run_agent_turn; the dispatcher looks up by name
locally. register_tool is just a hook to run validate_tool at module-import
time so policy violations crash the worker."""
from __future__ import annotations

from shared.agent_harness.policy import validate_tool
from shared.agent_harness.tooldef import ToolDef


def register_tool(tool: ToolDef) -> ToolDef:
    """Validate and return the ToolDef. Use as a wrapper at module level:

        SUBSTITUTE_ITEM_REPAIR_TOOL = register_tool(ToolDef(...))
    """
    validate_tool(tool)
    return tool
