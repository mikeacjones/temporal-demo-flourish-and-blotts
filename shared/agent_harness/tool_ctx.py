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
