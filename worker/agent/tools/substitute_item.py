"""substitute_item — REPAIR variant validates and stages the swap on workflow
state; OPS variant is a non-functional error shim (interactions in OPS are
not the right path for this action)."""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

from shared.agent_harness import (
    ToolCategory,
    ToolCtx,
    ops_tool,
    repair_tool,
)

with workflow.unsafe.imports_passed_through():
    from shared.catalog import get_book_by_id
    from shared.models import OrderRepairInput
    from worker.agent.guards import (
        ops_confirmation,
        substitute_item_customer_confirmation,
    )
    from worker.agent.repair_state import RepairAgentState
    from worker.agent.tool_args import SubstituteItemArgs


_DEFAULT_TIMEOUT = timedelta(seconds=30)


@repair_tool(
    name="substitute_item",
    category=ToolCategory.MUTATING,
    guards=(substitute_item_customer_confirmation,),
    timeout=_DEFAULT_TIMEOUT,
)
async def substitute_item_repair(args: SubstituteItemArgs, ctx: ToolCtx) -> str:
    """Commit a substitution of the customer's ordered book with a different in-stock book.
    The harness automatically asks the customer for approval (via email + on the order page)
    before the substitution is applied — Claude does NOT need to ask first via
    request_customer_confirmation. If the customer denies, this tool returns an error reason
    and you may propose a different substitute or escalate. The substitute must exist in the
    catalog and have enough physical stock; otherwise the tool returns an ERROR result.
    After a successful substitute_item, call update_order_status('repaired', ...) once and
    end your turn."""
    repair_input: OrderRepairInput = ctx.input
    state: RepairAgentState = ctx.state

    substitute_book = get_book_by_id(args.substitute_item_id)
    if substitute_book is None:
        return (
            f"ERROR: substitute item_id {args.substitute_item_id!r} not found "
            "in the catalog. Pick a valid book id."
        )
    if substitute_book.physical_count < repair_input.order_input.quantity:
        return (
            f"ERROR: substitute '{substitute_book.title}' has only "
            f"{substitute_book.physical_count} physically on the shelf "
            f"(need {repair_input.order_input.quantity}). Pick another."
        )

    state.staged_substitution = (
        args.original_item_id,
        args.substitute_item_id,
        args.reason,
    )
    return (
        f"Order {repair_input.order_id}: substitution committed — "
        f"'{args.original_item_id}' → '{args.substitute_item_id}' "
        f"('{substitute_book.title}'). Reason: {args.reason}. The order will be "
        "repackaged with the substituted book and dispatched normally."
    )


@ops_tool(
    name="substitute_item",
    category=ToolCategory.MUTATING,
    guards=(ops_confirmation,),
    timeout=_DEFAULT_TIMEOUT,
)
async def substitute_item_ops(args: SubstituteItemArgs, ctx: ToolCtx) -> str:
    """Replace a book in an order with a substitute. Confirmation required.
    Only valid for orders currently in repair."""
    return (
        f"ERROR: substitute_item must be handled in the repair workflow, not from ops. "
        f"The substitution did NOT take effect for order {args.order_id}. "
        "Use a repair-flow path instead."
    )
