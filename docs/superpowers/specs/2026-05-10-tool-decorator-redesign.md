# Tool Decorator Redesign — Spec

**Date:** 2026-05-10
**Status:** Draft (pending user review)
**Branch:** `feat/tool-decorator-redesign`

## Goal

Replace the dataclass-based `ToolDef` registration with a decorator-driven model so that adding a tool becomes "add a file in `worker/agent/tools/`, write the function, decorate it." Unify the impl/interaction split — every tool body is a workflow-side coroutine. Sub-activity helpers are plain async functions in the same file as the tool, dispatched through the existing dynamic activity.

## Motivation

The current `ToolDef(name=..., impl=..., interaction=..., make_impl_input=..., guards=..., timeout=...)` instantiation pattern lives in two long modules (`repair_tools.py`, `ops_tools.py`) that have grown crowded and force two distinct authoring shapes: "activity-backed impl tools" (typed args, registered as activities) and "workflow-side interaction tools" (`(tool_use, ctx)` signature, run inline). The split is structurally driven — interactions need workflow primitives like `start_child_workflow` — but the same author writing both has to keep two mental models. A decorator on a single tool-body shape collapses the asymmetry.

## Architecture

### One file per tool

```
worker/agent/tools/
    __init__.py                       # auto-imports every sibling module at worker startup
    check_inventory.py
    apply_containment_charm.py
    dispatch_house_elf.py
    reroute_via_floo.py
    update_order_status.py
    contact_customer.py
    verify_customer_credentials.py
    substitute_item.py                # OPS impl variant + REPAIR workflow-side body
    list_inventory.py
    list_orders.py
    describe_order.py
    describe_workflow.py
    get_workflow_history.py
    aggregate_repair_failures.py
    get_book.py
    cancel_order.py
    adjust_inventory.py
    post_rich_reply.py
    request_customer_confirmation.py  # spawns CustomerConfirmationWorkflow
    escalate_to_human.py              # spawns SlackConversationWorkflow
    post_order_picker.py              # awaits future on ctx.pending_actions
```

Each file declares one tool. `__init__.py` auto-imports siblings so decorators run at worker boot, populating the per-agent collections.

### Decorator API: stacked per-agent

A tool that exists in multiple agents stacks its agent decorators. Each decorator carries the per-agent metadata (category, guards, timeout). The function name is the wire-name for both Claude and the dispatcher. Description and args model are inferred — description from the docstring, args model from the first positional parameter's type annotation.

```python
@repair_tool(category=ToolCategory.AUTONOMOUS, timeout=timedelta(seconds=120))
@ops_tool(category=ToolCategory.MUTATING, guards=(ops_confirmation,), timeout=timedelta(seconds=120))
async def dispatch_house_elf(args: DispatchHouseElfArgs, ctx: ToolCtx) -> str:
    """Dispatch a house elf for magical manual intervention. Use for tasks
    requiring physical wizarding assistance: retrieving intercepted deliveries,
    capturing escaped magical items, emergency repackaging, or any on-site
    intervention."""
    outcome = await ctx.activity(
        _send_house_elf,
        args.task,
        summary=f"Dispatch a house elf to: {args.task}",
        start_to_close_timeout=timedelta(minutes=2),
        heartbeat_timeout=timedelta(seconds=15),
    )
    return f"Order {args.order_id}: House elf {outcome}"
```

Each decorator factory returns the same decorated function (so stacking just appends per-agent registrations). Identical-callable + same-name registration in `TOOL_HANDLERS` is a no-op (already supported).

### Tool body shape

```python
async def <tool_name>(args: <PydanticModel>, ctx: ToolCtx) -> str: ...
```

The body runs in workflow context (workflow-deterministic). It can:
- Read args (validated by the harness before the body runs)
- Call sub-activities via `ctx.activity(callable, *args, summary=..., ...)`
- Start child workflows (`workflow.execute_child_workflow`)
- Await futures from `ctx.pending_actions[tool_use.id]` (signal correlation)
- Mutate workflow state via `ctx.state` (the agent's domain state)
- Use `workflow.random()`, `workflow.now()`, `workflow.sleep()` for deterministic randomness/time

The body is responsible for returning a single string that becomes the tool result Claude sees.

### `ToolCtx`

```python
@dataclass
class ToolCtx:
    tool_use: ClaudeToolUse              # the originating tool_use; carries .id for signal correlation
    agent: AgentCtx                      # the existing agent context — pending_actions, channel/thread_ts, domain_input/state
    # Activity dispatch helper. Required `summary`. Forwards Temporal options.
    async def activity(
        self,
        callable: Callable[..., Awaitable[Any]],
        *args: Any,
        summary: str,
        start_to_close_timeout: timedelta = DEFAULT_ACTIVITY_TIMEOUT,
        heartbeat_timeout: timedelta | None = None,
        retry_policy: RetryPolicy | None = None,
        schedule_to_close_timeout: timedelta | None = None,
    ) -> Any: ...
```

The convenience accessors (`ctx.state`, `ctx.tool_use_id`, `ctx.repair_input`) are properties that delegate to `ctx.agent`. Tool authors don't need to navigate two-deep.

### `ctx.activity()` mechanics

The helper derives the activity's wire-name from the callable, then calls `workflow.execute_activity(name, args, summary=summary, ...)`. The dynamic `dispatch_tool_activity` (already registered) catches it on the activity worker side and resolves the callable.

**Name derivation:** `<file_basename>:<func_name>` derived from `callable.__module__.split(".")[-1]` and `callable.__name__`. Example: `_send_house_elf` defined in `worker/agent/tools/dispatch_house_elf.py` → activity_type `dispatch_house_elf:_send_house_elf`. This is namespace-stable as long as the file and function names don't change.

**Summary:** required keyword. Surfaced in workflow history as user-metadata on the activity event so the Temporal UI shows what each step is doing without needing to read the source. Per-call, so the same helper invoked from two tools can carry context-specific descriptions.

**Timeouts and retry policy:** forwarded directly to `workflow.execute_activity`. Sensible defaults for `start_to_close_timeout`; everything else opt-in.

### Sub-activity registration: zero ceremony

Sub-activity handlers are **plain async functions at module scope** in the tool's file. No decorator. The harness scans `worker/agent/tools/*.py` at worker startup and registers every module-scope async function (other than the `@*_tool`-decorated ones) into `TOOL_HANDLERS` keyed by `<file_basename>:<func_name>`.

```python
# tools/dispatch_house_elf.py
async def _send_house_elf(task: str) -> str:
    """Long-running stub — heartbeats while a notional elf retrieves an item."""
    elf = random.choice(["Dobby", "Kreacher", "Winky", "Hokey", "Mipsy"])
    total_steps = random.randint(5, 12)
    for step in range(total_steps):
        await asyncio.sleep(random.uniform(0.4, 0.9))
        activity.heartbeat(f"{elf} en route — step {step + 1}/{total_steps}")
    return random.choice([
        f"{elf} dispatched and completed: {task}",
        f"{elf} reports task complete. Requests no payment.",
    ])
```

The handler runs inside `dispatch_tool_activity`'s body, so Temporal's activity context is active and `activity.heartbeat()` works. Inside an activity, regular Python `random`, `asyncio.sleep`, network I/O, etc. are fine — activity code is not held to workflow determinism rules.

The first-positional-parameter type annotation drives wire-payload decoding. If the annotation is missing or unresolvable, args pass through as a raw dict (same fallback the existing dispatcher uses).

### Guard system: retained for category-policy enforcement

Guards remain a first-class concept but their purpose narrows. They are no longer the mechanism for "do this thing before the tool runs" — the tool body does that itself. They are now exclusively the policy-enforcement layer:

- A guard with `kind=GuardKind.CUSTOMER_CONFIRMATION` declares "this tool gates on customer approval."
- A guard with `kind=GuardKind.OPS_CONFIRMATION` declares the same for operator approval.
- `validate_tool` enforces that any `MUTATING` tool has at least one of those — at registration time, on worker boot.

A guard's body still runs before the tool body and may short-circuit with `Reject`. But ad-hoc per-tool pre-checks (catalog validation, stock checks) move into the tool body where they belong; they don't earn promotion to a guard unless they're reusable across tools and tied to a category invariant.

### Folder discovery

`worker/agent/tools/__init__.py` walks its directory at import time and imports every `.py` sibling (except itself and any `_*` private modules). The decorators run, populating `TOOL_HANDLERS` (sub-activities) and per-agent collections (`REPAIR_TOOLS: list[ToolDef]`, `OPS_TOOLS: list[ToolDef]`). Agent code imports these collections directly:

```python
from worker.agent.tools import REPAIR_TOOLS, OPS_TOOLS
```

## Data flow

```
Claude returns ClaudeResponse with tool_uses
        ↓
run_agent_turn iterates tool_uses
        ↓
dispatch_tool(tool_use, tool_def, agent_ctx)
  1. Pydantic-validate tool_use.input against tool_def.args_model
  2. Run guard chain — Reject short-circuits with the rejection reason
  3. Call tool_def.body(args, tool_ctx) — runs INLINE in workflow context
        ↓
Tool body, optionally calls ctx.activity(callable, args, summary=..., ...)
        ↓
ctx.activity → workflow.execute_activity(<file>:<func>, args, summary=..., ...)
        ↓
Activity task picked up by an activity worker; routes to dispatch_tool_activity
  4. Look up handler in TOOL_HANDLERS by activity_type
  5. Decode payload to handler's annotated arg type
  6. Invoke handler — any heartbeats, retries, sleeps live here
        ↓
Handler returns; activity completes; result flows back to the workflow
        ↓
Tool body returns a str; dispatch_tool wraps it in a ToolResult
        ↓
run_agent_turn appends the result to messages and continues the loop
```

## What changes / what's removed

| Today | After |
|---|---|
| `ToolDef.impl: Callable` (activity reference) | Removed |
| `ToolDef.interaction: Callable` (workflow coroutine) | Removed — collapsed into `ToolDef.body` |
| `ToolDef.make_impl_input: Callable` | Removed — tool body shapes its own activity inputs |
| `worker/agent/repair_tools.py` (~234 lines) | Deleted; per-tool files in `worker/agent/tools/` |
| `worker/agent/ops_tools.py` (~500 lines) | Deleted; per-tool files in `worker/agent/tools/` |
| `worker/agent/interactions.py` (~300 lines) | Deleted; bodies move into per-tool files |
| `worker/activities/repair_activities.py` per-tool handlers | Move to per-tool files; the per-tool `_*` helpers replace the existing implementations where simulation requires an activity, otherwise the work moves inline into the workflow body |
| `worker/activities/ops_activities.py` ops-tool handlers (`list_orders`, `cancel_order`, etc.) | Move into per-tool files |
| `register_tool(ToolDef(...))` | Replaced by `@<agent>_tool(...)` decorators |
| `validate_tool` policy check | Retained, runs per registered ToolDef as today |
| `dispatch_tool_activity` (dynamic) | Retained. The set of activity_types it handles changes: tools are no longer routed through it (they run inline as workflow code), but sub-activity handlers from per-tool files now do. |
| `TOOL_HANDLERS` | Retained. In the new model holds sub-activity handlers only, keyed by `<file>:<func>`. Tools themselves are workflow bodies and don't need a registry entry — they're called directly by `dispatch_tool` from the per-agent ToolDef collections. |
| `worker.activities.repair_activities.execute_approved_plan_step` | Removed. With tool bodies as workflow code, the `escalate_to_human` body that today calls `execute_approved_plan_step` for each step instead invokes the relevant tool's body directly (just a Python coroutine call inside the workflow). |
| `ToolDef` shape | `impl`/`interaction`/`make_impl_input` fields collapse to a single `body: Callable[[BaseModel, ToolCtx], Awaitable[str]]`. Other fields (`name`, `description`, `args_model`, `category`, `guards`, `timeout`, `terminates_loop`) unchanged. |

## Decisions

| Question | Decision | Rationale |
|---|---|---|
| Per-agent membership API | Stacked decorators (`@repair_tool` + `@ops_tool`) | Pythonic; per-agent metadata co-located cleanly; familiar from FastAPI/Click/pytest. |
| impl vs interaction | Unified — every tool body is a workflow coroutine | Honest about the underlying truth; eliminates the asymmetry. |
| Guard system | Retained for category-policy enforcement only | Harness-enforced determinism; can't be silently skipped by tool authors who forget. |
| Sub-activity decorator | None — plain functions, summary required at call site | Maximum DX; documentation surface guaranteed by the API rather than discipline. |
| Activity name derivation | `<file_basename>:<func_name>` (sub-activity); `<func_name>` (tool) | Human-readable in the Temporal UI; stable as long as files and functions aren't renamed. |
| Sub-activity dispatch | Reuses existing `dispatch_tool_activity` | Same registry, same dynamic-activity infrastructure; consistent end-to-end. |
| Folder location | `worker/agent/tools/` | Lives next to the rest of the agent harness consumers. |
| Description source | Function docstring | One source of truth; encourages writing actual documentation. |
| Args model source | First positional param's annotation | Already inferred today by `_resolve_arg_type`; reuses that machinery. |
| `ToolCtx` shape | `tool_use` + `agent` (existing AgentCtx) + `activity()` helper, with passthrough property accessors | Minimal new surface; tool authors don't navigate nested structures. |

## Out of scope

- Implementation step-by-step plan — that's the writing-plans output.
- Migration of in-flight workflow executions — branch is for the local demo; no live executions.
- Renaming `worker/agent/guards.py` or the guard kind enum — guards are unchanged.
- Per-tool unit tests — the codebase has no test infrastructure today; not expanding scope.
