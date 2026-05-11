"""Update the status and add a note to an order in the OMS."""
from __future__ import annotations

import asyncio
import random
from datetime import timedelta

from temporalio import workflow

from shared.agent_harness import (
    ToolCategory,
    ToolCtx,
    ops_tool,
    repair_tool,
)

with workflow.unsafe.imports_passed_through():
    from worker.agent.guards import ops_confirmation
    from worker.agent.tool_args import UpdateOrderStatusArgs


_DEFAULT_TIMEOUT = timedelta(seconds=30)


async def _update_oms_status(order_id: str, status: str, message: str) -> str:
    """Stub — write the status update to the OMS."""
    await asyncio.sleep(random.uniform(0.2, 0.5))
    return f"Order {order_id} status updated to '{status}': {message}"


@repair_tool(category=ToolCategory.AUTONOMOUS, timeout=_DEFAULT_TIMEOUT)
@ops_tool(
    category=ToolCategory.MUTATING,
    guards=(ops_confirmation,),
    timeout=_DEFAULT_TIMEOUT,
)
async def update_order_status(args: UpdateOrderStatusArgs, ctx: ToolCtx) -> str:
    """Update the status and add a note to an order in the OMS."""
    return await ctx.activity(
        _update_oms_status,
        args.order_id, args.status, args.message,
        summary=f"Update order {args.order_id} status to '{args.status}'.",
        start_to_close_timeout=_DEFAULT_TIMEOUT,
    )
