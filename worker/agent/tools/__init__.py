"""Auto-discovered tool modules.

Importing this package walks every sibling `.py` file (skipping `_*` private
modules and `__init__.py` itself), imports each, then walks the imported
module for module-scope underscore-prefixed async functions and registers
them in TOOL_HANDLERS as sub-activity handlers under the convention
`<file_basename>:<func_name>`.

The decorators in shared.agent_harness.decorators populate REPAIR_TOOLS /
OPS_TOOLS as a side effect of decoration; this package re-exports them.
"""
from __future__ import annotations

import asyncio
import importlib
import pkgutil
from pathlib import Path

from shared.agent_harness.decorators import OPS_TOOLS, REPAIR_TOOLS
from shared.agent_harness.registry import TOOL_HANDLERS, _resolve_arg_type

# Kept here from the legacy worker/agent/tools.py for SlackConversationWorkflow.
# This constant will be removed when tools.py is deleted in Task 8.
CONVERSATION_TOOLS = [
    {
        "name": "update_plan",
        "description": (
            "Update the repair plan based on the human operator's instructions. "
            "Call this when the human wants to modify, add, or remove steps from the plan. "
            "Also provide the message to post back to Slack."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "description": "Updated ordered list of repair steps",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string"},
                            "description": {"type": "string"},
                            "tool": {"type": "string"},
                            "tool_args": {"type": "object"},
                        },
                        "required": ["action", "description"],
                    },
                },
                "rationale": {"type": "string"},
                "response_to_human": {
                    "type": "string",
                    "description": "What to post back in the Slack thread (acknowledging their change, confirming the updated plan)",
                },
            },
            "required": ["steps", "rationale", "response_to_human"],
        },
    }
]


def _register_module_subactivities(module) -> None:
    """Register every module-scope `_*` async function in `module` as a
    sub-activity handler. Skips functions that are decorated as tools (those
    are appended to REPAIR_TOOLS/OPS_TOOLS, not run as sub-activities)."""
    module_basename = module.__name__.rsplit(".", 1)[-1]
    tool_callables = {
        td.body for td in REPAIR_TOOLS + OPS_TOOLS if td.body is not None
    }
    for attr_name, attr_obj in vars(module).items():
        if not attr_name.startswith("_"):
            continue
        if not asyncio.iscoroutinefunction(attr_obj):
            continue
        if attr_obj in tool_callables:
            continue
        if getattr(attr_obj, "__module__", None) != module.__name__:
            continue  # imported from elsewhere; not ours to register
        handler_name = f"{module_basename}:{attr_name}"
        existing = TOOL_HANDLERS.get(handler_name)
        if existing is not None and existing[0] is attr_obj:
            continue  # already registered (idempotent re-import)
        if existing is not None:
            raise ValueError(
                f"Sub-activity name conflict: {handler_name!r} already registered "
                f"to a different callable."
            )
        takes_arg, ann = _resolve_arg_type(attr_obj)
        TOOL_HANDLERS[handler_name] = (attr_obj, takes_arg, ann)


_pkg_path = Path(__file__).parent
for _info in pkgutil.iter_modules([str(_pkg_path)]):
    if _info.name.startswith("_"):
        continue
    _module = importlib.import_module(f"{__name__}.{_info.name}")
    _register_module_subactivities(_module)


__all__ = ["REPAIR_TOOLS", "OPS_TOOLS", "CONVERSATION_TOOLS"]
