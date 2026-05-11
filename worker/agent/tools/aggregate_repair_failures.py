"""Group recent OrderRepairWorkflows by FailureType and return counts."""
from __future__ import annotations

from collections import Counter
from datetime import timedelta
from typing import Optional

from temporalio import workflow

from shared.agent_harness import (
    ToolCategory,
    ToolCtx,
    ops_tool,
)

with workflow.unsafe.imports_passed_through():
    from shared.models import AggregateFailuresInput, AggregateFailuresResult, FailureBucket
    from worker.agent.tool_args import AggregateRepairFailuresArgs


_READ_TIMEOUT = timedelta(seconds=10)


def _start_time_after_iso(since_hours: Optional[int]) -> str | None:
    if not since_hours:
        return None
    return (workflow.now() - timedelta(hours=since_hours)).isoformat()


def _since_clause(start_time_after_iso: Optional[str]) -> str:
    if not start_time_after_iso:
        return ""
    return f" AND StartTime > '{start_time_after_iso}'"


def _first(search_attributes: dict, search_attribute_name: str) -> str:
    """Search-attribute values are lists in Temporal — pull the first scalar."""
    attribute_value = search_attributes.get(search_attribute_name)
    if isinstance(attribute_value, list):
        return str(attribute_value[0]) if attribute_value else ""
    if attribute_value is None:
        return ""
    return str(attribute_value)


async def _aggregate_failures(input: AggregateFailuresInput) -> AggregateFailuresResult:
    """Query Temporal Visibility and bucket OrderRepairWorkflows by FailureType.

    PORT FROM: worker/activities/ops_activities.py:aggregate_repair_failures
    """
    from temporalio.client import Client
    from temporalio.contrib.pydantic import pydantic_data_converter
    from worker.config import TEMPORAL_HOST, TEMPORAL_NAMESPACE

    client = await Client.connect(
        TEMPORAL_HOST,
        namespace=TEMPORAL_NAMESPACE,
        data_converter=pydantic_data_converter,
    )

    query = "WorkflowType='OrderRepairWorkflow'" + _since_clause(input.start_time_after_iso)
    counter: Counter = Counter()
    async for workflow_execution in client.list_workflows(query=query):
        failure_type = _first((workflow_execution.search_attributes or {}), "FailureType") or "unknown"
        counter[failure_type] += 1

    buckets = [
        FailureBucket(failure_type=failure_type, count=count)
        for failure_type, count in counter.most_common()
    ]
    return AggregateFailuresResult(buckets=buckets, total=sum(counter.values()))


@ops_tool(category=ToolCategory.READ, timeout=_READ_TIMEOUT)
async def aggregate_repair_failures(args: AggregateRepairFailuresArgs, ctx: ToolCtx) -> str:
    """Group recent OrderRepairWorkflows by FailureType and return counts —
    useful for answering 'what's been breaking lately?'"""
    input = AggregateFailuresInput(
        since_hours=args.since_hours,
        start_time_after_iso=_start_time_after_iso(args.since_hours),
    )
    result = await ctx.activity(
        _aggregate_failures,
        input,
        summary="Aggregate repair failure counts by FailureType.",
        start_to_close_timeout=_READ_TIMEOUT,
    )
    if not result.buckets:
        return "No repair failures found in the given time window."
    lines = [f"Total repair workflows: {result.total}"]
    for b in result.buckets:
        lines.append(f"  {b.failure_type}: {b.count}")
    return "\n".join(lines)
