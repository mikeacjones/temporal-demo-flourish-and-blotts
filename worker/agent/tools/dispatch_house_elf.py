"""Dispatch a house elf for magical manual intervention."""
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
    from worker.agent.tool_args import DispatchHouseElfArgs


_LONG_TIMEOUT = timedelta(seconds=120)


@dataclass
class _HouseElfDispatchInput:
    task: str
    elf: str
    total_steps: int
    delays: list[float]
    outcome_variant: int


async def _send_house_elf(input: _HouseElfDispatchInput) -> str:
    """Long-running stub — heartbeats while a notional house elf retrieves an item.

    Production version would call an external dispatch service and poll for
    completion. The activity heartbeats so the workflow can detect a stuck elf.
    """
    for step, delay in enumerate(input.delays, start=1):
        await asyncio.sleep(delay)
        activity.heartbeat(f"{input.elf} en route — step {step}/{input.total_steps}")
    outcomes = [
        f"{input.elf} dispatched and completed: {input.task}",
        f"{input.elf} reports task complete. Note: {input.elf} is very happy to help and requests no payment.",
    ]
    return outcomes[input.outcome_variant]


@repair_tool(category=ToolCategory.AUTONOMOUS, timeout=_LONG_TIMEOUT)
@ops_tool(
    category=ToolCategory.MUTATING,
    guards=(ops_confirmation,),
    timeout=_LONG_TIMEOUT,
)
async def dispatch_house_elf(args: DispatchHouseElfArgs, ctx: ToolCtx) -> str:
    """Dispatch a house elf for magical manual intervention. Use for tasks \
requiring physical wizarding assistance: retrieving intercepted deliveries, \
capturing escaped magical items, emergency repackaging, or any on-site \
intervention."""
    rng = workflow.random()
    total_steps = rng.randint(5, 12)
    outcome = await ctx.activity(
        _send_house_elf,
        _HouseElfDispatchInput(
            task=args.task,
            elf=rng.choice(["Dobby", "Kreacher", "Winky", "Hokey", "Mipsy"]),
            total_steps=total_steps,
            delays=[rng.uniform(0.4, 0.9) for _ in range(total_steps)],
            outcome_variant=rng.randrange(2),
        ),
        summary=f"Dispatch a house elf to: {args.task}",
        start_to_close_timeout=_LONG_TIMEOUT,
        heartbeat_timeout=timedelta(seconds=15),
    )
    return f"Order {args.order_id}: House elf {outcome}"
