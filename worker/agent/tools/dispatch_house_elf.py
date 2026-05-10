"""Dispatch a house elf for magical manual intervention."""
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
    from worker.agent.tool_args import DispatchHouseElfArgs


_LONG_TIMEOUT = timedelta(seconds=120)


async def _send_house_elf(task: str) -> str:
    """Long-running stub — heartbeats while a notional house elf retrieves an item.

    Production version would call an external dispatch service and poll for
    completion. The activity heartbeats so the workflow can detect a stuck elf.
    """
    elf = random.choice(["Dobby", "Kreacher", "Winky", "Hokey", "Mipsy"])
    total_steps = random.randint(5, 12)
    for step in range(total_steps):
        await asyncio.sleep(random.uniform(0.4, 0.9))
        activity.heartbeat(f"{elf} en route — step {step + 1}/{total_steps}")
    return random.choice([
        f"{elf} dispatched and completed: {task}",
        f"{elf} reports task complete. Note: {elf} is very happy to help and requests no payment.",
    ])


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
    outcome = await ctx.activity(
        _send_house_elf,
        args.task,
        summary=f"Dispatch a house elf to: {args.task}",
        start_to_close_timeout=_LONG_TIMEOUT,
        heartbeat_timeout=timedelta(seconds=15),
    )
    return f"Order {args.order_id}: House elf {outcome}"
