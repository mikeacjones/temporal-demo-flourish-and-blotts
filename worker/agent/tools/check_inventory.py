"""Check current inventory levels for a book item at Flourish & Blotts warehouse."""
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
    from worker.agent.tool_args import CheckInventoryArgs


_READ_TIMEOUT = timedelta(seconds=10)


async def _check_inventory_physical(item_id: str) -> str:
    """Return a human-readable inventory check result for a single book.

    PORT FROM: worker/activities/repair_activities.py:check_inventory
    """
    from shared.catalog import get_book_by_id
    book = get_book_by_id(item_id)
    if not book:
        return f"Item '{item_id}' not found in catalog."
    physical = book.physical_count
    if physical == book.in_stock:
        return (
            f"Inventory check: '{book.title}' — {physical} copies on the shelf "
            "at Diagon Alley warehouse."
        )
    return (
        f"Inventory check: '{book.title}' — only {physical} copies physically on "
        f"the shelf at Diagon Alley warehouse (OMS records {book.in_stock}; the "
        "OMS count is stale and cannot be filled against)."
    )


@repair_tool(category=ToolCategory.READ, timeout=_READ_TIMEOUT)
@ops_tool(category=ToolCategory.READ, timeout=_READ_TIMEOUT)
async def check_inventory(args: CheckInventoryArgs, ctx: ToolCtx) -> str:
    """Check current inventory levels for a book item at Flourish & Blotts warehouse."""
    return await ctx.activity(
        _check_inventory_physical,
        args.item_id,
        summary=f"Check physical inventory for book '{args.item_id}'.",
        start_to_close_timeout=_READ_TIMEOUT,
    )
