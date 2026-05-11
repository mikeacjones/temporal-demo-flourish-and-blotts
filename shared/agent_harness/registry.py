"""Tool registry — maps wire-name → (handler, takes_arg, arg_type).

`register_tool` validates each ToolDef and, when an `impl` is set, adds an
entry to TOOL_HANDLERS. Two ToolDefs may share the same wire-name (e.g.
substitute_item exists in both REPAIR and OPS) as long as the handler
callable is the same — registering a different callable under an existing
name is rejected.

The registry is consumed by:
  * dispatch_tool_activity — the dynamic Temporal activity for tool calls.
  * call_tool_handler — direct in-process callers like
    execute_approved_plan_step that already run inside an activity.
"""
from __future__ import annotations

import asyncio
import inspect
from typing import Any, Awaitable, Callable, get_type_hints

from pydantic import BaseModel

from shared.agent_harness.policy import validate_tool
from shared.agent_harness.tooldef import ToolDef


ToolHandler = Callable[..., Awaitable[Any]]
ToolHandlerEntry = tuple[ToolHandler, bool, type | None]


TOOL_HANDLERS: dict[str, ToolHandlerEntry] = {}


def _resolve_arg_type(handler: ToolHandler) -> tuple[bool, type | None]:
    """Return (takes_arg, annotation) for the handler's first positional parameter.

    takes_arg=False means the handler is parameterless; call_tool_handler will
    invoke it with no arguments.

    takes_arg=True, annotation=SomeType means decode/coerce args to that type.

    takes_arg=True, annotation=None means the annotation couldn't be resolved
    (forward reference, missing annotation, etc.); args are passed through as-is
    rather than silently dropped.
    """
    sig = inspect.signature(handler)
    positional = [
        p for p in sig.parameters.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    if not positional:
        return (False, None)
    try:
        hints = get_type_hints(handler)
    except Exception:
        return (True, None)
    return (True, hints.get(positional[0].name))


def register_tool(tool: ToolDef) -> ToolDef:
    """Validate the ToolDef and (if it has an `impl`) register the handler.

    Body-based ToolDefs (the decorator-driven path) are NOT added to
    TOOL_HANDLERS — their bodies run inline in workflow context, not as
    activities. Only `impl`-based legacy ToolDefs populate TOOL_HANDLERS;
    `interaction`-based and `body`-based ToolDefs leave it untouched.
    """
    validate_tool(tool)
    # body-based ToolDefs intentionally fall through here — they run inline
    # in workflow context and do not need a TOOL_HANDLERS entry.
    if tool.impl is not None:
        existing = TOOL_HANDLERS.get(tool.name)
        if existing is not None and existing[0] is not tool.impl:
            raise ValueError(
                f"Tool {tool.name!r}: conflicting handler. Two ToolDefs sharing "
                f"a wire-name must share the same impl callable."
            )
        if not asyncio.iscoroutinefunction(tool.impl):
            raise TypeError(
                f"Tool {tool.name!r}: impl must be an async function (got {tool.impl!r}). "
                "Tool handlers run inside an awaited dispatch and must return a coroutine."
            )
        takes_arg, ann = _resolve_arg_type(tool.impl)
        TOOL_HANDLERS[tool.name] = (tool.impl, takes_arg, ann)
    return tool


async def call_tool_handler(tool_name: str, args: Any) -> Any:
    """Dispatch a registered tool by name. If the handler expects a Pydantic
    model and `args` is a dict, instantiate the model. Used by dispatch_tool_activity
    after wire-payload decoding, and directly by execute_approved_plan_step
    where args arrive as plain dicts from a persisted plan."""
    entry = TOOL_HANDLERS.get(tool_name)
    if entry is None:
        raise KeyError(f"No tool handler registered for {tool_name!r}")
    handler, takes_arg, ann = entry
    if not takes_arg:
        return await handler()
    # isinstance(ann, type) guards against generic aliases (list[X], Optional[X])
    # which are not `type` instances and would raise TypeError in issubclass.
    if isinstance(args, dict) and isinstance(ann, type) and issubclass(ann, BaseModel):
        args = ann(**args)
    return await handler(args)
