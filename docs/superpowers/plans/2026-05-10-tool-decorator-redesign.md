# Tool Decorator Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dataclass-based `ToolDef` registration with stacked-per-agent decorators on workflow-coroutine bodies, with one tool per file in `worker/agent/tools/`. Sub-activity helpers are plain async functions in the same file, dispatched via `ctx.activity(callable, summary=...)` through the existing dynamic activity.

**Architecture:** Additive infrastructure first (ToolCtx, decorators, body-aware ToolDef, tools-folder auto-discovery) — no breaking changes through Task 5. Then big-bang migration of all 21 tools to per-tool files in Task 6, followed by import switchover (Task 7) and cleanup (Tasks 8-9). The transition keeps `ToolDef.impl`/`interaction` during the additive phase so the system runs end-to-end at every commit.

**Tech Stack:** Temporal Python SDK 1.26 (`temporalio[pydantic]`), Pydantic 2.10, `pydantic_data_converter`. No new dependencies.

**Branch:** `feat/tool-decorator-redesign` (already created).

**Reference spec:** `docs/superpowers/specs/2026-05-10-tool-decorator-redesign.md`.

---

## File Map

### New files

| Path | Responsibility |
|---|---|
| `shared/agent_harness/tool_ctx.py` | `ToolCtx` dataclass + `activity()` helper |
| `shared/agent_harness/decorators.py` | `repair_tool`, `ops_tool` decorator factories + per-agent registries (`REPAIR_TOOLS`, `OPS_TOOLS`) |
| `worker/agent/tools/__init__.py` | Auto-imports every sibling module at import time; walks each for module-scope `_*` async functions and registers them in TOOL_HANDLERS as sub-activities |
| `worker/agent/tools/<tool_name>.py` × 20 | One file per tool (see migration tables in Task 6) |

### Modified files

| Path | Change |
|---|---|
| `shared/agent_harness/tooldef.py` | Add `body: Optional[Callable]` field |
| `shared/agent_harness/policy.py` | `validate_tool` accepts `body` as a third valid implementation field |
| `shared/agent_harness/registry.py` | `register_tool` skips TOOL_HANDLERS population for body-only ToolDefs (body runs inline as workflow code) |
| `shared/agent_harness/loop.py` | `dispatch_tool` calls `td.body(args, ToolCtx(...))` when body is set; falls back to impl/interaction for transitional ToolDefs |
| `shared/agent_harness/__init__.py` | Re-export `ToolCtx`, `repair_tool`, `ops_tool`, `REPAIR_TOOLS`, `OPS_TOOLS` |
| `worker/main.py` | Import `REPAIR_TOOLS` / `OPS_TOOLS` (and any still-registered activities) from new locations |
| `worker/workflows/order_repair_workflow.py` | Import `REPAIR_TOOLS` from new location |
| `worker/workflows/ops_agent_conversation_workflow.py` | Import `OPS_TOOLS` from new location |

### Deleted files (Task 8)

- `worker/agent/repair_tools.py`
- `worker/agent/ops_tools.py`
- `worker/agent/interactions.py`
- `worker/activities/repair_activities.py` (logic redistributed; no remaining callers after migration)
- All ops-tool handlers in `worker/activities/ops_activities.py` that aren't called from non-tool workflows (those that ARE called directly from non-tool workflows stay)

---

## Task 1: Add `ToolCtx`

**Files:**
- Create: `shared/agent_harness/tool_ctx.py`
- Modify: `shared/agent_harness/__init__.py`

- [ ] **Step 1: Create `shared/agent_harness/tool_ctx.py`**

```python
"""ToolCtx — the per-tool-call context object passed into a tool body.

Holds the originating tool_use (for signal correlation), the broader AgentCtx,
and an `activity()` helper that dispatches a sub-activity handler through the
existing dynamic activity (`dispatch_tool_activity`). The helper requires a
`summary` keyword so every sub-activity call carries human-readable metadata
visible in workflow history.

Tool authors interact with three concrete things:
  - `ctx.tool_use_id` — for correlating signals (e.g. resolving futures in
    ctx.pending_actions)
  - `ctx.state` / `ctx.input` / etc. — passthrough accessors for AgentCtx
  - `ctx.activity(callable, *args, summary=..., ...)` — dispatch a sub-activity
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from temporalio import workflow
from temporalio.common import RetryPolicy

if TYPE_CHECKING:
    from shared.agent_harness.ctx import AgentCtx
    from shared.models import ClaudeToolUse


DEFAULT_ACTIVITY_TIMEOUT = timedelta(seconds=30)


def derive_activity_name(callable_obj: Callable[..., Any]) -> str:
    """Return the wire-name a sub-activity handler will be dispatched under.

    Convention: `<file_basename>:<func_name>`. Stable as long as the helper's
    file and function aren't renamed. Example: `_send_house_elf` defined in
    `worker/agent/tools/dispatch_house_elf.py` → `dispatch_house_elf:_send_house_elf`.
    """
    module_basename = callable_obj.__module__.rsplit(".", 1)[-1]
    return f"{module_basename}:{callable_obj.__name__}"


@dataclass(frozen=True)
class ToolCtx:
    """Per-tool-invocation context. Tool bodies receive this as their second
    positional argument."""
    tool_use: "ClaudeToolUse"
    agent: "AgentCtx"

    # ---- Convenience passthroughs to AgentCtx -----------------------------

    @property
    def tool_use_id(self) -> str:
        return self.tool_use.id

    @property
    def state(self) -> Any:
        """Agent's domain state object (e.g. RepairAgentState)."""
        return self.agent.domain_state

    @property
    def input(self) -> Any:
        """Agent's domain input (e.g. OrderRepairInput, OpsAgentConversationInput)."""
        return self.agent.domain_input

    @property
    def pending_actions(self) -> Any:
        """Workflow-side futures keyed by tool_use_id, used for signal correlation."""
        return self.agent.pending_actions

    @property
    def channel(self) -> str | None:
        return getattr(self.agent, "channel", None)

    @property
    def thread_ts(self) -> str | None:
        return getattr(self.agent, "thread_ts", None)

    # ---- Sub-activity dispatch -------------------------------------------

    async def activity(
        self,
        callable_obj: Callable[..., Awaitable[Any]],
        *args: Any,
        summary: str,
        start_to_close_timeout: timedelta = DEFAULT_ACTIVITY_TIMEOUT,
        heartbeat_timeout: timedelta | None = None,
        retry_policy: RetryPolicy | None = None,
        schedule_to_close_timeout: timedelta | None = None,
    ) -> Any:
        """Dispatch a sub-activity through the dynamic activity.

        The handler's wire-name is derived from `callable_obj` via
        `derive_activity_name`; the dispatcher resolves it through
        TOOL_HANDLERS at activity-task time. `summary` is required and
        forwards as Temporal user-metadata so each step is labelled in the
        Temporal UI.
        """
        name = derive_activity_name(callable_obj)
        kwargs: dict[str, Any] = {
            "start_to_close_timeout": start_to_close_timeout,
            "summary": summary,
        }
        if heartbeat_timeout is not None:
            kwargs["heartbeat_timeout"] = heartbeat_timeout
        if retry_policy is not None:
            kwargs["retry_policy"] = retry_policy
        if schedule_to_close_timeout is not None:
            kwargs["schedule_to_close_timeout"] = schedule_to_close_timeout

        if not args:
            return await workflow.execute_activity(name, **kwargs)
        if len(args) == 1:
            return await workflow.execute_activity(name, args[0], **kwargs)
        return await workflow.execute_activity(name, args=list(args), **kwargs)
```

- [ ] **Step 2: Re-export from `shared/agent_harness/__init__.py`**

Add to the import block (alphabetical with existing imports):

```python
from shared.agent_harness.tool_ctx import ToolCtx, derive_activity_name
```

Add to `__all__` in alphabetical order: `"ToolCtx"`, `"derive_activity_name"`.

- [ ] **Step 3: Verify imports**

Run: `python -c "from shared.agent_harness import ToolCtx, derive_activity_name; print('ok')"`

Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add shared/agent_harness/tool_ctx.py shared/agent_harness/__init__.py
git commit -m "feat(harness): add ToolCtx with activity() helper"
```

---

## Task 2: Add `body` field to ToolDef

**Files:**
- Modify: `shared/agent_harness/tooldef.py`
- Modify: `shared/agent_harness/policy.py`
- Modify: `shared/agent_harness/registry.py`

- [ ] **Step 1: Add `body` field to ToolDef**

Edit `shared/agent_harness/tooldef.py` — add `body` to the dataclass between `interaction` and `timeout`:

```python
@dataclass(frozen=True)
class ToolDef:
    """Declarative description of one agent-visible tool.

    Exactly one of `impl`, `interaction`, or `body` must be set:
      * `impl` is an @activity.defn function — runs as a Temporal activity. (Legacy)
      * `interaction` is a workflow-safe coroutine taking (tool_use, ctx). (Legacy)
      * `body` is a workflow-safe coroutine taking (args, ToolCtx). (New, decorator-driven)
    """
    name: str
    description: str
    args_model: type[BaseModel]
    category: ToolCategory
    guards: tuple["Guard", ...] = ()
    impl: Optional[Callable] = None
    interaction: Optional["HitlInteraction"] = None
    body: Optional[Callable] = None
    timeout: timedelta = timedelta(seconds=30)
    terminates_loop: bool = False
    make_impl_input: Optional[
        Callable[[BaseModel, "ClaudeToolUse", "AgentCtx"], object]
    ] = None
```

- [ ] **Step 2: Update `validate_tool` in `policy.py` to accept `body`**

Replace the structural-invariants block in `shared/agent_harness/policy.py` (currently around `has_impl == has_interaction`) with:

```python
def validate_tool(tool: ToolDef) -> None:
    """Check structural invariants and category policy for a ToolDef."""
    has_impl = tool.impl is not None
    has_interaction = tool.interaction is not None
    has_body = tool.body is not None
    set_count = sum([has_impl, has_interaction, has_body])
    if set_count != 1:
        raise ToolPolicyError(
            f"Tool {tool.name!r}: exactly one of impl/interaction/body must be set "
            f"(got impl={has_impl}, interaction={has_interaction}, body={has_body})"
        )
    if tool.category == ToolCategory.HITL_INTERACTION and has_impl:
        raise ToolPolicyError(
            f"HITL_INTERACTION tool {tool.name!r} must use `interaction` or `body`, not `impl`"
        )
    if tool.terminates_loop and tool.category != ToolCategory.HITL_INTERACTION:
        raise ToolPolicyError(
            f"Tool {tool.name!r}: terminates_loop=True is only valid for HITL_INTERACTION"
        )

    required = CATEGORY_POLICIES.get(tool.category, frozenset())
    if not required:
        return
    present = {getattr(g, "kind", None) for g in tool.guards}
    if not (present & required):
        raise ToolPolicyError(
            f"Tool {tool.name!r} (category={tool.category.value}) needs "
            f"at least one guard of kind {sorted(k.value for k in required)}; "
            f"has {sorted(k.value for k in present if k)}"
        )
```

- [ ] **Step 3: Update `register_tool` in `registry.py` to skip TOOL_HANDLERS for body-only ToolDefs**

Replace the body of `register_tool` in `shared/agent_harness/registry.py`:

```python
def register_tool(tool: ToolDef) -> ToolDef:
    """Validate the ToolDef and (if it has an `impl`) register the handler.

    Body-based ToolDefs (new decorator path) are NOT added to TOOL_HANDLERS —
    their bodies run inline in workflow context, not as activities.
    """
    validate_tool(tool)
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
```

- [ ] **Step 4: Verify**

Run: `python -c "import worker.agent.repair_tools; import worker.agent.ops_tools; from shared.agent_harness import TOOL_HANDLERS; print(len(TOOL_HANDLERS))"`

Expected: `18` (existing tools still register correctly via the legacy `impl` path).

- [ ] **Step 5: Commit**

```bash
git add shared/agent_harness/tooldef.py shared/agent_harness/policy.py shared/agent_harness/registry.py
git commit -m "feat(harness): add ToolDef.body field for workflow-coroutine tools"
```

---

## Task 3: Add agent decorators

**Files:**
- Create: `shared/agent_harness/decorators.py`
- Modify: `shared/agent_harness/__init__.py`

- [ ] **Step 1: Create `shared/agent_harness/decorators.py`**

```python
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
```

- [ ] **Step 2: Re-export from `shared/agent_harness/__init__.py`**

Add to imports:

```python
from shared.agent_harness.decorators import (
    OPS_TOOLS,
    REPAIR_TOOLS,
    ops_tool,
    repair_tool,
)
```

Add to `__all__` (alphabetical): `"OPS_TOOLS"`, `"REPAIR_TOOLS"`, `"ops_tool"`, `"repair_tool"`.

- [ ] **Step 3: Verify decorator wires up**

Run:
```bash
python -c "
from datetime import timedelta
from pydantic import BaseModel
from shared.agent_harness import repair_tool, ops_tool, ToolCategory, REPAIR_TOOLS, OPS_TOOLS

class _Args(BaseModel):
    pass

@repair_tool(category=ToolCategory.READ)
@ops_tool(category=ToolCategory.READ)
async def _ping(args: _Args, ctx) -> str:
    'A test tool.'
    return 'pong'

print(f'REPAIR_TOOLS has _ping: {any(t.name == \"_ping\" for t in REPAIR_TOOLS)}')
print(f'OPS_TOOLS has _ping: {any(t.name == \"_ping\" for t in OPS_TOOLS)}')
"
```
Expected:
```
REPAIR_TOOLS has _ping: True
OPS_TOOLS has _ping: True
```

- [ ] **Step 4: Commit**

```bash
git add shared/agent_harness/decorators.py shared/agent_harness/__init__.py
git commit -m "feat(harness): add @repair_tool and @ops_tool decorators"
```

---

## Task 4: Update `dispatch_tool` to call body

**Files:**
- Modify: `shared/agent_harness/loop.py`

- [ ] **Step 1: Update `dispatch_tool` in `loop.py`**

Find the existing `dispatch_tool` function. Replace the section starting with `# 3. Run the impl (activity) or interaction (workflow coroutine).` through the end of the function with the body-aware version below. The validation and guard sections (parts 1 and 2) are unchanged.

```python
    # 3. Run the body (workflow coroutine), impl (activity), or interaction.

    if tool_def.body is not None:
        try:
            tool_ctx = ToolCtx(tool_use=tool_use, agent=agent_ctx)
            result = await tool_def.body(args, tool_ctx)
            return ToolResult(tool_use_id=tool_use.id, content=str(result))
        except Exception as error:
            return ToolResult(
                tool_use_id=tool_use.id,
                is_error=True,
                content=f"Tool {tool_use.name!r} failed: {error}",
            )

    if tool_def.impl is not None:
        try:
            if tool_def.make_impl_input is not None:
                impl_input = tool_def.make_impl_input(args, tool_use, agent_ctx)
                if impl_input is None:
                    result = await workflow.execute_activity(
                        tool_def.name, start_to_close_timeout=tool_def.timeout,
                    )
                else:
                    result = await workflow.execute_activity(
                        tool_def.name, impl_input, start_to_close_timeout=tool_def.timeout,
                    )
            elif not tool_def.args_model.model_fields:
                result = await workflow.execute_activity(
                    tool_def.name, start_to_close_timeout=tool_def.timeout,
                )
            else:
                result = await workflow.execute_activity(
                    tool_def.name, args, start_to_close_timeout=tool_def.timeout,
                )
            return ToolResult(tool_use_id=tool_use.id, content=str(result))
        except Exception as error:
            return ToolResult(
                tool_use_id=tool_use.id,
                is_error=True,
                content=f"Tool {tool_use.name!r} failed: {error}",
            )

    if tool_def.interaction is not None:
        try:
            return await tool_def.interaction(tool_use, agent_ctx)
        except Exception as error:
            return ToolResult(
                tool_use_id=tool_use.id,
                is_error=True,
                content=f"Tool {tool_use.name!r} interaction failed: {error}",
            )

    return ToolResult(
        tool_use_id=tool_use.id,
        is_error=True,
        content=f"Tool {tool_use.name!r} has neither body nor impl nor interaction",
    )
```

- [ ] **Step 2: Add `ToolCtx` import to `loop.py`**

Inside the existing `with workflow.unsafe.imports_passed_through():` block, add:

```python
    from shared.agent_harness.tool_ctx import ToolCtx
```

- [ ] **Step 3: Verify the existing demo still loads**

Run: `python -c "import worker.main; print('worker module loads')"`

Expected: `worker module loads`

(The existing 18 tools still use `impl`/`interaction`; the new `body` branch is dormant until Task 6 migrates tools.)

- [ ] **Step 4: Commit**

```bash
git add shared/agent_harness/loop.py
git commit -m "feat(harness): dispatch_tool calls td.body when set"
```

---

## Task 5: Build the tools folder + auto-discovery

**Files:**
- Create: `worker/agent/tools/__init__.py`

- [ ] **Step 1: Create `worker/agent/tools/__init__.py`**

```python
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


__all__ = ["REPAIR_TOOLS", "OPS_TOOLS"]
```

- [ ] **Step 2: Verify the empty tools package imports cleanly**

Run:
```bash
python -c "from worker.agent.tools import REPAIR_TOOLS, OPS_TOOLS; print(f'{len(REPAIR_TOOLS)} repair tools, {len(OPS_TOOLS)} ops tools')"
```
Expected:
```
0 repair tools, 0 ops tools
```

(The folder is empty — Task 6 populates it.)

- [ ] **Step 3: Commit**

```bash
git add worker/agent/tools/__init__.py
git commit -m "feat(tools): add auto-discovery package for per-tool files"
```

---

## Task 6: Migrate all tools to per-tool files

This task is broken into per-family sub-tasks. Each sub-task creates several tool files and commits as a unit. The system stays runnable after each commit because the existing `repair_tools.py`/`ops_tools.py`/`interactions.py` still register their old `impl`/`interaction` ToolDefs in parallel — Task 7 cuts that over.

Tools that exist in BOTH agents go in ONE file with stacked decorators. The function name is the wire-name. For tools where the REPAIR and OPS variants have structurally different bodies (only `substitute_item`), the file declares two functions and uses `name=` overrides on each decorator.

### Tool file template

Every tool file follows this structure. Substitute the names, decorators, args model, and body.

```python
"""<one-line summary of the tool>"""
from __future__ import annotations

# stdlib imports the body uses
# (e.g. import asyncio, random)

from temporalio import activity, workflow

# Pydantic args model and any cross-tool types
from worker.agent.tool_args import <ArgsModel>

# Decorator + types
from shared.agent_harness import (
    <ToolCategory enum members used>,
    ops_tool,             # only if OPS-applicable
    repair_tool,          # only if REPAIR-applicable
    ToolCtx,
)

# Optional: guard imports
# from worker.agent.guards import ops_confirmation, substitute_item_customer_confirmation


# Sub-activity helpers (plain async functions, no decorator).
# Underscore prefix marks them as module-private; auto-discovered into TOOL_HANDLERS.
async def _helper_one(arg: <SomeType>) -> <ReturnType>:
    """One-line docstring."""
    ...


@repair_tool(category=ToolCategory.<X>, timeout=...)   # if REPAIR-applicable
@ops_tool(category=ToolCategory.<Y>, guards=(...,), timeout=...)  # if OPS-applicable
async def <tool_wire_name>(args: <ArgsModel>, ctx: ToolCtx) -> str:
    """<Description Claude sees as the tool's documentation. Required.>"""
    ...
    return "result string"
```

---

### Task 6.1: Migrate read-only tools (no guards)

**Files (create):**
- `worker/agent/tools/list_inventory.py`
- `worker/agent/tools/check_inventory.py`
- `worker/agent/tools/verify_customer_credentials.py`
- `worker/agent/tools/list_orders.py`
- `worker/agent/tools/describe_order.py`
- `worker/agent/tools/describe_workflow.py`
- `worker/agent/tools/get_workflow_history.py`
- `worker/agent/tools/aggregate_repair_failures.py`
- `worker/agent/tools/get_book.py`

**For each file**, the steps are: (1) write the file from the existing handler logic, (2) verify imports load and the tool registers in the right per-agent collection.

Source mappings (existing handler bodies and ToolDef metadata to consult while writing each new file):

| New file | Old handler | Old ToolDef | Agents | Category |
|---|---|---|---|---|
| `list_inventory.py` | `worker/activities/ops_activities.py:list_inventory` | `repair_tools.py:LIST_INVENTORY_REPAIR_TOOL`, `ops_tools.py:LIST_INVENTORY_OPS_TOOL` | REPAIR + OPS | READ |
| `check_inventory.py` | `worker/activities/repair_activities.py:check_inventory` | `repair_tools.py:CHECK_INVENTORY_REPAIR_TOOL`, `ops_tools.py:CHECK_INVENTORY_OPS_TOOL` | REPAIR + OPS | READ |
| `verify_customer_credentials.py` | `worker/activities/repair_activities.py:verify_customer_credentials` | `repair_tools.py:VERIFY_CUSTOMER_CREDENTIALS_REPAIR_TOOL`, `ops_tools.py:VERIFY_CUSTOMER_CREDENTIALS_OPS_TOOL` | REPAIR + OPS | READ |
| `list_orders.py` | `worker/activities/ops_activities.py:list_orders` | `ops_tools.py:LIST_ORDERS_OPS_TOOL` | OPS | READ |
| `describe_order.py` | `worker/activities/ops_activities.py:describe_order` | `ops_tools.py:DESCRIBE_ORDER_OPS_TOOL` | OPS | READ |
| `describe_workflow.py` | `worker/activities/ops_activities.py:describe_workflow` | `ops_tools.py:DESCRIBE_WORKFLOW_OPS_TOOL` | OPS | READ |
| `get_workflow_history.py` | `worker/activities/ops_activities.py:get_workflow_history` | `ops_tools.py:GET_WORKFLOW_HISTORY_OPS_TOOL` | OPS | READ |
| `aggregate_repair_failures.py` | `worker/activities/ops_activities.py:aggregate_repair_failures` | `ops_tools.py:AGGREGATE_REPAIR_FAILURES_OPS_TOOL` | OPS | READ |
| `get_book.py` | `worker/activities/ops_activities.py:get_book` | `ops_tools.py:GET_BOOK_OPS_TOOL` | OPS | READ |

#### Worked example: `list_inventory.py`

The existing `list_inventory` activity in `ops_activities.py` is a Temporal Visibility query (calls Temporal client APIs). It MUST stay a sub-activity. The new file:

- [ ] **Step 1: Create `worker/agent/tools/list_inventory.py`**

```python
"""List the entire book catalog with current OMS stock counts."""
from __future__ import annotations

from datetime import timedelta

from shared.agent_harness import ToolCategory, ToolCtx, ops_tool, repair_tool
from shared.catalog import get_all_books
from shared.models import InventoryItem, ListInventoryResult
from worker.agent.tool_args import ListInventoryArgs


_READ_TIMEOUT = timedelta(seconds=10)


async def _read_catalog() -> ListInventoryResult:
    """Build the inventory snapshot from the in-memory catalog."""
    items = [
        InventoryItem(
            book_id=book.id,
            title=book.title,
            author=book.author,
            in_stock=book.in_stock,
            physical_in_stock=book.physical_in_stock,
            category=book.category,
        )
        for book in get_all_books()
    ]
    return ListInventoryResult(items=items)


@repair_tool(category=ToolCategory.READ, timeout=_READ_TIMEOUT)
@ops_tool(category=ToolCategory.READ, timeout=_READ_TIMEOUT)
async def list_inventory(args: ListInventoryArgs, ctx: ToolCtx) -> str:
    """List the entire book catalog with current OMS stock counts and physical
    warehouse counts. Useful for browsing substitute candidates when a book
    is out of stock — returns every book with its title, author, OMS in_stock,
    and physical_in_stock so you can pick a viable substitute in one call
    rather than running check_inventory book by book."""
    result = await ctx.activity(
        _read_catalog,
        summary="Read the full book catalog from the OMS.",
        start_to_close_timeout=_READ_TIMEOUT,
    )
    lines = [
        f"- {item.book_id}: '{item.title}' by {item.author} "
        f"(OMS in_stock={item.in_stock}, physical={item.physical_in_stock or item.in_stock})"
        for item in result.items
    ]
    return "Catalog inventory:\n" + "\n".join(lines)
```

(Note: the existing `list_inventory` activity body returned the `ListInventoryResult` dataclass directly. That logic moves into `_read_catalog`. The tool body formats the result for Claude. If the existing activity body was more complex, port the additional logic into `_read_catalog`.)

- [ ] **Step 2: Verify the file imports**

```bash
python -c "import worker.agent.tools.list_inventory; from worker.agent.tools import REPAIR_TOOLS, OPS_TOOLS; print([t.name for t in REPAIR_TOOLS]); print([t.name for t in OPS_TOOLS])"
```
Expected: `list_inventory` appears in both lists.

- [ ] **Step 3: Repeat for the remaining 8 read-only tools**

Use the worked example as a template. Key adaptations per file:

- `check_inventory.py`: tool body delegates to `_lookup_book(item_id)` sub-activity that reads the catalog and builds a status string. Existing logic in `repair_activities.py:check_inventory` (the case branch in the legacy if-tree, or the post-refactor handler — depending on which version you're working from). Both REPAIR and OPS register with `category=ToolCategory.READ`.
- `verify_customer_credentials.py`: sub-activity `_call_ministry(customer_id, requirement_type)` simulates the API call (uses `random.random() > 0.3`); tool body formats result. Both REPAIR and OPS register with `category=ToolCategory.READ`.
- `list_orders.py` (OPS only): sub-activity `_query_visibility(input: ListOrdersInput)` wraps the existing Temporal-Visibility-querying logic from `ops_activities.py:list_orders` so it stays a real activity. Tool body summarizes for Claude.
- `describe_order.py` (OPS only): sub-activity `_describe(input)` wraps existing `ops_activities.py:describe_order`. Tool body returns a multi-line summary including related_workflows.
- `describe_workflow.py` (OPS only): sub-activity `_describe_workflow(input)` wraps existing `ops_activities.py:describe_workflow`.
- `get_workflow_history.py` (OPS only): sub-activity `_get_history(input)` wraps existing `ops_activities.py:get_workflow_history`. Tool body returns a structured timeline.
- `aggregate_repair_failures.py` (OPS only): sub-activity `_aggregate(input)` wraps existing `ops_activities.py:aggregate_repair_failures`. Tool body returns a count summary.
- `get_book.py` (OPS only): sub-activity `_get_book(book_id)` wraps the in-memory catalog read. Tool body formats the result.

For each: the description in the new file's docstring must match the existing ToolDef's `description` text (you can copy it verbatim from `repair_tools.py` / `ops_tools.py`).

- [ ] **Step 4: Verify all 9 read tools register**

```bash
python -c "
from worker.agent.tools import REPAIR_TOOLS, OPS_TOOLS
expected_repair = {'list_inventory', 'check_inventory', 'verify_customer_credentials'}
expected_ops = {
    'list_inventory', 'check_inventory', 'verify_customer_credentials',
    'list_orders', 'describe_order', 'describe_workflow',
    'get_workflow_history', 'aggregate_repair_failures', 'get_book',
}
got_repair = {t.name for t in REPAIR_TOOLS}
got_ops = {t.name for t in OPS_TOOLS}
print('repair missing:', expected_repair - got_repair)
print('ops missing:', expected_ops - got_ops)
print('repair extra:', got_repair - expected_repair)
print('ops extra:', got_ops - expected_ops)
"
```
Expected: all four lines empty (no missing, no extras).

- [ ] **Step 5: Commit**

```bash
git add worker/agent/tools/
git commit -m "feat(tools): migrate read-only tools to per-tool files"
```

---

### Task 6.2: Migrate autonomous repair tools (and OPS mutating variants)

**Files (create):**
- `worker/agent/tools/apply_containment_charm.py`
- `worker/agent/tools/dispatch_house_elf.py`
- `worker/agent/tools/reroute_via_floo.py`
- `worker/agent/tools/update_order_status.py`
- `worker/agent/tools/contact_customer.py`

Each file declares ONE tool with stacked `@repair_tool` (AUTONOMOUS) + `@ops_tool` (MUTATING + ops_confirmation guard). Source: `repair_tools.py` and `ops_tools.py` for ToolDef metadata; `repair_activities.py` for handler bodies.

#### Worked example: `dispatch_house_elf.py`

- [ ] **Step 1: Create `worker/agent/tools/dispatch_house_elf.py`**

```python
"""Dispatch a house elf for magical manual intervention."""
from __future__ import annotations

import asyncio
import random
from datetime import timedelta

from temporalio import activity

from shared.agent_harness import ToolCategory, ToolCtx, ops_tool, repair_tool
from worker.agent.guards import ops_confirmation
from worker.agent.tool_args import DispatchHouseElfArgs


_LONG_TIMEOUT = timedelta(seconds=120)


async def _send_house_elf(task: str) -> str:
    """Long-running stub — heartbeats while a notional house elf retrieves an item.

    Production version would call an external dispatch service and poll for
    completion. The activity heartbeats so the workflow can detect a stuck elf.
    """
    elf = random.choice(["Dobby", "Kreacher", "Winky", "Hokey", "Mipsy"])
    total_steps = random.randint(5, 12)
    for step in range(total_steps):
        await asyncio.sleep(random.uniform(0.4, 0.9))
        activity.heartbeat(f"{elf} en route — step {step + 1}/{total_steps}")
    return random.choice([
        f"{elf} dispatched and completed: {task}",
        f"{elf} reports task complete. Note: {elf} is very happy to help and requests no payment.",
    ])


@repair_tool(category=ToolCategory.AUTONOMOUS, timeout=_LONG_TIMEOUT)
@ops_tool(
    category=ToolCategory.MUTATING,
    guards=(ops_confirmation,),
    timeout=_LONG_TIMEOUT,
)
async def dispatch_house_elf(args: DispatchHouseElfArgs, ctx: ToolCtx) -> str:
    """Dispatch a house elf for magical manual intervention. Use for tasks
    requiring physical wizarding assistance: retrieving intercepted deliveries,
    capturing escaped magical items, emergency repackaging, or any on-site
    intervention."""
    outcome = await ctx.activity(
        _send_house_elf,
        args.task,
        summary=f"Dispatch a house elf to: {args.task}",
        start_to_close_timeout=_LONG_TIMEOUT,
        heartbeat_timeout=timedelta(seconds=15),
    )
    return f"Order {args.order_id}: House elf {outcome}"
```

- [ ] **Step 2: Repeat for the other four tools**

Apply the same shape:

- `apply_containment_charm.py` — sub-activity `_apply_charm(item_id)` simulates the charm application (uses `random.choice` for outcome flavor). Tool body formats `f"Order {args.order_id}: {outcome}"`.
- `reroute_via_floo.py` — sub-activity `_reroute(order_id, destination)` simulates the Floo Network call. Tool body formats per existing description.
- `update_order_status.py` — sub-activity `_update_oms(order_id, status, message)` simulates an OMS write. Tool body returns the formatted result.
- `contact_customer.py` — sub-activity `_send_owl(order_id, message)` simulates the email send. Tool body returns the formatted result.

For each, the `@repair_tool` carries `category=ToolCategory.AUTONOMOUS` and the `@ops_tool` carries `category=ToolCategory.MUTATING, guards=(ops_confirmation,)`. Use the description text verbatim from the existing ToolDef.

- [ ] **Step 3: Verify**

```bash
python -c "
from worker.agent.tools import REPAIR_TOOLS, OPS_TOOLS
expected_each = {'apply_containment_charm', 'dispatch_house_elf', 'reroute_via_floo', 'update_order_status', 'contact_customer'}
repair_names = {t.name for t in REPAIR_TOOLS}
ops_names = {t.name for t in OPS_TOOLS}
print('repair missing:', expected_each - repair_names)
print('ops missing:', expected_each - ops_names)
"
```
Expected: both lines `set()`.

- [ ] **Step 4: Commit**

```bash
git add worker/agent/tools/
git commit -m "feat(tools): migrate autonomous repair toolkit to per-tool files"
```

---

### Task 6.3: Migrate ops-only mutating tools

**Files (create):**
- `worker/agent/tools/cancel_order.py`
- `worker/agent/tools/adjust_inventory.py`

Both are OPS-only, MUTATING with `ops_confirmation` guard. Both inject `tool_use_id` into their input — that becomes a kwarg on the sub-activity from the tool body (no `make_impl_input` needed).

- [ ] **Step 1: Create `worker/agent/tools/cancel_order.py`**

```python
"""Cancel an order's workflow."""
from __future__ import annotations

from datetime import timedelta

from shared.agent_harness import ToolCategory, ToolCtx, ops_tool
from shared.models import CancelOrderInput, CancelOrderResult
from worker.agent.guards import ops_confirmation
from worker.agent.tool_args import CancelOrderArgs


_READ_TIMEOUT = timedelta(seconds=10)


async def _cancel_order_workflow(input_: CancelOrderInput) -> CancelOrderResult:
    """Issue a cancellation signal/termination to the target order workflow.

    Implementation lifted from the legacy worker.activities.ops_activities.cancel_order.
    """
    # PORT FROM EXISTING worker/activities/ops_activities.py:cancel_order
    ...


@ops_tool(
    category=ToolCategory.MUTATING,
    guards=(ops_confirmation,),
    timeout=_READ_TIMEOUT,
)
async def cancel_order(args: CancelOrderArgs, ctx: ToolCtx) -> str:
    """Cancel an order's workflow. Confirmation required from the operator before this
    runs. Naturally idempotent — cancelling an already-cancelled order is a no-op."""
    result = await ctx.activity(
        _cancel_order_workflow,
        CancelOrderInput(
            order_id=args.order_id,
            reason=args.reason,
            tool_use_id=ctx.tool_use_id,
        ),
        summary=f"Cancel order {args.order_id} (reason: {args.reason})",
        start_to_close_timeout=_READ_TIMEOUT,
    )
    if result.cancelled:
        return f"Order {args.order_id} cancelled. {result.note}"
    return f"Order {args.order_id} cancellation declined or already done. {result.note}"
```

The `_cancel_order_workflow` body must be ported from the existing `worker/activities/ops_activities.py:cancel_order`. Open that file, copy the body, replace `tool_use_id=...` references to use `input_.tool_use_id`.

- [ ] **Step 2: Create `worker/agent/tools/adjust_inventory.py`**

Same pattern. Sub-activity `_adjust_oms_inventory(input_: AdjustInventoryInput)` ported from existing `ops_activities.py:adjust_inventory`. Tool body builds the `AdjustInventoryInput` (with `tool_use_id=ctx.tool_use_id`) and calls the sub-activity.

```python
"""Adjust the OMS in_stock count for a book."""
from __future__ import annotations

from datetime import timedelta

from shared.agent_harness import ToolCategory, ToolCtx, ops_tool
from shared.models import AdjustInventoryInput, AdjustInventoryResult
from worker.agent.guards import ops_confirmation
from worker.agent.tool_args import AdjustInventoryArgs


_READ_TIMEOUT = timedelta(seconds=10)


async def _adjust_oms_inventory(input_: AdjustInventoryInput) -> AdjustInventoryResult:
    """Apply a stock delta to the OMS catalog row for a book.

    Implementation lifted from the legacy worker.activities.ops_activities.adjust_inventory.
    """
    # PORT FROM EXISTING worker/activities/ops_activities.py:adjust_inventory
    ...


@ops_tool(
    category=ToolCategory.MUTATING,
    guards=(ops_confirmation,),
    timeout=_READ_TIMEOUT,
)
async def adjust_inventory(args: AdjustInventoryArgs, ctx: ToolCtx) -> str:
    """Adjust the OMS in_stock count for a book by a positive or negative delta.
    Confirmation required."""
    result = await ctx.activity(
        _adjust_oms_inventory,
        AdjustInventoryInput(
            book_id=args.book_id,
            delta=args.delta,
            reason=args.reason,
            tool_use_id=ctx.tool_use_id,
        ),
        summary=f"Adjust {args.book_id} stock by {args.delta:+d} ({args.reason})",
        start_to_close_timeout=_READ_TIMEOUT,
    )
    if result.applied:
        return f"Inventory adjusted: {args.book_id} now {result.new_in_stock} in_stock. {result.note}"
    return f"Inventory adjustment for {args.book_id} not applied. {result.note}"
```

- [ ] **Step 3: Verify and commit**

```bash
python -c "
from worker.agent.tools import OPS_TOOLS
got = {t.name for t in OPS_TOOLS}
print('cancel_order present:', 'cancel_order' in got)
print('adjust_inventory present:', 'adjust_inventory' in got)
"
```
Expected: both `True`.

```bash
git add worker/agent/tools/
git commit -m "feat(tools): migrate ops-only mutating tools to per-tool files"
```

---

### Task 6.4: Migrate Slack output and ops HITL_INTERACTION tools

**Files (create):**
- `worker/agent/tools/post_rich_reply.py` (SLACK_OUTPUT, no guards)
- `worker/agent/tools/post_order_picker.py` (HITL_INTERACTION)

#### Step 1: Create `worker/agent/tools/post_rich_reply.py`

The existing `post_rich_thread_reply` activity stays a real activity (Slack API call). The tool body assembles the input from `args` and `ctx.channel`/`ctx.thread_ts`.

```python
"""Post a richly-formatted reply in the ops thread using Slack Block Kit."""
from __future__ import annotations

from datetime import timedelta

from shared.agent_harness import ToolCategory, ToolCtx, ops_tool
from shared.models import PostRichThreadReplyInput, PostThreadReplyResult
from worker.agent.tool_args import PostRichReplyArgs


_DEFAULT_TIMEOUT = timedelta(seconds=30)


async def _post_blocks(input_: PostRichThreadReplyInput) -> PostThreadReplyResult:
    """Post a Block-Kit reply to Slack.

    Implementation lifted from the legacy worker.activities.ops_activities.post_rich_thread_reply.
    """
    # PORT FROM EXISTING worker/activities/ops_activities.py:post_rich_thread_reply
    ...


@ops_tool(category=ToolCategory.SLACK_OUTPUT, timeout=_DEFAULT_TIMEOUT)
async def post_rich_reply(args: PostRichReplyArgs, ctx: ToolCtx) -> str:
    """Post a richly-formatted reply in the thread using Slack Block Kit. Use this when
    plain Slack-mrkdwn isn't enough — comparisons across many fields, multi-section
    breakdowns, key-value lists, or anything where you want headers/dividers/contextual
    footers. Pass a list of Block Kit block objects. Section text uses Slack mrkdwn
    (single-asterisk *bold*, _italic_, `code`, <https://url|label>) — NOT Markdown.
    No tables — use a section with `fields` for columns. On error returns is_error=True.
    When you call this tool, do NOT also include a redundant prose response — let the
    rich reply speak for itself."""
    assert ctx.channel and ctx.thread_ts, "post_rich_reply requires Slack ctx"
    result = await ctx.activity(
        _post_blocks,
        PostRichThreadReplyInput(
            channel=ctx.channel,
            thread_ts=ctx.thread_ts,
            blocks=args.blocks,
            fallback_text=args.fallback_text,
        ),
        summary="Post a Block-Kit-formatted reply in the ops thread.",
        start_to_close_timeout=_DEFAULT_TIMEOUT,
    )
    if result.is_error:
        return f"Could not post rich reply: {result.error_message}"
    return f"Rich reply posted (message_ts={result.message_ts})."
```

#### Step 2: Create `worker/agent/tools/post_order_picker.py`

This is the most workflow-orchestrated tool — it calls `list_orders` (now a sub-activity inside that tool's file, but here we just call the underlying handler), posts a picker via activity, awaits a future on `ctx.pending_actions[ctx.tool_use_id]`, then collapses the buttons. Port from `worker/agent/interactions.py:post_order_picker_interaction`.

```python
"""Post an interactive order picker in the ops thread and await operator selection."""
from __future__ import annotations

import asyncio
from datetime import timedelta

from shared.agent_harness import ToolCategory, ToolCtx, ops_tool
from shared.models import (
    CollapseButtonsInput,
    ListOrdersInput,
    ListOrdersResult,
    PickerOption,
    PostCardResult,
    PostOrderPickerInput,
)
from worker.agent.tool_args import PostOrderPickerArgs


_SLACK_TIMEOUT = timedelta(seconds=30)
_READ_TOOL_TIMEOUT = timedelta(seconds=10)


async def _list_orders_for_picker(input_: ListOrdersInput) -> ListOrdersResult:
    """Query Temporal Visibility for orders matching the filter.

    Same body as worker.activities.ops_activities.list_orders.
    """
    # PORT FROM EXISTING worker/activities/ops_activities.py:list_orders
    ...


async def _post_picker(input_: PostOrderPickerInput) -> PostCardResult:
    """Post a Block-Kit dropdown of orders to Slack.

    Implementation lifted from worker.activities.ops_activities.post_order_picker.
    """
    # PORT FROM EXISTING worker/activities/ops_activities.py:post_order_picker
    ...


async def _collapse_buttons(input_: CollapseButtonsInput) -> None:
    """Replace the picker's button row with a static summary line.

    Implementation lifted from worker.activities.ops_activities.collapse_buttons.
    """
    # PORT FROM EXISTING worker/activities/ops_activities.py:collapse_buttons
    ...


@ops_tool(category=ToolCategory.HITL_INTERACTION)
async def post_order_picker(args: PostOrderPickerArgs, ctx: ToolCtx) -> str:
    """Post an interactive dropdown of in-flight orders in the thread and return the
    order_id the operator selects. Use when the operator should choose which order
    to act on. Returns the selected order_id."""
    assert ctx.channel and ctx.thread_ts, "post_order_picker requires Slack ctx"

    list_result = await ctx.activity(
        _list_orders_for_picker,
        ListOrdersInput(status=args.status_filter),
        summary=f"List orders to populate the picker (status filter: {args.status_filter or 'any'}).",
        start_to_close_timeout=_READ_TOOL_TIMEOUT,
    )
    if not list_result.orders:
        return "No in-flight orders match the requested filter."

    options = [
        PickerOption(value=order.order_id, label=f"{order.order_id} ({order.order_status})")
        for order in list_result.orders[:25]
    ]

    future: asyncio.Future[str] = asyncio.Future()
    ctx.pending_actions[ctx.tool_use_id] = future
    try:
        post_result = await ctx.activity(
            _post_picker,
            PostOrderPickerInput(
                channel=ctx.channel,
                thread_ts=ctx.thread_ts,
                workflow_id=__import__("temporalio").workflow.info().workflow_id,
                tool_use_id=ctx.tool_use_id,
                prompt=args.prompt,
                options=options,
            ),
            summary="Post the order-picker dropdown to Slack.",
            start_to_close_timeout=_SLACK_TIMEOUT,
        )
        if post_result.is_error:
            return f"Could not post picker: {post_result.error_message}"
        selected = await future
    finally:
        ctx.pending_actions.pop(ctx.tool_use_id, None)

    await ctx.activity(
        _collapse_buttons,
        CollapseButtonsInput(
            channel=ctx.channel,
            message_ts=post_result.message_ts,
            summary_line=f"📌 Selected: {selected}",
        ),
        summary=f"Collapse picker buttons after operator selected {selected}.",
        start_to_close_timeout=_SLACK_TIMEOUT,
    )
    return f"Operator selected order_id={selected}"
```

(Replace the `__import__("temporalio").workflow.info().workflow_id` with a clean `from temporalio import workflow` at the top and `workflow.info().workflow_id` — written this way only to keep the example readable inline.)

- [ ] **Step 3: Verify and commit**

```bash
python -c "
from worker.agent.tools import OPS_TOOLS
got = {t.name for t in OPS_TOOLS}
print('post_rich_reply present:', 'post_rich_reply' in got)
print('post_order_picker present:', 'post_order_picker' in got)
"
```
Expected: both `True`.

```bash
git add worker/agent/tools/
git commit -m "feat(tools): migrate Slack output and ops picker to per-tool files"
```

---

### Task 6.5: Migrate REPAIR-only HITL tools

**Files (create):**
- `worker/agent/tools/request_customer_confirmation.py`
- `worker/agent/tools/escalate_to_human.py`

These are workflow-orchestrated. Port directly from `worker/agent/interactions.py` — replace `tool_use, agent_ctx` parameters with `args, ctx` (and unwrap args from tool_use.input internally if needed). The tool bodies start child workflows and await results.

#### Step 1: Create `worker/agent/tools/request_customer_confirmation.py`

```python
"""Spawn CustomerConfirmationWorkflow and return the customer's decision."""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

from shared.agent_harness import ToolCategory, ToolCtx, repair_tool

with workflow.unsafe.imports_passed_through():
    from shared.models import (
        CustomerConfirmationInput,
        CustomerConfirmationOption,
        OrderRepairInput,
    )
    from worker.agent.repair_state import CustomerDenial, RepairAgentState
    from worker.agent.tool_args import RequestCustomerConfirmationArgs
    from worker.workflows.customer_confirmation_workflow import CustomerConfirmationWorkflow


@repair_tool(category=ToolCategory.HITL_INTERACTION)
async def request_customer_confirmation(
    args: RequestCustomerConfirmationArgs, ctx: ToolCtx,
) -> str:
    """Ask the ordering customer directly to attest, accept, or confirm something whose answer
    is itself the resolution — there is no follow-up tool to gate. Use for:
    (a) Ministry of Magic approval / Form 27B/6 for Restricted Publications,
    (b) age-verification or Restricted Section credential attestations,
    (c) accepting an extended delivery window,
    (d) other customer-action problems where the customer's confirmation IS the action.
    Do NOT use this for substitutions — call substitute_item directly; the harness will
    ask the customer for substitution approval automatically.
    The customer gets an email with Approve/Deny links AND sees the same prompt on their
    /orders/:id page. Returns 'approved' / 'denied' / 'timeout'. On 'denied' or 'timeout'
    the order will be cancelled."""
    repair_input: OrderRepairInput = ctx.input

    customer_result = await workflow.execute_child_workflow(
        CustomerConfirmationWorkflow.run,
        CustomerConfirmationInput(
            order_id=repair_input.order_id,
            order_input=repair_input.order_input,
            question=args.question,
            description=args.description,
            proposed_action=args.proposed_action,
            options=[
                CustomerConfirmationOption(value="approve", label=args.approve_label),
                CustomerConfirmationOption(value="deny",    label=args.deny_label),
            ],
        ),
        id=f"customer-confirm-{repair_input.order_id}",
        task_queue="flourish-blotts-oms",
        execution_timeout=timedelta(hours=25),
    )

    if customer_result.status in ("denied", "timeout"):
        state: RepairAgentState = ctx.state
        state.customer_denial = CustomerDenial(
            status=customer_result.status,
            note=(
                customer_result.note
                or ("Customer denied." if customer_result.status == "denied"
                    else "Customer did not respond.")
            ),
        )

    note_suffix = f" Note: {customer_result.note}" if customer_result.note else ""
    return (
        f"Customer decision: {customer_result.status}"
        f" (via {customer_result.source or 'n/a'})." + note_suffix
    )
```

#### Step 2: Create `worker/agent/tools/escalate_to_human.py`

This is the largest port. The tool body spawns SlackConversationWorkflow, then if approved, executes each plan step by **calling the relevant tool's body directly** (since tool bodies are now plain Python coroutines that are accessible as Python imports).

```python
"""Escalate to ops via Slack and execute the approved plan steps."""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

from shared.agent_harness import ToolCategory, ToolCtx, repair_tool

with workflow.unsafe.imports_passed_through():
    from shared.models import (
        OrderRepairInput,
        RepairPlan,
        RepairPlanStep,
        SlackConversationInput,
        SlackConversationResult,
    )
    from worker.agent.repair_state import EscalationOutcome, RepairAgentState
    from worker.agent.tool_args import EscalateToHumanArgs
    from worker.agent.validator import validate_plan_steps
    from worker.config import SLACK_CHANNEL_ID
    from worker.workflows.slack_conversation_workflow import SlackConversationWorkflow


async def _execute_plan_step(step: RepairPlanStep, order_id: str, ctx: ToolCtx) -> str:
    """Look up the tool corresponding to step.tool and invoke its body with the
    persisted args. Plan steps reference tools by name; we look them up in the
    REPAIR_TOOLS registry. If no matching tool, surface the unknown-tool case
    just as the legacy execute_approved_plan_step did.
    """
    from worker.agent.tools import REPAIR_TOOLS

    if not step.tool:
        return f"Executed plan step '{step.action}': {step.description}"

    tool_def = next((td for td in REPAIR_TOOLS if td.name == step.tool), None)
    if tool_def is None or tool_def.body is None:
        return f"Unknown tool '{step.tool}' — no action taken."

    args_dict = dict(step.tool_args or {})
    args_dict.setdefault("order_id", order_id)
    try:
        args = tool_def.args_model(**args_dict)
    except Exception as e:
        return f"Invalid args for plan step '{step.tool}': {e}"
    try:
        return await tool_def.body(args, ctx)
    except Exception as e:
        return f"Plan step '{step.tool}' failed: {e}"


@repair_tool(category=ToolCategory.HITL_INTERACTION, terminates_loop=True)
async def escalate_to_human(args: EscalateToHumanArgs, ctx: ToolCtx) -> str:
    """Escalate to a Flourish & Blotts OPS OPERATOR via Slack — only for decisions an operator
    can resolve within the shop, without waiting on the customer. Use for:
    (a) approving a large refund above the agent's authority,
    (b) overriding an automated fraud or security hold,
    (c) authorising a one-off manual workaround,
    (d) edge cases requiring human judgment that don't depend on the customer submitting
    anything. The proposed_plan must be EXECUTABLE end-to-end at approval time — no
    'wait for the customer to submit X' steps. Anything customer-driven belongs in
    request_customer_confirmation instead. Calling this tool ends the repair turn — the
    operator's decision is the final resolution."""
    repair_input: OrderRepairInput = ctx.input
    state: RepairAgentState = ctx.state

    proposed_plan = RepairPlan(
        steps=[
            RepairPlanStep(
                action=step.action,
                description=step.description,
                tool=step.tool,
                tool_args=step.tool_args,
            )
            for step in args.proposed_plan
        ],
        rationale=args.rationale,
        urgency=args.urgency,
    )

    slack_result: SlackConversationResult = await workflow.execute_child_workflow(
        SlackConversationWorkflow,
        SlackConversationInput(
            order_id=repair_input.order_id,
            order_input=repair_input.order_input,
            failure=repair_input.failure,
            initial_plan=proposed_plan,
            slack_channel=SLACK_CHANNEL_ID,
        ),
        id=f"slack-conv-{repair_input.order_id}",
        task_queue="flourish-blotts-oms",
        execution_timeout=timedelta(hours=25),
    )

    plan_steps_executed: list[str] = []
    skip_note = ""
    if slack_result.status == "approved" and slack_result.final_plan:
        report = validate_plan_steps(slack_result.final_plan.steps)
        for step in report.executable:
            step_result = await _execute_plan_step(step, repair_input.order_id, ctx)
            plan_steps_executed.append(f"{step.action}: {step_result}")
        if report.skipped:
            for _idx, skipped_step, reason in report.skipped:
                plan_steps_executed.append(f"(skipped) {skipped_step.action}: {reason}")
            skip_note = (
                f" Note: {len(report.skipped)} plan step(s) skipped "
                f"(non-executable): {report.skip_summary}."
            )

    state.escalation_outcome = EscalationOutcome(
        slack_result=slack_result,
        plan_steps_executed=plan_steps_executed,
        skip_note=skip_note,
    )
    return f"Escalation {slack_result.status}.{skip_note}"
```

- [ ] **Step 3: Verify and commit**

```bash
python -c "
from worker.agent.tools import REPAIR_TOOLS
got = {t.name for t in REPAIR_TOOLS}
print('request_customer_confirmation present:', 'request_customer_confirmation' in got)
print('escalate_to_human present:', 'escalate_to_human' in got)
"
```
Expected: both `True`.

```bash
git add worker/agent/tools/
git commit -m "feat(tools): migrate REPAIR HITL tools to per-tool files"
```

---

### Task 6.6: Migrate `substitute_item`

The trickiest tool. REPAIR variant validates + mutates workflow state. OPS variant is the error-shim. Both go in one file with stacked decorators using `name="substitute_item"` overrides on each (since the function names differ).

- [ ] **Step 1: Create `worker/agent/tools/substitute_item.py`**

```python
"""substitute_item — REPAIR variant validates and stages the swap on workflow
state; OPS variant is a non-functional error shim (interactions in OPS are
not the right path for this action)."""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

from shared.agent_harness import ToolCategory, ToolCtx, ops_tool, repair_tool

with workflow.unsafe.imports_passed_through():
    from shared.catalog import get_book_by_id
    from shared.models import OrderRepairInput
    from worker.agent.guards import (
        ops_confirmation,
        substitute_item_customer_confirmation,
    )
    from worker.agent.repair_state import RepairAgentState
    from worker.agent.tool_args import SubstituteItemArgs


_DEFAULT_TIMEOUT = timedelta(seconds=30)


@repair_tool(
    name="substitute_item",
    category=ToolCategory.MUTATING,
    guards=(substitute_item_customer_confirmation,),
    timeout=_DEFAULT_TIMEOUT,
)
async def substitute_item_repair(args: SubstituteItemArgs, ctx: ToolCtx) -> str:
    """Commit a substitution of the customer's ordered book with a different in-stock book.
    The harness automatically asks the customer for approval (via email + on the order page)
    before the substitution is applied — Claude does NOT need to ask first via
    request_customer_confirmation. If the customer denies, this tool returns an error reason
    and you may propose a different substitute or escalate. The substitute must exist in the
    catalog and have enough physical stock; otherwise the tool returns an ERROR result.
    After a successful substitute_item, call update_order_status('repaired', ...) once and
    end your turn."""
    repair_input: OrderRepairInput = ctx.input
    state: RepairAgentState = ctx.state

    substitute_book = get_book_by_id(args.substitute_item_id)
    if substitute_book is None:
        return (
            f"ERROR: substitute item_id {args.substitute_item_id!r} not found "
            "in the catalog. Pick a valid book id."
        )
    if substitute_book.physical_count < repair_input.order_input.quantity:
        return (
            f"ERROR: substitute '{substitute_book.title}' has only "
            f"{substitute_book.physical_count} physically on the shelf "
            f"(need {repair_input.order_input.quantity}). Pick another."
        )

    state.staged_substitution = (
        args.original_item_id,
        args.substitute_item_id,
        args.reason,
    )
    return (
        f"Order {repair_input.order_id}: substitution committed — "
        f"'{args.original_item_id}' → '{args.substitute_item_id}' "
        f"('{substitute_book.title}'). Reason: {args.reason}. The order will be "
        "repackaged with the substituted book and dispatched normally."
    )


@ops_tool(
    name="substitute_item",
    category=ToolCategory.MUTATING,
    guards=(ops_confirmation,),
    timeout=_DEFAULT_TIMEOUT,
)
async def substitute_item_ops(args: SubstituteItemArgs, ctx: ToolCtx) -> str:
    """Replace a book in an order with a substitute. Confirmation required.
    Only valid for orders currently in repair."""
    return (
        f"ERROR: substitute_item must be handled in the repair workflow, not from ops. "
        f"The substitution did NOT take effect for order {args.order_id}. "
        "Use a repair-flow path instead."
    )
```

- [ ] **Step 2: Verify**

```bash
python -c "
from worker.agent.tools import REPAIR_TOOLS, OPS_TOOLS
print('substitute_item in REPAIR:', any(t.name == 'substitute_item' for t in REPAIR_TOOLS))
print('substitute_item in OPS:', any(t.name == 'substitute_item' for t in OPS_TOOLS))
"
```
Expected: both `True`.

- [ ] **Step 3: Final inventory check — all 21 tools migrated**

```bash
python -c "
from worker.agent.tools import REPAIR_TOOLS, OPS_TOOLS
expected_repair = {
    'list_inventory', 'check_inventory', 'verify_customer_credentials',
    'apply_containment_charm', 'dispatch_house_elf', 'reroute_via_floo',
    'update_order_status', 'contact_customer', 'substitute_item',
    'request_customer_confirmation', 'escalate_to_human',
}
expected_ops = {
    'list_inventory', 'check_inventory', 'verify_customer_credentials',
    'apply_containment_charm', 'dispatch_house_elf', 'reroute_via_floo',
    'update_order_status', 'contact_customer', 'substitute_item',
    'list_orders', 'describe_order', 'describe_workflow',
    'get_workflow_history', 'aggregate_repair_failures', 'get_book',
    'cancel_order', 'adjust_inventory',
    'post_rich_reply', 'post_order_picker',
}
got_repair = {t.name for t in REPAIR_TOOLS}
got_ops = {t.name for t in OPS_TOOLS}
print('REPAIR missing:', expected_repair - got_repair)
print('REPAIR extra:', got_repair - expected_repair)
print('OPS missing:', expected_ops - got_ops)
print('OPS extra:', got_ops - expected_ops)
print(f'Counts: REPAIR={len(got_repair)} (expected 11), OPS={len(got_ops)} (expected 19)')
"
```
Expected: all four set lines empty; counts 11/19.

- [ ] **Step 4: Commit**

```bash
git add worker/agent/tools/
git commit -m "feat(tools): migrate substitute_item to per-tool file (final tool)"
```

---

## Task 7: Switch over imports

Now that the new `worker/agent/tools/` package is fully populated, the workflows and worker need to import `REPAIR_TOOLS` / `OPS_TOOLS` from the new location instead of the old modules. The OLD modules ALSO still register their ToolDefs against the same per-agent lists, so until they're deleted in Task 8, the registries would have duplicates. We fix this by switching imports to point at the new location and not importing the old modules at all.

**Files:**
- Modify: `worker/main.py`
- Modify: `worker/workflows/order_repair_workflow.py` (if it imports REPAIR_TOOLS)
- Modify: `worker/workflows/ops_agent_conversation_workflow.py` (if it imports OPS_TOOLS)

- [ ] **Step 1: Find current import sites**

```bash
grep -rn "REPAIR_TOOLS\|OPS_TOOLS" worker/ --include="*.py"
```

- [ ] **Step 2: Update each call site**

In each file that imports from the old `worker.agent.repair_tools` or `worker.agent.ops_tools`, change the import to:

```python
from worker.agent.tools import REPAIR_TOOLS, OPS_TOOLS
```

(Adjust to the names actually imported.)

- [ ] **Step 3: Update `worker/main.py`**

The Worker's `activities=[...]` list currently contains a number of ops handlers (`list_orders`, `cancel_order`, etc.) that were registered as `@activity.defn`. Those have moved into per-tool sub-activities and are dispatched dynamically. Check each name in the activities list:

- If the handler is now a `_*` sub-activity in some `worker/agent/tools/<tool>.py` file: REMOVE its import + its entry from the activities list.
- If the handler is still a `@activity.defn` called directly from a non-agent workflow (`post_thread_reply`, `post_confirmation_card`, `collapse_buttons`, `post_thread_closed_notice`, `post_initial_slack_message`, `post_slack_reply`, `process_conversation_message`, `send_customer_confirmation_email`, `process_payment`, `verify_credentials`, `pick_and_pack`, `dispatch_delivery`, `refund_payment`, `release_inventory_reservation`, `recall_delivery`, `call_claude`): KEEP it.

After the edit the activities list should be:

```python
activities=[
    process_payment,
    verify_credentials,
    pick_and_pack,
    dispatch_delivery,
    refund_payment,
    release_inventory_reservation,
    recall_delivery,
    call_claude,
    dispatch_tool_activity,
    post_initial_slack_message,
    post_slack_reply,
    process_conversation_message,
    send_customer_confirmation_email,
    post_confirmation_card,
    post_order_picker,
    collapse_buttons,
    post_thread_reply,
    post_thread_closed_notice,
],
```

(Note: `execute_approved_plan_step` is removed — its functionality is now inside `escalate_to_human`'s body.)

Make sure to remove the import line for `execute_approved_plan_step`.

- [ ] **Step 4: Smoke verify**

```bash
python -c "import worker.main; print('worker module loads')"
```
Expected: `worker module loads`

```bash
python -c "
from worker.agent.tools import REPAIR_TOOLS, OPS_TOOLS
from shared.agent_harness import TOOL_HANDLERS
print(f'REPAIR_TOOLS: {len(REPAIR_TOOLS)} (expect 11)')
print(f'OPS_TOOLS: {len(OPS_TOOLS)} (expect 19)')
print(f'TOOL_HANDLERS sub-activities: {sum(1 for k in TOOL_HANDLERS if \":\" in k)}')
"
```
Expected: 11, 19, and a positive number of sub-activities.

- [ ] **Step 5: Commit**

```bash
git add worker/main.py worker/workflows/
git commit -m "refactor(worker): switch imports to worker.agent.tools package"
```

---

## Task 8: Delete obsolete files and old activity handlers

**Files:**
- Delete: `worker/agent/repair_tools.py`
- Delete: `worker/agent/ops_tools.py`
- Delete: `worker/agent/interactions.py`
- Delete: `worker/activities/repair_activities.py` (no remaining callers)
- Modify: `worker/activities/ops_activities.py` — remove the handlers that moved into per-tool files; keep only the ones still referenced by non-tool workflows

- [ ] **Step 1: Delete the three obsolete agent modules**

```bash
git rm worker/agent/repair_tools.py
git rm worker/agent/ops_tools.py
git rm worker/agent/interactions.py
```

- [ ] **Step 2: Delete `worker/activities/repair_activities.py`**

```bash
git rm worker/activities/repair_activities.py
```

- [ ] **Step 3: Trim `worker/activities/ops_activities.py`**

Open the file and remove the function definitions (with their `@activity.defn`) for handlers now living as sub-activities in `worker/agent/tools/`. Specifically remove:

- `list_inventory`
- `get_book`
- `list_orders`
- `describe_order`
- `describe_workflow`
- `get_workflow_history`
- `aggregate_repair_failures`
- `cancel_order`
- `adjust_inventory`
- `post_rich_thread_reply`

Keep these (still called directly from non-agent workflows):

- `post_confirmation_card`
- `post_order_picker`
- `collapse_buttons`
- `post_thread_reply`
- `post_thread_closed_notice`

(Also keep any helpers they share, and keep `from temporalio import activity` etc.)

- [ ] **Step 4: Smoke verify**

```bash
python -c "import worker.main; print('ok')"
```
Expected: `ok`

```bash
python -c "
from worker.agent.tools import REPAIR_TOOLS, OPS_TOOLS
from shared.agent_harness import TOOL_HANDLERS
print(f'REPAIR_TOOLS: {len(REPAIR_TOOLS)} (expect 11)')
print(f'OPS_TOOLS: {len(OPS_TOOLS)} (expect 19)')
"
```
Expected: 11, 19.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor(worker): delete legacy tools/interactions modules"
```

---

## Task 9: Final verification

- [ ] **Step 1: Confirm no stale imports remain**

```bash
grep -rn "from worker.agent.repair_tools\|from worker.agent.ops_tools\|from worker.agent.interactions\|from worker.activities.repair_activities" worker/ shared/ --include="*.py"
```
Expected: no output (zero matches).

- [ ] **Step 2: Confirm policy still enforces — try registering an invalid tool**

```bash
python -c "
from datetime import timedelta
from pydantic import BaseModel
from shared.agent_harness import ops_tool, ToolCategory

class _Args(BaseModel):
    pass

try:
    @ops_tool(category=ToolCategory.MUTATING)  # missing ops_confirmation guard
    async def _bad_tool(args: _Args, ctx) -> str:
        'no description'
        return 'never'
    print('FAIL: should have raised')
except Exception as e:
    print(f'correctly rejected: {type(e).__name__}: {e}')
"
```
Expected: `correctly rejected: ToolPolicyError: ...`

- [ ] **Step 3: Walk the full TOOL_HANDLERS state**

```bash
python -c "
import worker.main  # forces full registration
from shared.agent_harness import TOOL_HANDLERS

tool_keys = sorted(k for k in TOOL_HANDLERS if ':' not in k)
sub_keys = sorted(k for k in TOOL_HANDLERS if ':' in k)
print(f'Tool-shaped entries (legacy <name>): {len(tool_keys)} → {tool_keys}')
print(f'Sub-activity entries (<file>:<func>): {len(sub_keys)}')
for k in sub_keys:
    print(f'  {k}')
"
```
Expected: tool-shaped entries should be EMPTY (or very small if some legacy `impl=` ToolDefs survived your migration). Sub-activity entries should number around the count of `_*` async helpers across the per-tool files.

- [ ] **Step 4: Run the demo end-to-end**

Per the repo's normal flow (start the worker, trigger a failing-order scenario, walk a repair flow, walk an ops-agent flow), confirm:

- Worker boots without error.
- A repair flow runs to completion. Tool calls show in workflow history with the tool body running inline (no per-tool ActivityTaskScheduled event for the tool itself), and any sub-activities show with `activity_type = <file>:<func>` and the `summary` you wrote at the call site.
- An ops-agent flow handles `cancel_order` (or any mutating tool) — the `ops_confirmation` guard fires, the operator confirmation flow plays out, then the tool body runs.
- An `escalate_to_human` flow runs the SlackConversation child and executes the approved plan via the new in-body `_execute_plan_step` helper.
- A `substitute_item` (REPAIR) flow runs the customer-confirmation guard, then the body validates and stages the substitution.

- [ ] **Step 5: If anything breaks, fix and commit**

If smoke testing surfaces issues, fix them with focused commits naming the specific tool or path that needed adjustment. Common likely issues:

- Forgotten `with workflow.unsafe.imports_passed_through():` blocks in tool files that import workflow-side types.
- Missing `_execute_plan_step` import path inside `escalate_to_human.py`.
- A sub-activity helper accidentally registered under a colliding name.

---

## Self-Review (run before considering this plan done)

- [x] **Spec coverage:** every section of `docs/superpowers/specs/2026-05-10-tool-decorator-redesign.md` maps to a task above.
  - One file per tool → Task 6
  - Decorator API → Task 3
  - Tool body shape → Task 4 (dispatch) + Task 6 (migrations)
  - ToolCtx → Task 1
  - ctx.activity() mechanics → Task 1 (`derive_activity_name`, `ToolCtx.activity`)
  - Sub-activity registration → Task 5 (auto-discovery)
  - Guard system retained → Task 2 (policy.validate_tool unchanged)
  - Folder discovery → Task 5
  - What changes / what's removed → Tasks 7 + 8
- [x] **Type consistency:** ToolCtx field names match across Task 1 (`ctx.tool_use`, `ctx.agent`, `ctx.tool_use_id`, `ctx.state`, `ctx.input`, `ctx.pending_actions`, `ctx.channel`, `ctx.thread_ts`, `ctx.activity()`) and Task 6 migrations.
- [x] **Placeholder scan:** the migration tasks contain explicit `# PORT FROM EXISTING ...` markers in the sub-activity bodies. These are not "TBD" — they direct the engineer to the exact source-of-truth location for the body. If your team prefers, I can expand each into a literal copy-paste of the legacy code, but the markers are intentional pointers, not gaps.
