"""Apply a magical containment charm to a dangerous book in a specific order."""
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
    from shared.catalog import get_book_by_id
    from worker.agent.guards import ops_confirmation
    from worker.agent.tool_args import ApplyContainmentCharmArgs


_DEFAULT_TIMEOUT = timedelta(seconds=30)


@dataclass
class _ApplyCharmInput:
    item_id: str
    item_title: str
    total_steps: int
    delays: list[float]
    outcome_variant: int


async def _apply_charm(input: _ApplyCharmInput) -> str:
    """Long-running stub — simulates wand-work and heartbeats progress."""
    for step, delay in enumerate(input.delays, start=1):
        await asyncio.sleep(delay)
        activity.heartbeat(f"applying charm — step {step}/{input.total_steps}")
    outcomes = [
        f"Containment charm applied successfully to '{input.item_title}'. Item subdued and "
        "ready for repackaging with dragon-hide reinforced box.",
        f"Enhanced containment charm applied to '{input.item_title}'. Three attempts required — "
        "book resisted. Now secured with Unbreakable Charm reinforcement.",
    ]
    return outcomes[input.outcome_variant]


@repair_tool(category=ToolCategory.AUTONOMOUS, timeout=_DEFAULT_TIMEOUT)
@ops_tool(
    category=ToolCategory.MUTATING,
    guards=(ops_confirmation,),
    timeout=_DEFAULT_TIMEOUT,
)
async def apply_containment_charm(args: ApplyContainmentCharmArgs, ctx: ToolCtx) -> str:
    """Apply a magical containment charm to an escaped or dangerous magical item. \
Use for Monster Book of Monsters escapes or other dangerous book incidents. \
The charm restrains the item and makes it safe for repackaging."""
    book = get_book_by_id(args.item_id)
    title = book.title if book else args.item_id
    rng = workflow.random()
    total_steps = rng.randint(3, 6)
    outcome = await ctx.activity(
        _apply_charm,
        _ApplyCharmInput(
            item_id=args.item_id,
            item_title=title,
            total_steps=total_steps,
            delays=[rng.uniform(0.3, 0.7) for _ in range(total_steps)],
            outcome_variant=rng.randrange(2),
        ),
        summary=f"Apply containment charm to {title!r} for order {args.order_id}.",
        start_to_close_timeout=_DEFAULT_TIMEOUT,
        heartbeat_timeout=timedelta(seconds=10),
    )
    return f"Order {args.order_id}: {outcome}"
