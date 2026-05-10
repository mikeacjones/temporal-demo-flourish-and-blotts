"""Cancel an order's workflow."""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

from shared.agent_harness import (
    ToolCategory,
    ToolCtx,
    ops_tool,
)

with workflow.unsafe.imports_passed_through():
    from shared.models import CancelOrderInput, CancelOrderResult
    from worker.agent.guards import ops_confirmation
    from worker.agent.tool_args import CancelOrderArgs


_READ_TIMEOUT = timedelta(seconds=10)


async def _cancel_order_workflow(input_: CancelOrderInput) -> CancelOrderResult:
    """Issue a cancellation signal/termination to the target order workflow.

    PORT FROM: worker/activities/ops_activities.py:cancel_order body verbatim.
    """
    from temporalio.exceptions import ApplicationError

    from worker.activities.ops_activities import _cancel_idempotency_cache, _get_client

    cached = _cancel_idempotency_cache.get(input_.tool_use_id)
    if cached is not None:
        return cached

    client = await _get_client()
    workflow_id = f"order-{input_.order_id}"
    handle = client.get_workflow_handle(workflow_id)
    try:
        await handle.cancel()
    except Exception as error:
        # Cancelling something that doesn't exist or is already closed: surface
        # to the agent as a non-retryable error so the executor wraps it as
        # is_error=True and the agent sees a clear message.
        raise ApplicationError(
            f"cancel_order failed for '{input_.order_id}': {error}",
            type="OrderCancelFailed",
            non_retryable=True,
        )

    result = CancelOrderResult(
        cancelled=True,
        note=f"Order {input_.order_id} cancellation requested. Reason: {input_.reason}",
    )
    _cancel_idempotency_cache[input_.tool_use_id] = result
    return result


@ops_tool(
    category=ToolCategory.MUTATING,
    guards=(ops_confirmation,),
    timeout=_READ_TIMEOUT,
)
async def cancel_order(args: CancelOrderArgs, ctx: ToolCtx) -> str:
    """Cancel an order's workflow. Confirmation required from the operator before this
    runs. Naturally idempotent — cancelling an already-cancelled order is a no-op."""
    result = await ctx.activity(
        _cancel_order_workflow,
        CancelOrderInput(
            order_id=args.order_id,
            reason=args.reason,
            tool_use_id=ctx.tool_use_id,
        ),
        summary=f"Cancel order {args.order_id} (reason: {args.reason})",
        start_to_close_timeout=_READ_TIMEOUT,
    )
    if result.cancelled:
        return f"Order {args.order_id} cancelled. {result.note}"
    return f"Order {args.order_id} cancellation declined or already done. {result.note}"
