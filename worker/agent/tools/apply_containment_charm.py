"""Apply a magical containment charm to a dangerous book in a specific order."""
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
    from shared.catalog import get_book_by_id
    from worker.agent.guards import ops_confirmation
    from worker.agent.tool_args import ApplyContainmentCharmArgs


_DEFAULT_TIMEOUT = timedelta(seconds=30)


async def _apply_charm(item_id: str, item_title: str) -> str:
    """Long-running stub — simulates wand-work and heartbeats progress."""
    total_steps = random.randint(3, 6)
    for step in range(total_steps):
        await asyncio.sleep(random.uniform(0.3, 0.7))
        activity.heartbeat(f"applying charm — step {step + 1}/{total_steps}")
    return random.choice([
        f"Containment charm applied successfully to '{item_title}'. Item subdued and "
        "ready for repackaging with dragon-hide reinforced box.",
        f"Enhanced containment charm applied to '{item_title}'. Three attempts required — "
        "book resisted. Now secured with Unbreakable Charm reinforcement.",
    ])


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
    outcome = await ctx.activity(
        _apply_charm,
        args.item_id, title,
        summary=f"Apply containment charm to {title!r} for order {args.order_id}.",
        start_to_close_timeout=_DEFAULT_TIMEOUT,
        heartbeat_timeout=timedelta(seconds=10),
    )
    return f"Order {args.order_id}: {outcome}"
