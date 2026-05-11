"""Describe ANY workflow by its workflow_id."""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

from shared.agent_harness import (
    ToolCategory,
    ToolCtx,
    ops_tool,
)

with workflow.unsafe.imports_passed_through():
    from shared.models import DescribeWorkflowInput, DescribeWorkflowResult
    from worker.agent.tool_args import DescribeWorkflowArgs


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


async def _fetch_workflow_description(input: DescribeWorkflowInput) -> DescribeWorkflowResult:
    """Describe any workflow by its ID.

    PORT FROM: worker/activities/ops_activities.py:describe_workflow
    """
    from temporalio.client import Client
    from temporalio.contrib.pydantic import pydantic_data_converter
    from worker.config import TEMPORAL_HOST, TEMPORAL_NAMESPACE

    client = await Client.connect(
        TEMPORAL_HOST,
        namespace=TEMPORAL_NAMESPACE,
        data_converter=pydantic_data_converter,
    )

    handle = client.get_workflow_handle(input.workflow_id)
    workflow_description = await handle.describe()
    return DescribeWorkflowResult(
        workflow_id=input.workflow_id,
        workflow_type=workflow_description.workflow_type,
        status=str(workflow_description.status),
        start_time_iso=workflow_description.start_time.isoformat() if workflow_description.start_time else "",
        close_time_iso=workflow_description.close_time.isoformat() if workflow_description.close_time else "",
        search_attributes=_flatten_sa(workflow_description.search_attributes or {}),
    )


@ops_tool(category=ToolCategory.READ, timeout=_READ_TIMEOUT)
async def describe_workflow(args: DescribeWorkflowArgs, ctx: ToolCtx) -> str:
    """Describe ANY workflow by its workflow_id — useful when describe_order's
    related_workflows hands you a child's workflow_id and you want to drill in.
    Returns status, timing, and search attributes."""
    input = DescribeWorkflowInput(workflow_id=args.workflow_id)
    result = await ctx.activity(
        _fetch_workflow_description,
        input,
        summary=f"Describe workflow '{args.workflow_id}'.",
        start_to_close_timeout=_READ_TIMEOUT,
    )
    sa_str = ", ".join(f"{k}={v}" for k, v in result.search_attributes.items()) if result.search_attributes else "(none)"
    lines = [
        f"Workflow: {result.workflow_id}",
        f"Type: {result.workflow_type}",
        f"Status: {result.status}",
        f"Started: {result.start_time_iso or '(unknown)'}",
        f"Closed: {result.close_time_iso or '(still running)'}",
        f"SearchAttributes: {sa_str}",
    ]
    return "\n".join(lines)
