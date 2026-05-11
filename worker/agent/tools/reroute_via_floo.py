"""Reroute a delivery via the Floo Network to a corrected or alternative destination."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
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


@dataclass
class _RerouteInput:
    order_id: str
    destination: str
    total_steps: int
    delays: list[float]


async def _reroute(input: _RerouteInput) -> str:
    """Stub — simulates the Floo Network call with heartbeats."""
    for step, delay in enumerate(input.delays, start=1):
        await asyncio.sleep(delay)
        activity.heartbeat(f"floo rerouting — step {step}/{input.total_steps}")
    return (
        f"Order {input.order_id}: Floo Network rerouting initiated. "
        f"Package redirected to '{input.destination}'. "
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
    rng = workflow.random()
    total_steps = rng.randint(4, 8)
    return await ctx.activity(
        _reroute,
        _RerouteInput(
            order_id=args.order_id,
            destination=args.destination,
            total_steps=total_steps,
            delays=[rng.uniform(0.4, 0.8) for _ in range(total_steps)],
        ),
        summary=f"Reroute order {args.order_id} via Floo Network to '{args.destination}'.",
        start_to_close_timeout=_LONG_TIMEOUT,
        heartbeat_timeout=timedelta(seconds=15),
    )
