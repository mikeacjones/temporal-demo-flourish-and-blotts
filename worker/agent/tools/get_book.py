"""Get a single book by ID from the OMS catalog."""
from __future__ import annotations

from datetime import timedelta

import httpx

from temporalio import workflow

from shared.agent_harness import (
    ToolCategory,
    ToolCtx,
    ops_tool,
)

with workflow.unsafe.imports_passed_through():
    from shared.models import GetBookInput, GetBookResult, InventoryItem
    from worker.agent.tool_args import GetBookArgs
    from worker.config import API_BASE_URL


_READ_TIMEOUT = timedelta(seconds=10)


async def _fetch_book(input: GetBookInput) -> GetBookResult:
    """Fetch a single book from the OMS API catalog.

    PORT FROM: worker/activities/ops_activities.py:get_book
    """
    async with httpx.AsyncClient(timeout=10.0) as http:
        response = await http.get(f"{API_BASE_URL}/api/catalog")
        response.raise_for_status()
        catalog_items = response.json()

    matching_book = next(
        (book_data for book_data in catalog_items if book_data["id"] == input.book_id),
        None,
    )
    if matching_book is None:
        return GetBookResult(found=False, item=None)
    return GetBookResult(
        found=True,
        item=InventoryItem(
            book_id=matching_book["id"],
            title=matching_book["title"],
            author=matching_book["author"],
            in_stock=matching_book["in_stock"],
            physical_in_stock=matching_book.get("physical_in_stock"),
            category=matching_book["category"],
        ),
    )


@ops_tool(category=ToolCategory.READ, timeout=_READ_TIMEOUT)
async def get_book(args: GetBookArgs, ctx: ToolCtx) -> str:
    """Get a single book by ID."""
    input = GetBookInput(book_id=args.book_id)
    result = await ctx.activity(
        _fetch_book,
        input,
        summary=f"Fetch book '{args.book_id}' from the OMS catalog.",
        start_to_close_timeout=_READ_TIMEOUT,
    )
    if not result.found or result.item is None:
        return f"Book '{args.book_id}' not found in catalog."
    item = result.item
    physical = item.physical_in_stock if item.physical_in_stock is not None else item.in_stock
    return (
        f"Book '{item.book_id}': '{item.title}' by {item.author} | "
        f"category={item.category} | OMS in_stock={item.in_stock} | physical={physical}"
    )
