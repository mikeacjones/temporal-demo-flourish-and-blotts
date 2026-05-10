"""Return the structured event history of a workflow."""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

from shared.agent_harness import (
    ToolCategory,
    ToolCtx,
    ops_tool,
)

with workflow.unsafe.imports_passed_through():
    from shared.models import GetWorkflowHistoryInput, GetWorkflowHistoryResult, WorkflowHistoryEvent
    from worker.agent.tool_args import GetWorkflowHistoryArgs


_READ_TIMEOUT = timedelta(seconds=10)


def _summarize_event(event) -> "WorkflowHistoryEvent":
    """Render a Temporal HistoryEvent proto as a small structured summary.

    PORT FROM: worker/activities/ops_activities.py:_summarize_event
    """
    from temporalio.api.enums.v1 import EventType

    event_type_name = EventType.Name(event.event_type) if event.event_type else "Unknown"
    summary = ""
    try:
        if event_type_name == "EVENT_TYPE_ACTIVITY_TASK_SCHEDULED":
            event_attributes = event.activity_task_scheduled_event_attributes
            summary = f"activity={event_attributes.activity_type.name}"
        elif event_type_name == "EVENT_TYPE_ACTIVITY_TASK_FAILED":
            event_attributes = event.activity_task_failed_event_attributes
            summary = f"failure={event_attributes.failure.message[:200]}"
        elif event_type_name == "EVENT_TYPE_WORKFLOW_EXECUTION_SIGNALED":
            event_attributes = event.workflow_execution_signaled_event_attributes
            summary = f"signal={event_attributes.signal_name}"
        elif event_type_name == "EVENT_TYPE_START_CHILD_WORKFLOW_EXECUTION_INITIATED":
            event_attributes = event.start_child_workflow_execution_initiated_event_attributes
            summary = f"child_id={event_attributes.workflow_id} type={event_attributes.workflow_type.name}"
        elif event_type_name == "EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_COMPLETED":
            event_attributes = event.child_workflow_execution_completed_event_attributes
            summary = f"child_id={event_attributes.workflow_execution.workflow_id}"
        elif event_type_name == "EVENT_TYPE_WORKFLOW_EXECUTION_FAILED":
            event_attributes = event.workflow_execution_failed_event_attributes
            summary = f"failure={event_attributes.failure.message[:200]}"
        elif event_type_name == "EVENT_TYPE_TIMER_STARTED":
            event_attributes = event.timer_started_event_attributes
            duration_seconds = (
                event_attributes.start_to_fire_timeout.seconds
                if event_attributes.start_to_fire_timeout
                else 0
            )
            summary = f"timer_id={event_attributes.timer_id} duration_s={duration_seconds}"
        elif event_type_name == "EVENT_TYPE_UPSERT_WORKFLOW_SEARCH_ATTRIBUTES":
            event_attributes = event.upsert_workflow_search_attributes_event_attributes
            search_attribute_keys = list(
                (event_attributes.search_attributes.indexed_fields or {}).keys()
            )
            summary = f"keys={search_attribute_keys}"
    except Exception:
        pass

    timestamp = ""
    if event.event_time:
        try:
            timestamp = event.event_time.ToDatetime().isoformat() + "Z"
        except Exception:
            pass

    short_type = event_type_name.removeprefix("EVENT_TYPE_") if hasattr(event_type_name, "removeprefix") else event_type_name
    return WorkflowHistoryEvent(
        event_id=event.event_id,
        timestamp_iso=timestamp,
        event_type=short_type,
        summary=summary,
    )


async def _fetch_workflow_history(input: GetWorkflowHistoryInput) -> GetWorkflowHistoryResult:
    """Fetch and summarize the event history for a workflow.

    PORT FROM: worker/activities/ops_activities.py:get_workflow_history
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
    events: list[WorkflowHistoryEvent] = []
    truncated = False
    history = await handle.fetch_history()
    for event in history.events:
        if len(events) >= input.max_events:
            truncated = True
            break
        events.append(_summarize_event(event))
    return GetWorkflowHistoryResult(
        workflow_id=input.workflow_id,
        events=events,
        truncated=truncated,
    )


@ops_tool(category=ToolCategory.READ, timeout=_READ_TIMEOUT)
async def get_workflow_history(args: GetWorkflowHistoryArgs, ctx: ToolCtx) -> str:
    """Return the structured event history of a workflow — every activity scheduled/
    completed/failed, signals received, child workflows started, timers, search-
    attribute upserts. Use this when you need to know exactly WHAT HAPPENED — e.g.
    'why is this order stuck?', 'what activities did the repair agent run?',
    'which signals has this workflow received?'. Each event is summarized with a
    one-line detail. Bounded by max_events to keep responses tight; default 200."""
    input = GetWorkflowHistoryInput(
        workflow_id=args.workflow_id,
        max_events=args.max_events,
    )
    result = await ctx.activity(
        _fetch_workflow_history,
        input,
        summary=f"Fetch event history for workflow '{args.workflow_id}'.",
        start_to_close_timeout=_READ_TIMEOUT,
    )
    lines = [f"History for {result.workflow_id} ({len(result.events)} events" + (", truncated)" if result.truncated else "):")]
    for e in result.events:
        detail = f" — {e.summary}" if e.summary else ""
        lines.append(f"  [{e.event_id}] {e.timestamp_iso} {e.event_type}{detail}")
    return "\n".join(lines)
