"""OrderRepairWorkflow — agentic repair loop driven by the shared harness."""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from shared.agent_harness import AgentCtx, TurnResult, run_agent_turn
    from shared.catalog import get_book_by_id
    from shared.models import (
        OrderInput,
        OrderRepairInput,
        OrderRepairResult,
    )
    from worker.agent.repair_state import RepairAgentState
    from worker.agent.repair_tools import REPAIR_TOOLS


MAX_AGENT_ITERATIONS = 10


def _build_system_prompt(input: OrderRepairInput) -> str:
    order = input.order_input
    failure = input.failure
    return f"""You are the automated repair agent for Flourish & Blotts Wizarding Bookshop OMS.
An order has encountered a problem and you must diagnose and resolve it.

Order Details:
- Order ID: {order.order_id}
- Customer: {order.customer_name} ({order.customer_email})
- Book: {order.book_title} (ID: {order.book_id})
- Quantity: {order.quantity}
- Delivery: {order.delivery_method.replace('_', ' ').title()} to {order.delivery_address}

Failure:
- Step: {failure.step}
- Type: {failure.failure_type}
- Description: {failure.description}
- Context: {failure.context}

Instructions:
1. Diagnose the root cause of the failure.
2. Use available tools to auto-resolve if possible (containment charms, house elves, rerouting, etc.).
3. For ANYTHING THE CUSTOMER MUST DO OR DECIDE where the customer's response IS
   the resolution (no follow-up tool to gate), call request_customer_confirmation.
   This includes — IMPORTANTLY:
   * Delivery-method changes.
   * Ministry of Magic approval / Form 27B/6 for Restricted Publications.
   * Age-verification or Restricted Section credential checks that the customer must supply.
   * Any case where the resolution depends on the customer submitting paperwork, credentials,
     or a choice. These are customer-action problems, NOT ops problems, even when the law
     requires compliance — compliance is the customer's to satisfy, not ops'.
   Substitutions go through substitute_item directly (the tool's description
   covers how the customer approval is handled).
4. Call escalate_to_human ONLY when a Flourish & Blotts operator (not the customer)
   must make a policy or judgment call that can be resolved WITHIN THE SHOP: e.g.
   approving a large refund, waiving a fee, overriding an automated fraud hold,
   authorising a one-off manual workaround. The ops operator cannot make the customer
   submit paperwork or supply credentials — don't route those through Slack.
5. Be efficient and decisive. This is a live order.
6. Keep the wizarding theme — you're a proper magical OMS agent.

IMPORTANT — no "wait for customer" in ops-approved plans:
When you propose an ops plan via escalate_to_human, it must be runnable end-to-end at
ops-approval time. Do NOT include plan steps like "wait for the customer to submit X"
or "once the customer confirms Y" — ops approval doesn't pause for customer action.
If the resolution depends on a customer action, route it through request_customer_confirmation
instead (or as well), not through Slack.

STOP CONDITIONS:
- Once THIS ORDER's root-cause failure has been resolved, STOP. Do not escalate for
  follow-ups, welfare checks, post-mortems, or QoS notes that aren't strictly required
  to deliver the order.
- Call update_order_status(status='repaired', ...) at most ONCE, when the failure is
  resolved, and then end your turn. No additional tools after that.
- Never call request_customer_confirmation AND escalate_to_human for the same order —
  choose one channel based on who can actually resolve it.
- Use AT MOST ~5 tool calls per repair in the common case. On call 6+, escalate concisely
  or end the turn — don't keep poking."""


def _initial_user_message(input: OrderRepairInput) -> str:
    return (
        f"Order {input.order_id} has failed at step '{input.failure.step}'. "
        f"Failure: {input.failure.description}. "
        "Please diagnose and repair this order."
    )


def _build_updated_order(
    state: RepairAgentState, original: OrderInput,
) -> OrderInput | None:
    """Materialise the post-substitution OrderInput for the parent workflow."""
    if state.staged_substitution is None:
        return None
    _original_item_id, substitute_item_id, _reason = state.staged_substitution
    substitute_book = get_book_by_id(substitute_item_id)
    if substitute_book is None:
        # Shouldn't happen — the interaction validates this — but degrade
        # gracefully rather than poisoning the parent.
        return None
    return OrderInput(
        order_id=original.order_id,
        customer_name=original.customer_name,
        customer_email=original.customer_email,
        book_id=substitute_book.id,
        book_title=substitute_book.title,
        quantity=original.quantity,
        delivery_method=original.delivery_method,
        delivery_address=original.delivery_address,
        forced_failure=None,
    )


def _format_tools_executed(turn: TurnResult, state: RepairAgentState) -> list[str]:
    executed_tools = [
        f"{executed_tool.name}: {executed_tool.result_content}"
        + (" [error]" if executed_tool.is_error else "")
        for executed_tool in turn.tools_executed
    ]
    if state.escalation_outcome is not None:
        executed_tools.extend(state.escalation_outcome.plan_steps_executed)
    return executed_tools


def _shape_repair_result(
    turn: TurnResult, state: RepairAgentState, input: OrderRepairInput,
) -> OrderRepairResult:
    """Translate the harness TurnResult + RepairAgentState into the typed
    OrderRepairResult, including final search-attribute upserts."""
    tools_executed = _format_tools_executed(turn, state)

    # Customer denial wins over everything else — the customer said no.
    if state.customer_denial is not None:
        outcome = "customer_denied"
        workflow.upsert_search_attributes({
            "RepairOutcome": [outcome],
            "OrderStatus": ["cancelled_by_customer"],
        })
        return OrderRepairResult(
            status="cancelled",
            outcome=outcome,
            repair_steps_executed=tools_executed,
            requires_hitl=True,
            notes=state.customer_denial.note,
        )

    # Ops escalation drove a terminal decision (the agent loop terminated).
    if state.escalation_outcome is not None:
        slack = state.escalation_outcome.slack_result
        if slack.status == "approved":
            outcome = "ops_approved"
            workflow.upsert_search_attributes({
                "RepairOutcome": [outcome],
                "OrderStatus": ["repair_complete"],
            })
            return OrderRepairResult(
                status="resolved",
                outcome=outcome,
                repair_steps_executed=tools_executed,
                requires_hitl=True,
                updated_order=_build_updated_order(state, input.order_input),
                notes=(
                    f"Approved by {slack.decided_by or 'operator'}."
                    + state.escalation_outcome.skip_note
                ),
            )
        outcome = "ops_denied"
        workflow.upsert_search_attributes({
            "RepairOutcome": [outcome],
            "OrderStatus": ["cancelled_by_ops"],
        })
        return OrderRepairResult(
            status="cancelled",
            outcome=outcome,
            repair_steps_executed=tools_executed,
            requires_hitl=True,
            notes=f"Denied by {slack.decided_by or 'operator'}. {slack.notes}",
        )

    if turn.stop_reason == "end_turn":
        outcome = "auto_repaired"
        workflow.upsert_search_attributes({
            "RepairOutcome": [outcome],
            "OrderStatus": ["repair_complete"],
        })
        return OrderRepairResult(
            status="resolved",
            outcome=outcome,
            repair_steps_executed=tools_executed,
            requires_hitl=False,
            updated_order=_build_updated_order(state, input.order_input),
            notes=turn.final_text,
        )

    # max_iterations
    workflow.upsert_search_attributes({
        "RepairOutcome": ["unresolved"],
        "OrderStatus": ["repair_in_progress"],
    })
    return OrderRepairResult(
        status="unresolved",
        outcome="unresolved",
        repair_steps_executed=tools_executed,
        requires_hitl=False,
        notes="Max agent iterations reached without resolution.",
    )


@workflow.defn
class OrderRepairWorkflow:
    @workflow.run
    async def run(self, input: OrderRepairInput) -> OrderRepairResult:
        workflow.upsert_search_attributes({
            "OrderId": [input.order_id],
            "OrderStatus": ["repair_in_progress"],
            "FailureType": [input.failure.failure_type],
        })
        workflow.set_current_details(
            f"Repair agent diagnosing `{input.failure.failure_type}` on "
            f"`{input.failure.step}` for order `{input.order_id}` "
            f"({input.order_input.book_title} ×{input.order_input.quantity}). "
            f"{input.failure.description}"
        )

        repair_state = RepairAgentState()
        agent_ctx = AgentCtx(domain_input=input, domain_state=repair_state)
        messages: list[dict] = [
            {"role": "user", "content": _initial_user_message(input)},
        ]

        turn = await run_agent_turn(
            messages=messages,
            system=_build_system_prompt(input),
            tools=REPAIR_TOOLS,
            agent_ctx=agent_ctx,
            max_iterations=MAX_AGENT_ITERATIONS,
            agent_label="repair",
        )

        return _shape_repair_result(turn, repair_state, input)
