"""ToolDef — the declarative description of a tool the agent can call."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import TYPE_CHECKING, Callable, Optional

from pydantic import BaseModel

if TYPE_CHECKING:
    from shared.agent_harness.guards import Guard, HitlInteraction
    from shared.agent_harness.ctx import AgentCtx
    from shared.models import ClaudeToolUse


class ToolCategory(str, Enum):
    READ              = "read"             # no required guards
    MUTATING          = "mutating"         # requires CUSTOMER_ or OPS_CONFIRMATION
    AUTONOMOUS        = "autonomous"       # state-changing but agent-driven (no required guards)
    HITL_INTERACTION  = "hitl_interaction" # the human's response IS the tool's result
    SLACK_OUTPUT      = "slack_output"     # posts Slack content (e.g. post_rich_reply)


@dataclass(frozen=True)
class ToolDef:
    """Declarative description of one agent-visible tool.

    Exactly one of `impl` or `interaction` must be set:
      * `impl` is an @activity.defn function — runs as a Temporal activity.
      * `interaction` is a workflow-safe coroutine — runs in the workflow,
        used for HITL_INTERACTION tools and workflow-state-only tools.
    """
    name: str
    description: str
    args_model: type[BaseModel]
    category: ToolCategory
    guards: tuple["Guard", ...] = ()
    impl: Optional[Callable] = None
    interaction: Optional["HitlInteraction"] = None
    timeout: timedelta = timedelta(seconds=30)
    # Only valid for HITL_INTERACTION tools. When True, run_agent_turn
    # terminates the loop after this tool runs successfully.
    terminates_loop: bool = False
    # Optional builder that produces the activity's input from
    # (validated args, tool_use, agent_ctx). Default is identity. Use this when the
    # activity needs harness metadata Claude doesn't supply (tool_use_id) or
    # when the activity's input shape differs from the tool's args (e.g. a
    # ToolCallInput wrapping name + args + order_id for execute_repair_tool).
    make_impl_input: Optional[
        Callable[[BaseModel, "ClaudeToolUse", "AgentCtx"], object]
    ] = None

    def to_anthropic_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.args_model.model_json_schema(),
        }
