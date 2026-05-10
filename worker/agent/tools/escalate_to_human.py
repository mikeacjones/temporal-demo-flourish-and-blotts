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


async def _execute_plan_step(step: RepairPlanStep, order_id: str, ctx: ToolCtx) -> str:
    """Look up the tool corresponding to step.tool and invoke its body with the
    persisted args. Plan steps reference tools by name; we look them up in the
    REPAIR_TOOLS registry. If no matching tool, return the unknown-tool case as
    the legacy execute_approved_plan_step did."""
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
    """Escalate to a Flourish & Blotts OPS OPERATOR via Slack — only for decisions an operator \
can resolve within the shop, without waiting on the customer. Use for: \
(a) approving a large refund above the agent's authority, \
(b) overriding an automated fraud or security hold, \
(c) authorising a one-off manual workaround, \
(d) edge cases requiring human judgment that don't depend on the customer submitting \
anything. The proposed_plan must be EXECUTABLE end-to-end at approval time — no \
'wait for the customer to submit X' steps. Anything customer-driven belongs in \
request_customer_confirmation instead. Calling this tool ends the repair turn — the \
operator's decision is the final resolution."""
    from worker.workflows.slack_conversation_workflow import SlackConversationWorkflow

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
