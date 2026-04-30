"""Concrete guards used by the agent harness.

Workflow-safe: each guard is an async function that may call activities
or await futures resolved by signal handlers."""
from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from shared.agent_harness import AgentCtx, GuardKind, Pass, Reject, guard
    from shared.agent_harness.guards import GuardOutcome
    from shared.catalog import get_book_by_id
    from shared.models import (
        ClaudeToolUse,
        CollapseButtonsInput,
        CustomerConfirmationInput,
        CustomerConfirmationOption,
        OrderRepairInput,
        PostConfirmationCardInput,
    )
    from worker.activities.ops_activities import (
        collapse_buttons,
        post_confirmation_card,
    )
    from worker.agent.tool_args import SubstituteItemArgs
    from worker.workflows.customer_confirmation_workflow import CustomerConfirmationWorkflow


SLACK_TIMEOUT = timedelta(seconds=30)


def _confirmation_title_and_description(tu: ClaudeToolUse) -> tuple[str, str]:
    """Friendly title + description for the Block-Kit confirmation card.
    Encodes the agent's proposed action so the operator can see what they're
    approving."""
    name = tu.name
    args = tu.input
    if name == "cancel_order":
        return (
            f"Cancel order {args.get('order_id', '?')}?",
            f"Reason: {args.get('reason', '(none)')}",
        )
    if name == "adjust_inventory":
        try:
            delta = int(args.get("delta", 0))
        except (TypeError, ValueError):
            delta = 0
        return (
            f"Adjust inventory for {args.get('book_id', '?')} by {delta:+d}?",
            f"Reason: {args.get('reason', '(none)')}",
        )
    return (f"Run `{name}`?", f"Args: {args}")


@guard(kind=GuardKind.OPS_CONFIRMATION)
async def ops_confirmation(tu: ClaudeToolUse, ctx: AgentCtx) -> GuardOutcome:
    """Post a Slack confirmation card; await the operator's click.
    Pass -> proceed. Reject -> tool not run; reason fed back to Claude."""
    assert ctx.channel and ctx.thread_ts, "ops_confirmation requires a Slack ctx"
    title, description = _confirmation_title_and_description(tu)

    future: asyncio.Future[str] = asyncio.Future()
    ctx.pending_actions[tu.id] = future
    try:
        post_result = await workflow.execute_activity(
            post_confirmation_card,
            PostConfirmationCardInput(
                channel=ctx.channel,
                thread_ts=ctx.thread_ts,
                workflow_id=workflow.info().workflow_id,
                tool_use_id=tu.id,
                title=title,
                description=description,
            ),
            start_to_close_timeout=SLACK_TIMEOUT,
        )
        if post_result.is_error:
            return Reject(
                reason=f"Could not post confirmation card: {post_result.error_message}",
            )
        decision = await future
    finally:
        ctx.pending_actions.pop(tu.id, None)

    summary = "✅ Confirmed" if decision == "confirm" else "❌ Denied"
    await workflow.execute_activity(
        collapse_buttons,
        CollapseButtonsInput(
            channel=ctx.channel,
            message_ts=post_result.message_ts,
            summary_line=f"{summary} — {tu.name}",
        ),
        start_to_close_timeout=SLACK_TIMEOUT,
    )

    if decision == "confirm":
        return Pass()
    return Reject(
        reason=(
            f"Operator declined to run {tu.name}. This is a final decision — "
            "do not retry the same call. Acknowledge the operator's choice "
            "in your reply and proceed with any remaining tasks."
        ),
    )


@guard(kind=GuardKind.CUSTOMER_CONFIRMATION)
async def substitute_item_customer_confirmation(
    tu: ClaudeToolUse, ctx: AgentCtx,
) -> GuardOutcome:
    """Spawn CustomerConfirmationWorkflow asking the customer to approve a
    proposed substitution. If approved, the substitute_item interaction runs
    next. If denied/timeout, the tool is rejected with a reason Claude can
    use to pick another substitute or escalate."""
    args = SubstituteItemArgs(**tu.input)
    repair_input: OrderRepairInput = ctx.domain_input
    sub_book = get_book_by_id(args.substitute_item_id)
    sub_title = sub_book.title if sub_book else args.substitute_item_id

    customer_result = await workflow.execute_child_workflow(
        CustomerConfirmationWorkflow.run,
        CustomerConfirmationInput(
            order_id=repair_input.order_id,
            order_input=repair_input.order_input,
            question=f"Substitute with '{sub_title}'?",
            description=(
                f"'{repair_input.order_input.book_title}' is out of stock. "
                f"We can substitute with '{sub_title}'. Reason: {args.reason}"
            ),
            proposed_action=(
                f"substitute {args.original_item_id} -> {args.substitute_item_id}"
            ),
            options=[
                CustomerConfirmationOption(value="approve", label="Yes, substitute"),
                CustomerConfirmationOption(value="deny",    label="No, cancel my order"),
            ],
        ),
        # Match the ID format the API and request_customer_confirmation use
        # (`customer-confirm-{order_id}` — no per-call suffix). Temporal's
        # default child-workflow id_reuse_policy is ALLOW_DUPLICATE, so if the
        # customer denies one substitute and the agent retries with a
        # different one, the new attempt can reuse this ID once the previous
        # run closes.
        id=f"customer-confirm-{repair_input.order_id}",
        task_queue="flourish-blotts-oms",
        execution_timeout=timedelta(hours=25),
    )
    if customer_result.status == "approved":
        return Pass()
    return Reject(
        reason=(
            f"Customer {customer_result.status}: "
            f"{customer_result.note or '(no note)'}. "
            "Do not retry the same substitute; propose a different one or escalate."
        ),
    )
