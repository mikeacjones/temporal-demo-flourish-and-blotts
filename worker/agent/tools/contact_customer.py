"""Send a notification owl (email) to the customer about their order status."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
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
    from worker.agent.tool_args import ContactCustomerArgs


_DEFAULT_TIMEOUT = timedelta(seconds=30)


@dataclass
class _SendOwlInput:
    order_id: str
    message: str
    delay: float


async def _send_owl(input: _SendOwlInput) -> str:
    """Stub — dispatch an owl with the customer notification."""
    await asyncio.sleep(input.delay)
    return f"Notification owl dispatched to customer for Order {input.order_id}: '{input.message}'"


@repair_tool(category=ToolCategory.AUTONOMOUS, timeout=_DEFAULT_TIMEOUT)
@ops_tool(
    category=ToolCategory.MUTATING,
    guards=(ops_confirmation,),
    timeout=_DEFAULT_TIMEOUT,
)
async def contact_customer(args: ContactCustomerArgs, ctx: ToolCtx) -> str:
    """Send a notification owl (email) to the customer about their order status."""
    rng = workflow.random()
    return await ctx.activity(
        _send_owl,
        _SendOwlInput(
            order_id=args.order_id,
            message=args.message,
            delay=rng.uniform(0.2, 0.5),
        ),
        summary=f"Send notification owl to customer for order {args.order_id}.",
        start_to_close_timeout=_DEFAULT_TIMEOUT,
    )
