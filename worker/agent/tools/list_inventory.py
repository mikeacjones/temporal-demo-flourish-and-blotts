"""List the entire book catalog with current OMS stock counts."""
from __future__ import annotations

from datetime import timedelta

import httpx

from temporalio import workflow

from shared.agent_harness import (
    ToolCategory,
    ToolCtx,
    ops_tool,
    repair_tool,
)

with workflow.unsafe.imports_passed_through():
    from shared.models import InventoryItem, ListInventoryResult
    from worker.agent.tool_args import ListInventoryArgs
    from worker.config import API_BASE_URL


_READ_TIMEOUT = timedelta(seconds=10)


async def _read_catalog() -> ListInventoryResult:
    """Read the OMS catalog snapshot via the API.

    PORT FROM: worker/activities/ops_activities.py:list_inventory
    """
    async with httpx.AsyncClient(timeout=10.0) as http:
        response = await http.get(f"{API_BASE_URL}/api/catalog")
        response.raise_for_status()
        catalog_items = response.json()
    items = [
        InventoryItem(
            book_id=book_data["id"],
            title=book_data["title"],
            author=book_data["author"],
            in_stock=book_data["in_stock"],
            physical_in_stock=book_data.get("physical_in_stock"),
            category=book_data["category"],
        )
        for book_data in catalog_items
    ]
    return ListInventoryResult(items=items)


@repair_tool(category=ToolCategory.READ, timeout=_READ_TIMEOUT)
@ops_tool(category=ToolCategory.READ, timeout=_READ_TIMEOUT)
async def list_inventory(args: ListInventoryArgs, ctx: ToolCtx) -> str:
    """List the entire book catalog with current OMS stock counts and physical
    warehouse counts. Useful for browsing substitute candidates when a book
    is out of stock — returns every book with its title, author, OMS in_stock,
    and physical_in_stock so you can pick a viable substitute in one call
    rather than running check_inventory book by book."""
    result = await ctx.activity(
        _read_catalog,
        summary="Read the full book catalog from the OMS.",
        start_to_close_timeout=_READ_TIMEOUT,
    )
    lines = [
        f"- {item.book_id}: '{item.title}' by {item.author} "
        f"(OMS in_stock={item.in_stock}, physical={item.physical_in_stock or item.in_stock})"
        for item in result.items
    ]
    return "Catalog inventory:\n" + "\n".join(lines)
