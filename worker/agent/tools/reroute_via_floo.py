"""Reroute a delivery via the Floo Network to a corrected or alternative destination."""
from __future__ import annotations

import asyncio
import random
from datetime import timedelta

from temporalio import activity, workflow

from shared.agent_harness import (
    ToolCategory,
    ToolCtx,
    ops_tool,
    repair_tool,
)

with workflow.unsafe.imports_passed_through():
    from worker.agent.guards import ops_confirmation
    from worker.agent.tool_args import RerouteViaFlooArgs


_LONG_TIMEOUT = timedelta(seconds=120)


async def _reroute(order_id: str, destination: str) -> str:
    """Stub — simulates the Floo Network call with heartbeats."""
    total_steps = random.randint(4, 8)
    for step in range(total_steps):
        await asyncio.sleep(random.uniform(0.4, 0.8))
        activity.heartbeat(f"floo rerouting — step {step + 1}/{total_steps}")
    return (
        f"Order {order_id}: Floo Network rerouting initiated. "
        f"Package redirected to '{destination}'. "
        "Floo Regulation Panel notified. Estimated re-delivery: 2 hours."
    )


@repair_tool(category=ToolCategory.AUTONOMOUS, timeout=_LONG_TIMEOUT)
@ops_tool(
    category=ToolCategory.MUTATING,
    guards=(ops_confirmation,),
    timeout=_LONG_TIMEOUT,
)
async def reroute_via_floo(args: RerouteViaFlooArgs, ctx: ToolCtx) -> str:
    """Reroute a delivery via the Floo Network to a corrected or alternative \
destination. Use when Floo misdirection has occurred or as a fallback for \
failed owl deliveries."""
    return await ctx.activity(
        _reroute,
        args.order_id, args.destination,
        summary=f"Reroute order {args.order_id} via Floo Network to '{args.destination}'.",
        start_to_close_timeout=_LONG_TIMEOUT,
        heartbeat_timeout=timedelta(seconds=15),
    )
