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


@repair_tool(category=ToolCategory.HITL_INTERACTION)
async def request_customer_confirmation(
    args: RequestCustomerConfirmationArgs, ctx: ToolCtx,
) -> str:
    """Ask the ordering customer directly to attest, accept, or confirm something whose answer \
is itself the resolution — there is no follow-up tool to gate. Use for: \
(a) Ministry of Magic approval / Form 27B/6 for Restricted Publications, \
(b) age-verification or Restricted Section credential attestations, \
(c) accepting an extended delivery window, \
(d) other customer-action problems where the customer's confirmation IS the action. \
Do NOT use this for substitutions — call substitute_item directly; the harness will \
ask the customer for substitution approval automatically. \
The customer gets an email with Approve/Deny links AND sees the same prompt on their \
/orders/:id page. Returns 'approved' / 'denied' / 'timeout'. On 'denied' or 'timeout' \
the order will be cancelled."""
    from worker.workflows.customer_confirmation_workflow import CustomerConfirmationWorkflow

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
