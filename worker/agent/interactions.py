"""HITL_INTERACTION and workflow-state interactions for the agent harness.

Each interaction is a workflow-safe coroutine that returns a ToolResult.
HITL_INTERACTION tools have no impl; their interaction is the entire tool
body. Workflow-state-only tools (substitute_item in repair) are also
modelled as interactions because they do workflow-side work, not activity
work."""
from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from shared.agent_harness import AgentCtx
    from shared.catalog import get_book_by_id
    from shared.models import (
        ClaudeToolUse,
        CollapseButtonsInput,
        CustomerConfirmationInput,
        CustomerConfirmationOption,
        ListOrdersInput,
        OrderRepairInput,
        PickerOption,
        PostOrderPickerInput,
        RepairPlan,
        RepairPlanStep,
        SlackConversationInput,
        SlackConversationResult,
        ToolResult,
    )
    from worker.activities.ops_activities import (
        collapse_buttons,
        list_orders,
        post_order_picker,
    )
    from worker.activities.repair_activities import execute_approved_plan_step
    from worker.agent.repair_state import (
        CustomerDenial,
        EscalationOutcome,
        RepairAgentState,
    )
    from worker.agent.tool_args import (
        EscalateToHumanArgs,
        PostOrderPickerArgs,
        RequestCustomerConfirmationArgs,
        SubstituteItemArgs,
    )
    from worker.agent.validator import validate_plan_steps
    from worker.config import SLACK_CHANNEL_ID
    from worker.workflows.customer_confirmation_workflow import (
        CustomerConfirmationWorkflow,
    )
    from worker.workflows.slack_conversation_workflow import (
        SlackConversationWorkflow,
    )


SLACK_TIMEOUT = timedelta(seconds=30)
READ_TOOL_TIMEOUT = timedelta(seconds=10)


async def request_customer_confirmation_interaction(
    tu: ClaudeToolUse, ctx: AgentCtx,
) -> ToolResult:
    """Spawn CustomerConfirmationWorkflow to ask the customer for credential
    attestation, scope-change acceptance, etc. The customer's answer IS the
    resolution — there is no follow-up tool. On denial/timeout we record it
    on ctx.domain_state.customer_denial so the repair workflow can terminate
    as cancelled_by_customer."""
    args = RequestCustomerConfirmationArgs(**tu.input)
    repair_input: OrderRepairInput = ctx.domain_input

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
        state: RepairAgentState = ctx.domain_state
        state.customer_denial = CustomerDenial(
            status=customer_result.status,
            note=(
                customer_result.note
                or ("Customer denied." if customer_result.status == "denied"
                    else "Customer did not respond.")
            ),
        )

    note_suffix = f" Note: {customer_result.note}" if customer_result.note else ""
    return ToolResult(
        tool_use_id=tu.id,
        content=(
            f"Customer decision: {customer_result.status}"
            f" (via {customer_result.source or 'n/a'})." + note_suffix
        ),
    )


async def post_order_picker_interaction(
    tu: ClaudeToolUse, ctx: AgentCtx,
) -> ToolResult:
    """Post a Block-Kit dropdown of in-flight orders and return the
    operator's selection. The pending future is resolved by an
    OpsActionSignal carrying the selected order_id."""
    assert ctx.channel and ctx.thread_ts, "post_order_picker requires Slack ctx"
    args = PostOrderPickerArgs(**tu.input)

    list_result = await workflow.execute_activity(
        list_orders,
        ListOrdersInput(status=args.status_filter),
        start_to_close_timeout=READ_TOOL_TIMEOUT,
    )
    if not list_result.orders:
        return ToolResult(
            tool_use_id=tu.id,
            content="No in-flight orders match the requested filter.",
            is_error=True,
        )
    options = [
        PickerOption(value=o.order_id, label=f"{o.order_id} ({o.order_status})")
        for o in list_result.orders[:25]
    ]

    future: asyncio.Future[str] = asyncio.Future()
    ctx.pending_actions[tu.id] = future
    try:
        post_result = await workflow.execute_activity(
            post_order_picker,
            PostOrderPickerInput(
                channel=ctx.channel,
                thread_ts=ctx.thread_ts,
                workflow_id=workflow.info().workflow_id,
                tool_use_id=tu.id,
                prompt=args.prompt,
                options=options,
            ),
            start_to_close_timeout=SLACK_TIMEOUT,
        )
        if post_result.is_error:
            return ToolResult(
                tool_use_id=tu.id,
                content=f"Could not post picker: {post_result.error_message}",
                is_error=True,
            )
        selected = await future
    finally:
        ctx.pending_actions.pop(tu.id, None)

    await workflow.execute_activity(
        collapse_buttons,
        CollapseButtonsInput(
            channel=ctx.channel,
            message_ts=post_result.message_ts,
            summary_line=f"📌 Selected: {selected}",
        ),
        start_to_close_timeout=SLACK_TIMEOUT,
    )
    return ToolResult(tool_use_id=tu.id, content=f"Operator selected order_id={selected}")


async def escalate_to_human_interaction(
    tu: ClaudeToolUse, ctx: AgentCtx,
) -> ToolResult:
    """Hand off to a Flourish & Blotts ops operator via Slack. Spawns
    SlackConversationWorkflow for multi-turn negotiation; if approved, runs
    the validated plan steps before returning. ToolDef.terminates_loop=True
    means run_agent_turn ends the loop after this tool runs successfully —
    the repair workflow then shapes its terminal result from
    ctx.domain_state.escalation_outcome."""
    args = EscalateToHumanArgs(**tu.input)
    repair_input: OrderRepairInput = ctx.domain_input
    state: RepairAgentState = ctx.domain_state

    proposed_plan = RepairPlan(
        steps=[
            RepairPlanStep(
                action=s.action,
                description=s.description,
                tool=s.tool,
                tool_args=s.tool_args,
            )
            for s in args.proposed_plan
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
            step_timeout = timedelta(seconds=120) if step.tool in (
                "dispatch_house_elf", "reroute_via_floo",
            ) else timedelta(seconds=30)
            step_result = await workflow.execute_activity(
                execute_approved_plan_step,
                args=[step, repair_input.order_id],
                start_to_close_timeout=step_timeout,
            )
            plan_steps_executed.append(f"{step.action}: {step_result}")
        if report.skipped:
            for _idx, sk, reason in report.skipped:
                plan_steps_executed.append(f"(skipped) {sk.action}: {reason}")
            skip_note = (
                f" Note: {len(report.skipped)} plan step(s) skipped "
                f"(non-executable): {report.skip_summary}."
            )

    state.escalation_outcome = EscalationOutcome(
        slack_result=slack_result,
        plan_steps_executed=plan_steps_executed,
        skip_note=skip_note,
    )
    return ToolResult(
        tool_use_id=tu.id,
        content=f"Escalation {slack_result.status}.{skip_note}",
    )


async def substitute_item_repair_interaction(
    tu: ClaudeToolUse, ctx: AgentCtx,
) -> ToolResult:
    """In-repair substitute_item: validates the substitute against the
    catalog and stages it on workflow state. Pure workflow-side work; no
    activity. The parent OrderWorkflow re-runs subsequent fulfilment steps
    against the substituted book once the repair workflow returns its
    OrderRepairResult.updated_order."""
    args = SubstituteItemArgs(**tu.input)
    repair_input: OrderRepairInput = ctx.domain_input
    state: RepairAgentState = ctx.domain_state

    sub_book = get_book_by_id(args.substitute_item_id)
    if sub_book is None:
        return ToolResult(
            tool_use_id=tu.id,
            is_error=True,
            content=(
                f"ERROR: substitute item_id {args.substitute_item_id!r} not found "
                "in the catalog. Pick a valid book id."
            ),
        )
    if sub_book.physical_count < repair_input.order_input.quantity:
        return ToolResult(
            tool_use_id=tu.id,
            is_error=True,
            content=(
                f"ERROR: substitute '{sub_book.title}' has only "
                f"{sub_book.physical_count} physically on the shelf "
                f"(need {repair_input.order_input.quantity}). Pick another."
            ),
        )

    state.staged_substitution = (
        args.original_item_id,
        args.substitute_item_id,
        args.reason,
    )
    return ToolResult(
        tool_use_id=tu.id,
        content=(
            f"Order {repair_input.order_id}: substitution committed — "
            f"'{args.original_item_id}' → '{args.substitute_item_id}' "
            f"('{sub_book.title}'). Reason: {args.reason}. The order will be "
            "repackaged with the substituted book and dispatched normally."
        ),
    )
