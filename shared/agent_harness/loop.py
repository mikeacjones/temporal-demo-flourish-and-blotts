"""run_agent_turn — the inner agentic loop, shared between OrderRepairWorkflow
and OpsAgentConversationWorkflow.

Workflow-safe: only uses workflow.execute_activity, awaited futures, and
child workflows (via interactions). Has zero CAN knobs and zero per-iteration
hooks — lifecycle concerns belong to the workflow envelope."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Literal

from pydantic import ValidationError
from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from shared.agent_harness.ctx import AgentCtx
    from shared.agent_harness.guards import Reject
    from shared.agent_harness.tooldef import ToolDef
    from shared.models import (
        CallClaudeInput,
        ClaudeResponse,
        ClaudeToolUse,
        ToolResult,
    )
    from worker.agent.executor import execute_tool_uses, to_tool_results_message
    from worker.activities.claude_activities import call_claude


DEFAULT_CLAUDE_RETRY = RetryPolicy(
    maximum_attempts=4,
    initial_interval=timedelta(seconds=1),
)


@dataclass
class ExecutedTool:
    name: str
    args: dict
    result_content: str
    is_error: bool


@dataclass
class TurnResult:
    stop_reason: Literal["end_turn", "terminating_tool", "max_iterations"]
    iterations: int
    tools_executed: list[ExecutedTool] = field(default_factory=list)
    final_text: str = ""
    terminating_tool_use: ClaudeToolUse | None = None


async def dispatch_tool(
    tu: ClaudeToolUse, td: ToolDef, ctx: AgentCtx,
) -> ToolResult:
    """Validate args, run guard chain, then run impl or interaction."""
    # 1. Pydantic-validate the args. ValidationError -> is_error=True.
    try:
        args = td.args_model(**tu.input)
    except ValidationError as e:
        return ToolResult(
            tool_use_id=tu.id,
            is_error=True,
            content=f"Invalid args for {tu.name}: {e}",
        )

    # 2. Guard chain. Reject -> return; Pass -> continue.
    for g in td.guards:
        try:
            outcome = await g(tu, ctx)
        except Exception as e:  # any exception in a guard becomes a Reject
            outcome = Reject(reason=f"Guard error: {e}")
        if isinstance(outcome, Reject):
            return ToolResult(tool_use_id=tu.id, content=outcome.reason)

    # 3. Run the impl (activity) or interaction (workflow coroutine).
    if td.impl is not None:
        try:
            if td.make_impl_input is not None:
                # Custom adapter — pass whatever it returns as the activity's
                # single positional arg. (Returning None is supported for
                # activities that take no args.)
                impl_input = td.make_impl_input(args, tu, ctx)
                if impl_input is None:
                    result = await workflow.execute_activity(
                        td.impl, start_to_close_timeout=td.timeout,
                    )
                else:
                    result = await workflow.execute_activity(
                        td.impl, impl_input, start_to_close_timeout=td.timeout,
                    )
            elif not td.args_model.model_fields:
                # Parameterless args model (e.g. list_inventory's
                # ListInventoryArgs) — call the activity with no positional
                # arg. Activities defined as `async def f() -> ...` would
                # otherwise raise TypeError.
                result = await workflow.execute_activity(
                    td.impl, start_to_close_timeout=td.timeout,
                )
            else:
                # Default: pass the validated Pydantic args through.
                result = await workflow.execute_activity(
                    td.impl, args, start_to_close_timeout=td.timeout,
                )
            return ToolResult(tool_use_id=tu.id, content=str(result))
        except Exception as e:
            return ToolResult(
                tool_use_id=tu.id,
                is_error=True,
                content=f"Tool {tu.name!r} failed: {e}",
            )

    if td.interaction is not None:
        try:
            return await td.interaction(tu, ctx)
        except Exception as e:
            return ToolResult(
                tool_use_id=tu.id,
                is_error=True,
                content=f"Tool {tu.name!r} interaction failed: {e}",
            )

    # validate_tool prevents this in practice; defensive only.
    return ToolResult(
        tool_use_id=tu.id,
        is_error=True,
        content=f"Tool {tu.name!r} has neither impl nor interaction",
    )


async def run_agent_turn(
    *,
    messages: list[dict],          # mutated in place — workflow owns the list
    system: str,
    tools: list[ToolDef],
    ctx: AgentCtx,
    max_iterations: int = 10,
    claude_timeout: timedelta = timedelta(seconds=60),
    claude_retry: RetryPolicy = DEFAULT_CLAUDE_RETRY,
) -> TurnResult:
    """Run the agent loop until end_turn / terminating_tool / max_iterations.

    Each iteration: call Claude, append the assistant message, dispatch any
    tool_uses (in parallel via the existing executor), append tool results.
    """
    by_name = {t.name: t for t in tools}
    anthropic_tools = [t.to_anthropic_schema() for t in tools]
    tools_executed: list[ExecutedTool] = []

    for i in range(max_iterations):
        response: ClaudeResponse = await workflow.execute_activity(
            call_claude,
            CallClaudeInput(messages=messages, system=system, tools=anthropic_tools),
            start_to_close_timeout=claude_timeout,
            retry_policy=claude_retry,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            return TurnResult(
                stop_reason="end_turn",
                iterations=i + 1,
                tools_executed=tools_executed,
                final_text=response.text,
            )

        async def dispatch(tu: ClaudeToolUse, _pending) -> ToolResult:
            return await dispatch_tool(tu, by_name[tu.name], ctx)

        results = await execute_tool_uses(
            response.tool_uses,
            pending_actions=ctx.pending_actions,
            activity_dispatch=dispatch,
        )
        messages.append(to_tool_results_message(results))

        for tu, r in zip(response.tool_uses, results):
            tools_executed.append(ExecutedTool(
                name=tu.name,
                args=tu.input,
                result_content=r.content,
                is_error=r.is_error,
            ))

        # Did any *successful* tool declare terminates_loop=True?
        terminator = next(
            (
                tu for tu, r in zip(response.tool_uses, results)
                if by_name[tu.name].terminates_loop and not r.is_error
            ),
            None,
        )
        if terminator is not None:
            return TurnResult(
                stop_reason="terminating_tool",
                iterations=i + 1,
                tools_executed=tools_executed,
                final_text=response.text,
                terminating_tool_use=terminator,
            )

    return TurnResult(
        stop_reason="max_iterations",
        iterations=max_iterations,
        tools_executed=tools_executed,
        final_text="",
    )
