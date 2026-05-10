"""List Flourish & Blotts orders matching optional filters."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional

from temporalio import workflow

from shared.agent_harness import (
    ToolCategory,
    ToolCtx,
    ops_tool,
)

with workflow.unsafe.imports_passed_through():
    from shared.models import ListOrdersInput, ListOrdersResult, OrderSummary
    from worker.agent.tool_args import ListOrdersArgs


_READ_TIMEOUT = timedelta(seconds=10)


def _since_clause(since_hours: Optional[int]) -> str:
    if not since_hours:
        return ""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    return f" AND StartTime > '{cutoff.isoformat()}'"


def _first(search_attributes: dict, search_attribute_name: str) -> str:
    """Search-attribute values are lists in Temporal — pull the first scalar."""
    attribute_value = search_attributes.get(search_attribute_name)
    if isinstance(attribute_value, list):
        return str(attribute_value[0]) if attribute_value else ""
    if attribute_value is None:
        return ""
    return str(attribute_value)


async def _fetch_orders(input: ListOrdersInput) -> ListOrdersResult:
    """Query Temporal Visibility for orders matching the given filters.

    PORT FROM: worker/activities/ops_activities.py:list_orders
    """
    from temporalio.client import Client
    from temporalio.contrib.pydantic import pydantic_data_converter
    from worker.config import TEMPORAL_HOST, TEMPORAL_NAMESPACE

    client = await Client.connect(
        TEMPORAL_HOST,
        namespace=TEMPORAL_NAMESPACE,
        data_converter=pydantic_data_converter,
    )

    parts: list[str] = []
    if input.status:
        parts.append(f"OrderStatus='{input.status}'")
    else:
        parts.append("WorkflowType='OrderWorkflow'")
    if input.failure_type:
        parts.append(f"FailureType='{input.failure_type}'")
    query = " AND ".join(parts) + _since_clause(input.since_hours)

    fetch_cap = input.limit * 3 if input.status else input.limit
    by_order: dict[str, OrderSummary] = {}
    async for workflow_execution in client.list_workflows(query=query, limit=fetch_cap):
        search_attributes = workflow_execution.search_attributes or {}
        order_id = _first(search_attributes, "OrderId")
        if not order_id:
            continue
        if order_id in by_order:
            continue
        by_order[order_id] = OrderSummary(
            order_id=order_id,
            workflow_id=workflow_execution.id,
            workflow_type=workflow_execution.workflow_type,
            status=str(workflow_execution.status),
            order_status=_first(search_attributes, "OrderStatus"),
        )
        if len(by_order) >= input.limit:
            break

    return ListOrdersResult(orders=list(by_order.values()))


@ops_tool(category=ToolCategory.READ, timeout=_READ_TIMEOUT)
async def list_orders(args: ListOrdersArgs, ctx: ToolCtx) -> str:
    """List Flourish & Blotts orders matching optional filters. Returns one
    row per OrderId. Backed by Temporal Visibility (~1-2s eventual consistency lag).
    When a `status` filter is set, results may include any workflow type carrying
    that OrderStatus — some states live on the active child workflow, not the
    parent OrderWorkflow. The returned `workflow_id` reflects whichever workflow
    currently carries that status — feed it into `describe_workflow` or
    `get_workflow_history` to drill in."""
    input = ListOrdersInput(
        status=args.status,
        failure_type=args.failure_type,
        since_hours=args.since_hours,
        limit=args.limit,
    )
    result = await ctx.activity(
        _fetch_orders,
        input,
        summary="Query Temporal Visibility for orders.",
        start_to_close_timeout=_READ_TIMEOUT,
    )
    if not result.orders:
        return "No orders found matching the given filters."
    lines = [
        f"- {o.order_id} | {o.workflow_type} | status={o.status} | order_status={o.order_status} | wf={o.workflow_id}"
        for o in result.orders
    ]
    return f"Orders ({len(result.orders)}):\n" + "\n".join(lines)
