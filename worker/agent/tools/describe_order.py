"""Describe an order's workflow execution and every related workflow tagged with the same OrderId."""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

from shared.agent_harness import (
    ToolCategory,
    ToolCtx,
    ops_tool,
)

with workflow.unsafe.imports_passed_through():
    from shared.models import DescribeOrderInput, DescribeOrderResult, RelatedWorkflowSummary
    from worker.agent.tool_args import DescribeOrderArgs


_READ_TIMEOUT = timedelta(seconds=10)


def _flatten_sa(search_attributes: dict) -> dict:
    """Render Temporal search attributes as plain key→scalar/list of scalars."""
    flattened = {}
    for search_attribute_name, attribute_value in (search_attributes or {}).items():
        if isinstance(attribute_value, list):
            if len(attribute_value) == 1:
                flattened[search_attribute_name] = attribute_value[0]
            else:
                flattened[search_attribute_name] = list(attribute_value)
        else:
            flattened[search_attribute_name] = attribute_value
    return flattened


async def _fetch_order_description(input: DescribeOrderInput) -> DescribeOrderResult:
    """Describe the OrderWorkflow and all related workflows tagged with the same OrderId.

    PORT FROM: worker/activities/ops_activities.py:describe_order
    """
    from temporalio.client import Client
    from temporalio.contrib.pydantic import pydantic_data_converter
    from worker.config import TEMPORAL_HOST, TEMPORAL_NAMESPACE

    client = await Client.connect(
        TEMPORAL_HOST,
        namespace=TEMPORAL_NAMESPACE,
        data_converter=pydantic_data_converter,
    )

    workflow_id = f"order-{input.order_id}"
    handle = client.get_workflow_handle(workflow_id)
    workflow_description = await handle.describe()

    related: list[RelatedWorkflowSummary] = []
    async for workflow_execution in client.list_workflows(query=f"OrderId='{input.order_id}'"):
        if workflow_execution.id == workflow_id:
            continue
        related.append(
            RelatedWorkflowSummary(
                workflow_id=workflow_execution.id,
                workflow_type=workflow_execution.workflow_type,
                status=str(workflow_execution.status),
                search_attributes=_flatten_sa(workflow_execution.search_attributes or {}),
            )
        )

    return DescribeOrderResult(
        order_id=input.order_id,
        workflow_id=workflow_id,
        status=str(workflow_description.status),
        start_time_iso=workflow_description.start_time.isoformat() if workflow_description.start_time else "",
        close_time_iso=workflow_description.close_time.isoformat() if workflow_description.close_time else "",
        search_attributes=_flatten_sa(workflow_description.search_attributes or {}),
        related_workflows=related,
    )


@ops_tool(category=ToolCategory.READ, timeout=_READ_TIMEOUT)
async def describe_order(args: DescribeOrderArgs, ctx: ToolCtx) -> str:
    """Describe an order's workflow execution AND every related workflow tagged
    with the same OrderId — repair workflow, customer-confirmation child,
    slack-conversation HITL child. The parent OrderWorkflow's search attributes
    go stale once it's awaiting a child workflow. Look at the `related_workflows`
    array for the live HITL/repair state."""
    input = DescribeOrderInput(order_id=args.order_id)
    result = await ctx.activity(
        _fetch_order_description,
        input,
        summary=f"Describe order '{args.order_id}' and related workflows.",
        start_to_close_timeout=_READ_TIMEOUT,
    )
    sa_str = ", ".join(f"{k}={v}" for k, v in result.search_attributes.items()) if result.search_attributes else "(none)"
    lines = [
        f"Order: {result.order_id}",
        f"Workflow: {result.workflow_id}",
        f"Status: {result.status}",
        f"Started: {result.start_time_iso or '(unknown)'}",
        f"Closed: {result.close_time_iso or '(still running)'}",
        f"SearchAttributes: {sa_str}",
    ]
    if result.related_workflows:
        lines.append("Related workflows:")
        for rw in result.related_workflows:
            rw_sa = ", ".join(f"{k}={v}" for k, v in rw.search_attributes.items()) if rw.search_attributes else ""
            lines.append(f"  - {rw.workflow_id} ({rw.workflow_type}) status={rw.status}" + (f" [{rw_sa}]" if rw_sa else ""))
    else:
        lines.append("Related workflows: (none)")
    return "\n".join(lines)
