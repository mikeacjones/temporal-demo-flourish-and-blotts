"""Per-agent tool decorators.

Each decorator (`repair_tool`, `ops_tool`) builds a ToolDef from the decorated
function's metadata + decorator kwargs and appends it to a per-agent registry
list. Stacking decorators registers the same function under each agent it
decorates with potentially different category/guards/timeout per agent.

The function name is the wire-name (overridable via `name=`); the docstring
is the description (overridable via `description=`); the first-positional-
parameter annotation is the args_model.
"""
from __future__ import annotations

import inspect
from datetime import timedelta
from typing import Any, Awaitable, Callable, get_type_hints

from pydantic import BaseModel

from shared.agent_harness.guards import Guard
from shared.agent_harness.policy import validate_tool
from shared.agent_harness.tooldef import ToolCategory, ToolDef


REPAIR_TOOLS: list[ToolDef] = []
OPS_TOOLS: list[ToolDef] = []


def _resolve_args_model(fn: Callable[..., Awaitable[Any]]) -> type[BaseModel]:
    """Return the Pydantic model class annotating the function's first
    positional parameter. The decorator requires this — the args model is
    what Claude sees as the JSON schema for the tool's input.
    """
    sig = inspect.signature(fn)
    positional = [
        p for p in sig.parameters.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    if not positional:
        raise TypeError(
            f"Tool {fn.__name__!r}: must accept (args, ctx) positionally; got 0 params."
        )
    try:
        hints = get_type_hints(fn)
    except Exception as e:
        raise TypeError(
            f"Tool {fn.__name__!r}: could not resolve type hints — {e}"
        ) from e
    ann = hints.get(positional[0].name)
    if not (isinstance(ann, type) and issubclass(ann, BaseModel)):
        raise TypeError(
            f"Tool {fn.__name__!r}: first positional parameter must be annotated "
            f"with a Pydantic BaseModel subclass; got {ann!r}."
        )
    return ann


def _make_tool_decorator(registry: list[ToolDef]):
    """Factory: build a per-agent decorator that appends to `registry`."""

    def decorator(
        *,
        category: ToolCategory,
        guards: tuple[Guard, ...] = (),
        timeout: timedelta = timedelta(seconds=30),
        terminates_loop: bool = False,
        name: str | None = None,
        description: str | None = None,
    ) -> Callable[[Callable[..., Awaitable[str]]], Callable[..., Awaitable[str]]]:
        def wrap(fn: Callable[..., Awaitable[str]]) -> Callable[..., Awaitable[str]]:
            tool_name = name or fn.__name__
            tool_desc = description if description is not None else (fn.__doc__ or "").strip()
            if not tool_desc:
                raise ValueError(
                    f"Tool {tool_name!r}: description required (provide via "
                    "decorator `description=` or function docstring)."
                )
            args_model = _resolve_args_model(fn)
            tool_def = ToolDef(
                name=tool_name,
                description=tool_desc,
                args_model=args_model,
                category=category,
                guards=guards,
                body=fn,
                timeout=timeout,
                terminates_loop=terminates_loop,
            )
            validate_tool(tool_def)  # category-policy enforcement at import time
            registry.append(tool_def)
            return fn  # return original so decorator stacking works

        return wrap

    return decorator


repair_tool = _make_tool_decorator(REPAIR_TOOLS)
ops_tool = _make_tool_decorator(OPS_TOOLS)
