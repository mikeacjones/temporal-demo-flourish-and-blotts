"""Plan-validation layer between HITL approval and tool execution.

When a human approves a plan (from Slack, or — today, though not yet wired — from a
customer), each planned step gets validated here before being dispatched to an activity.
This prevents Claude (or a creative operator) from slipping through a tool name outside
the auto-execute allowlist or supplying malformed arguments.

The *list* of allowed tools lives in this module (not in tools.py) because the Claude
tool schema includes HITL-trigger tools (escalate_to_human, request_customer_confirmation)
which are deliberately NOT in the execution allowlist — those tools only make sense as
requests TO a human, not as steps executed from an approved plan.
"""
from __future__ import annotations

from dataclasses import dataclass
from pydantic import BaseModel, ValidationError, ConfigDict
from shared.models import RepairPlanStep


# ---------------------------------------------------------------------------
# Pydantic argument schemas for each executable tool.
# These mirror the JSON schemas in agent/tools.py but in a runtime-checked form.
# ---------------------------------------------------------------------------

class _StrictModel(BaseModel):
    # extra='forbid' so any stray arg Claude hallucinates is rejected rather than silently ignored.
    model_config = ConfigDict(extra="forbid")


class CheckInventoryArgs(_StrictModel):
    item_id: str


class ApplyContainmentCharmArgs(_StrictModel):
    order_id: str
    item_id: str


class DispatchHouseElfArgs(_StrictModel):
    order_id: str
    task: str


class RerouteViaFlooArgs(_StrictModel):
    order_id: str
    destination: str


class UpdateOrderStatusArgs(_StrictModel):
    order_id: str
    status: str
    message: str


class ContactCustomerArgs(_StrictModel):
    order_id: str
    message: str


class SubstituteItemArgs(_StrictModel):
    order_id: str
    original_item_id: str
    substitute_item_id: str
    reason: str


class VerifyCustomerCredentialsArgs(_StrictModel):
    customer_id: str
    requirement_type: str


# Executable tools only — HITL-trigger tools (escalate_to_human,
# request_customer_confirmation) are intentionally excluded.
TOOL_ARG_MODELS: dict[str, type[_StrictModel]] = {
    "check_inventory": CheckInventoryArgs,
    "apply_containment_charm": ApplyContainmentCharmArgs,
    "dispatch_house_elf": DispatchHouseElfArgs,
    "reroute_via_floo": RerouteViaFlooArgs,
    "update_order_status": UpdateOrderStatusArgs,
    "contact_customer": ContactCustomerArgs,
    "substitute_item": SubstituteItemArgs,
    "verify_customer_credentials": VerifyCustomerCredentialsArgs,
}

EXECUTABLE_TOOLS: frozenset[str] = frozenset(TOOL_ARG_MODELS.keys())


@dataclass
class PlanValidationReport:
    """Output of validate_plan_steps — which steps to run, which to skip and why."""
    executable: list[RepairPlanStep]
    skipped: list[tuple[int, RepairPlanStep, str]]  # (index, step, reason)

    @property
    def skip_summary(self) -> str:
        if not self.skipped:
            return ""
        return "; ".join(f"#{i} ({s.tool or s.action!r}): {r}" for i, s, r in self.skipped)


def validate_plan_steps(steps: list[RepairPlanStep]) -> PlanValidationReport:
    """Partition an approved plan into executable and skipped steps.

    An approved plan is treated as *advisory* from the human operator: executable
    steps (allowlisted tool + valid args) run; everything else is recorded as
    skipped with a reason. We deliberately do not fail the whole plan — the human
    approved it, and rejecting their approval just to protect against one bogus
    step would penalise them for free-form planning.

    Narrative-only steps (tool is None/empty) are also skipped — there's nothing
    to execute, but they represent real human intent so we log them.
    """
    executable: list[RepairPlanStep] = []
    skipped: list[tuple[int, RepairPlanStep, str]] = []

    for i, step in enumerate(steps):
        if not step.tool:
            skipped.append((i, step, "narrative-only step (no tool)"))
            continue

        model = TOOL_ARG_MODELS.get(step.tool)
        if model is None:
            skipped.append((i, step, f"tool {step.tool!r} not in executable allowlist"))
            continue

        try:
            model.model_validate(step.tool_args or {})
        except ValidationError as e:
            summary = "; ".join(
                f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}"
                for err in e.errors()
            )
            skipped.append((i, step, f"args failed validation — {summary}"))
            continue

        executable.append(step)

    return PlanValidationReport(executable=executable, skipped=skipped)
