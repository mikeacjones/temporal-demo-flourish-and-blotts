"""Adjust the OMS in_stock count for a book."""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

from shared.agent_harness import ToolCategory, ToolCtx, ops_tool

with workflow.unsafe.imports_passed_through():
    from shared.models import AdjustInventoryInput, AdjustInventoryResult
    from worker.agent.guards import ops_confirmation
    from worker.agent.tool_args import AdjustInventoryArgs


_READ_TIMEOUT = timedelta(seconds=10)


async def _adjust_oms_inventory(input_: AdjustInventoryInput) -> AdjustInventoryResult:
    """Apply a stock delta to the OMS catalog row for a book.

    PORT FROM: worker/activities/ops_activities.py:adjust_inventory body verbatim.
    """
    import httpx
    from temporalio.exceptions import ApplicationError

    from shared.catalog import get_book_by_id
    from worker.activities.ops_activities import _adjust_idempotency_cache
    from worker.config import API_BASE_URL

    cached = _adjust_idempotency_cache.get(input_.tool_use_id)
    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            response = await http.post(
                f"{API_BASE_URL}/api/inventory/adjust",
                json={
                    "book_id": input_.book_id,
                    "delta": input_.delta,
                    "reason": input_.reason,
                    "idempotency_key": input_.tool_use_id,
                },
            )
            if response.status_code == 400:
                detail = response.json().get("detail", "unknown error")
                raise ApplicationError(
                    f"adjust_inventory: {detail}",
                    type="UnknownBook",
                    non_retryable=True,
                )
            response.raise_for_status()
            data = response.json()
    except ApplicationError:
        raise
    except Exception as error:
        # Transient / network error — let Temporal retry.
        raise ApplicationError(
            f"adjust_inventory API call failed: {error}",
            type="ApiError",
        )

    new_count = int(data.get("in_stock", 0))
    book = get_book_by_id(input_.book_id)  # local catalog only used for friendly title
    title = book.title if book else input_.book_id
    result = AdjustInventoryResult(
        applied=True,
        new_in_stock=new_count,
        note=f"Stock for '{title}' adjusted by {input_.delta:+d} → {new_count}. Reason: {input_.reason}",
    )
    _adjust_idempotency_cache[input_.tool_use_id] = result
    return result


@ops_tool(
    category=ToolCategory.MUTATING,
    guards=(ops_confirmation,),
    timeout=_READ_TIMEOUT,
)
async def adjust_inventory(args: AdjustInventoryArgs, ctx: ToolCtx) -> str:
    """Adjust the OMS in_stock count for a book by a positive or negative delta.
    Confirmation required."""
    result = await ctx.activity(
        _adjust_oms_inventory,
        AdjustInventoryInput(
            book_id=args.book_id,
            delta=args.delta,
            reason=args.reason,
            tool_use_id=ctx.tool_use_id,
        ),
        summary=f"Adjust {args.book_id} stock by {args.delta:+d} ({args.reason})",
        start_to_close_timeout=_READ_TIMEOUT,
    )
    if result.applied:
        return f"Inventory adjusted: {args.book_id} now {result.new_in_stock} in_stock. {result.note}"
    return f"Inventory adjustment for {args.book_id} not applied. {result.note}"
