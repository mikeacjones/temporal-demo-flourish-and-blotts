"""Activities for the Slack ops-agent.

Read activities query the Temporal Visibility API and the in-process catalog.
Mutation activities (cancel_order, adjust_inventory) and Slack-targeting
activities (post_confirmation_card, post_order_picker, collapse_buttons) live
later in this file — added in subsequent tasks.

The Temporal client is lazily constructed and cached at module level. For
tests, monkeypatch `_get_client` to return a stub.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient
from temporalio import activity
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError

from shared.catalog import CATALOG, get_book_by_id
from shared.models import (
    AdjustInventoryInput,
    AdjustInventoryResult,
    AggregateFailuresInput,
    AggregateFailuresResult,
    CancelOrderInput,
    CancelOrderResult,
    CollapseButtonsInput,
    DescribeOrderInput,
    DescribeOrderResult,
    DescribeWorkflowInput,
    DescribeWorkflowResult,
    GetWorkflowHistoryInput,
    GetWorkflowHistoryResult,
    RelatedWorkflowSummary,
    WorkflowHistoryEvent,
    FailureBucket,
    GetBookInput,
    GetBookResult,
    InventoryItem,
    ListInventoryResult,
    ListOrdersInput,
    ListOrdersResult,
    OrderSummary,
    PostCardResult,
    PostConfirmationCardInput,
    PostOrderPickerInput,
    PostThreadClosedNoticeInput,
    PostRichThreadReplyInput,
    PostThreadReplyInput,
    PostThreadReplyResult,
)
from worker.config import API_BASE_URL, SLACK_BOT_TOKEN, TEMPORAL_HOST, TEMPORAL_NAMESPACE


_client: Optional[Client] = None
_client_lock: Optional[asyncio.Lock] = None


async def _get_client() -> Client:
    global _client, _client_lock
    if _client_lock is None:
        _client_lock = asyncio.Lock()
    async with _client_lock:
        if _client is None:
            _client = await Client.connect(
                TEMPORAL_HOST,
                namespace=TEMPORAL_NAMESPACE,
                data_converter=pydantic_data_converter,
            )
    return _client


def _first(sa: dict, key: str) -> str:
    """Search-attribute values are lists in Temporal — pull the first scalar."""
    val = sa.get(key)
    if isinstance(val, list):
        return str(val[0]) if val else ""
    if val is None:
        return ""
    return str(val)


def _since_clause(since_hours: Optional[int]) -> str:
    if not since_hours:
        return ""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    return f" AND StartTime > '{cutoff.isoformat()}'"


@activity.defn
async def list_orders(input: ListOrdersInput) -> ListOrdersResult:
    """List orders, optionally filtered by status/failure_type/age.

    OrderStatus filtering note: states like `awaiting_customer`, `awaiting_ops`,
    `awaiting_hitl`, `repair_in_progress` live on CHILD workflows
    (OrderRepairWorkflow, SlackConversationWorkflow), not the parent
    OrderWorkflow. So when a status filter is specified, we drop the
    WorkflowType filter and let any matching workflow type qualify, then
    dedupe by OrderId so each logical order appears once.
    """
    client = await _get_client()

    parts: list[str] = []
    if input.status:
        # No WorkflowType filter — the matching state may be on a child workflow.
        parts.append(f"OrderStatus='{input.status}'")
    else:
        # No status filter: default to top-level orders only.
        parts.append("WorkflowType='OrderWorkflow'")
    if input.failure_type:
        parts.append(f"FailureType='{input.failure_type}'")
    query = " AND ".join(parts) + _since_clause(input.since_hours)

    # Fetch a generous page so dedupe doesn't starve the limit.
    fetch_cap = input.limit * 3 if input.status else input.limit
    by_order: dict[str, OrderSummary] = {}
    async for ex in client.list_workflows(query=query, limit=fetch_cap):
        sa = ex.search_attributes or {}
        order_id = _first(sa, "OrderId")
        if not order_id:
            continue
        if order_id in by_order:
            continue  # already have a row for this order
        by_order[order_id] = OrderSummary(
            order_id=order_id,
            workflow_id=ex.id,
            workflow_type=ex.workflow_type,
            status=str(ex.status),
            order_status=_first(sa, "OrderStatus"),
        )
        if len(by_order) >= input.limit:
            break

    return ListOrdersResult(orders=list(by_order.values()))


def _flatten_sa(sa: dict) -> dict:
    """Render Temporal search attributes as plain key→scalar/list of scalars.
    Useful when serializing for the agent."""
    out = {}
    for key, val in (sa or {}).items():
        if isinstance(val, list):
            if len(val) == 1:
                out[key] = val[0]
            else:
                out[key] = list(val)
        else:
            out[key] = val
    return out


@activity.defn
async def describe_order(input: DescribeOrderInput) -> DescribeOrderResult:
    """Describe the OrderWorkflow plus every workflow tagged with the same
    OrderId search attribute (repair workflow, customer-confirmation child,
    slack-conversation HITL child). The parent OrderWorkflow's search
    attributes are stale once it's awaiting a child workflow — the child's
    attributes reflect the live HITL/repair state, so we surface them here.

    OrderWorkflow IDs are deterministic in this codebase: order_id maps to
    workflow_id `order-{order_id}`.
    """
    client = await _get_client()
    workflow_id = f"order-{input.order_id}"
    handle = client.get_workflow_handle(workflow_id)
    desc = await handle.describe()

    # Find every other workflow tagged with this OrderId.
    related: list[RelatedWorkflowSummary] = []
    async for ex in client.list_workflows(query=f"OrderId='{input.order_id}'"):
        if ex.id == workflow_id:
            continue
        related.append(
            RelatedWorkflowSummary(
                workflow_id=ex.id,
                workflow_type=ex.workflow_type,
                status=str(ex.status),
                search_attributes=_flatten_sa(ex.search_attributes or {}),
            )
        )

    return DescribeOrderResult(
        order_id=input.order_id,
        workflow_id=workflow_id,
        status=str(desc.status),
        start_time_iso=desc.start_time.isoformat() if desc.start_time else "",
        close_time_iso=desc.close_time.isoformat() if desc.close_time else "",
        search_attributes=_flatten_sa(desc.search_attributes or {}),
        related_workflows=related,
    )


@activity.defn
async def describe_workflow(input: DescribeWorkflowInput) -> DescribeWorkflowResult:
    """Describe ANY workflow by ID — useful when the agent has a workflow_id
    from describe_order's related_workflows or list_orders and wants to drill in.
    Distinct from describe_order which is order_id-keyed."""
    client = await _get_client()
    handle = client.get_workflow_handle(input.workflow_id)
    desc = await handle.describe()
    return DescribeWorkflowResult(
        workflow_id=input.workflow_id,
        workflow_type=desc.workflow_type,
        status=str(desc.status),
        start_time_iso=desc.start_time.isoformat() if desc.start_time else "",
        close_time_iso=desc.close_time.isoformat() if desc.close_time else "",
        search_attributes=_flatten_sa(desc.search_attributes or {}),
    )


def _summarize_event(event) -> "WorkflowHistoryEvent":
    """Render a Temporal HistoryEvent proto as a small structured summary.
    Pulls the most useful per-type detail (activity type name, signal name,
    failure message, child workflow id) without dumping full proto payloads —
    keeps the response tight for the agent's context."""
    from temporalio.api.enums.v1 import EventType

    event_type_name = EventType.Name(event.event_type) if event.event_type else "Unknown"
    summary = ""
    try:
        if event_type_name == "EVENT_TYPE_ACTIVITY_TASK_SCHEDULED":
            attrs = event.activity_task_scheduled_event_attributes
            summary = f"activity={attrs.activity_type.name}"
        elif event_type_name == "EVENT_TYPE_ACTIVITY_TASK_FAILED":
            attrs = event.activity_task_failed_event_attributes
            summary = f"failure={attrs.failure.message[:200]}"
        elif event_type_name == "EVENT_TYPE_WORKFLOW_EXECUTION_SIGNALED":
            attrs = event.workflow_execution_signaled_event_attributes
            summary = f"signal={attrs.signal_name}"
        elif event_type_name == "EVENT_TYPE_START_CHILD_WORKFLOW_EXECUTION_INITIATED":
            attrs = event.start_child_workflow_execution_initiated_event_attributes
            summary = f"child_id={attrs.workflow_id} type={attrs.workflow_type.name}"
        elif event_type_name == "EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_COMPLETED":
            attrs = event.child_workflow_execution_completed_event_attributes
            summary = f"child_id={attrs.workflow_execution.workflow_id}"
        elif event_type_name == "EVENT_TYPE_WORKFLOW_EXECUTION_FAILED":
            attrs = event.workflow_execution_failed_event_attributes
            summary = f"failure={attrs.failure.message[:200]}"
        elif event_type_name == "EVENT_TYPE_TIMER_STARTED":
            attrs = event.timer_started_event_attributes
            secs = attrs.start_to_fire_timeout.seconds if attrs.start_to_fire_timeout else 0
            summary = f"timer_id={attrs.timer_id} duration_s={secs}"
        elif event_type_name == "EVENT_TYPE_UPSERT_WORKFLOW_SEARCH_ATTRIBUTES":
            attrs = event.upsert_workflow_search_attributes_event_attributes
            keys = list((attrs.search_attributes.indexed_fields or {}).keys())
            summary = f"keys={keys}"
    except Exception:
        # If anything in the proto layout differs, fall back to type name only.
        pass

    timestamp = ""
    if event.event_time:
        try:
            timestamp = event.event_time.ToDatetime().isoformat() + "Z"
        except Exception:
            pass

    # Strip "EVENT_TYPE_" prefix for readability.
    short_type = event_type_name.removeprefix("EVENT_TYPE_") if hasattr(event_type_name, "removeprefix") else event_type_name
    return WorkflowHistoryEvent(
        event_id=event.event_id,
        timestamp_iso=timestamp,
        event_type=short_type,
        summary=summary,
    )


@activity.defn
async def get_workflow_history(input: GetWorkflowHistoryInput) -> GetWorkflowHistoryResult:
    """Return a structured summary of a workflow's event history. The agent
    uses this to understand exactly what happened in a workflow — which
    activities ran, which signals arrived, which children were started, etc.
    Bounded by `max_events` (default 200) to keep responses tight."""
    client = await _get_client()
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


@activity.defn
async def aggregate_repair_failures(input: AggregateFailuresInput) -> AggregateFailuresResult:
    """List recent OrderRepairWorkflows and bucket them by FailureType search attribute."""
    client = await _get_client()
    query = "WorkflowType='OrderRepairWorkflow'" + _since_clause(input.since_hours)

    counter: Counter = Counter()
    async for ex in client.list_workflows(query=query):
        ft = _first((ex.search_attributes or {}), "FailureType") or "unknown"
        counter[ft] += 1

    buckets = [FailureBucket(failure_type=k, count=v) for k, v in counter.most_common()]
    return AggregateFailuresResult(buckets=buckets, total=sum(counter.values()))


async def _fetch_catalog_from_api() -> list[dict]:
    """Pull the canonical catalog (with live `in_stock` from the API process).
    The worker's `shared.catalog.CATALOG` is stale once the API mutates state
    via reserve/release/adjust — so all reads go through HTTP."""
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.get(f"{API_BASE_URL}/api/catalog")
        resp.raise_for_status()
        return resp.json()


@activity.defn
async def list_inventory() -> ListInventoryResult:
    raw = await _fetch_catalog_from_api()
    items = [
        InventoryItem(
            book_id=b["id"],
            title=b["title"],
            author=b["author"],
            in_stock=b["in_stock"],
            physical_in_stock=b.get("physical_in_stock"),
            category=b["category"],
        )
        for b in raw
    ]
    return ListInventoryResult(items=items)


@activity.defn
async def get_book(input: GetBookInput) -> GetBookResult:
    raw = await _fetch_catalog_from_api()
    match = next((b for b in raw if b["id"] == input.book_id), None)
    if match is None:
        return GetBookResult(found=False, item=None)
    return GetBookResult(
        found=True,
        item=InventoryItem(
            book_id=match["id"],
            title=match["title"],
            author=match["author"],
            in_stock=match["in_stock"],
            physical_in_stock=match.get("physical_in_stock"),
            category=match["category"],
        ),
    )


# ---------------------------------------------------------------------------
# Mutation activities — gated by Block-Kit confirmation in the workflow layer.
# Idempotency: tool_use_id (Claude-generated, deterministic across replay) is
# tracked in a process-memory set. On replay or repeat invocation, the prior
# result is returned and no second mutation occurs. Sufficient for the demo.
# ---------------------------------------------------------------------------

_cancel_idempotency_cache: dict[str, CancelOrderResult] = {}
_adjust_idempotency_cache: dict[str, AdjustInventoryResult] = {}


@activity.defn
async def cancel_order(input: CancelOrderInput) -> CancelOrderResult:
    cached = _cancel_idempotency_cache.get(input.tool_use_id)
    if cached is not None:
        return cached

    client = await _get_client()
    workflow_id = f"order-{input.order_id}"
    handle = client.get_workflow_handle(workflow_id)
    try:
        await handle.cancel()
    except Exception as e:
        # Cancelling something that doesn't exist or is already closed: surface
        # to the agent as a non-retryable error so the executor wraps it as
        # is_error=True and the agent sees a clear message.
        raise ApplicationError(
            f"cancel_order failed for '{input.order_id}': {e}",
            type="OrderCancelFailed",
            non_retryable=True,
        )

    result = CancelOrderResult(
        cancelled=True,
        note=f"Order {input.order_id} cancellation requested. Reason: {input.reason}",
    )
    _cancel_idempotency_cache[input.tool_use_id] = result
    return result


@activity.defn
async def adjust_inventory(input: AdjustInventoryInput) -> AdjustInventoryResult:
    """Apply an inventory delta. Routes through the API so the storefront and
    every other reader (worker, agent) see the same canonical in_stock count.
    tool_use_id is sent as the API's idempotency_key so that workflow replay
    or activity retry doesn't double-apply."""
    cached = _adjust_idempotency_cache.get(input.tool_use_id)
    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.post(
                f"{API_BASE_URL}/api/inventory/adjust",
                json={
                    "book_id": input.book_id,
                    "delta": input.delta,
                    "reason": input.reason,
                    "idempotency_key": input.tool_use_id,
                },
            )
            if resp.status_code == 400:
                detail = resp.json().get("detail", "unknown error")
                raise ApplicationError(
                    f"adjust_inventory: {detail}",
                    type="UnknownBook",
                    non_retryable=True,
                )
            resp.raise_for_status()
            data = resp.json()
    except ApplicationError:
        raise
    except Exception as e:
        # Transient / network error — let Temporal retry.
        raise ApplicationError(
            f"adjust_inventory API call failed: {e}",
            type="ApiError",
        )

    new_count = int(data.get("in_stock", 0))
    book = get_book_by_id(input.book_id)  # local catalog only used for friendly title
    title = book.title if book else input.book_id
    result = AdjustInventoryResult(
        applied=True,
        new_in_stock=new_count,
        note=f"Stock for '{title}' adjusted by {input.delta:+d} → {new_count}. Reason: {input.reason}",
    )
    _adjust_idempotency_cache[input.tool_use_id] = result
    return result


# ---------------------------------------------------------------------------
# Slack-targeting activities — post Block Kit cards/pickers and collapse buttons.
# On SlackApiError these RETURN an is_error result instead of raising, so the
# agent loop can self-correct from a malformed-blocks response without crashing
# the activity-retry chain.
# ---------------------------------------------------------------------------


def _confirmation_blocks(input: PostConfirmationCardInput) -> list[dict]:
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"⚠️ *{input.title}*"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": input.description},
        },
        {
            "type": "actions",
            "block_id": f"ops_confirm_block_{input.tool_use_id}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": input.confirm_label[:75]},
                    "style": "primary",
                    "action_id": f"ops_confirm_{input.tool_use_id}",
                    "value": f"{input.workflow_id}|{input.tool_use_id}|confirm",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": input.deny_label[:75]},
                    "style": "danger",
                    "action_id": f"ops_deny_{input.tool_use_id}",
                    "value": f"{input.workflow_id}|{input.tool_use_id}|deny",
                },
            ],
        },
    ]


@activity.defn
async def post_confirmation_card(input: PostConfirmationCardInput) -> PostCardResult:
    client = AsyncWebClient(token=SLACK_BOT_TOKEN)
    blocks = _confirmation_blocks(input)
    try:
        resp = await client.chat_postMessage(
            channel=input.channel,
            thread_ts=input.thread_ts,
            blocks=blocks,
            text=input.title,
        )
    except SlackApiError as e:
        return PostCardResult(is_error=True, error_message=str(e))
    return PostCardResult(message_ts=resp["ts"])


def _picker_blocks(input: PostOrderPickerInput) -> list[dict]:
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": input.prompt}},
        {
            "type": "actions",
            "block_id": f"ops_picker_block_{input.tool_use_id}",
            "elements": [
                {
                    "type": "static_select",
                    "action_id": f"ops_picker_select_{input.tool_use_id}",
                    "placeholder": {"type": "plain_text", "text": "Choose…"},
                    "options": [
                        {
                            "text": {"type": "plain_text", "text": opt.label[:75]},
                            "value": opt.value,
                        }
                        for opt in input.options
                    ],
                }
            ],
        },
    ]


@activity.defn
async def post_order_picker(input: PostOrderPickerInput) -> PostCardResult:
    client = AsyncWebClient(token=SLACK_BOT_TOKEN)
    blocks = _picker_blocks(input)
    try:
        resp = await client.chat_postMessage(
            channel=input.channel,
            thread_ts=input.thread_ts,
            blocks=blocks,
            text=input.prompt,
        )
    except SlackApiError as e:
        return PostCardResult(is_error=True, error_message=str(e))
    return PostCardResult(message_ts=resp["ts"])


@activity.defn
async def collapse_buttons(input: CollapseButtonsInput) -> None:
    client = AsyncWebClient(token=SLACK_BOT_TOKEN)
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": input.summary_line}},
    ]
    try:
        await client.chat_update(
            channel=input.channel,
            ts=input.message_ts,
            blocks=blocks,
            text=input.summary_line,
        )
    except SlackApiError as e:
        # Best-effort: a failed update doesn't break correctness; log only.
        activity.logger.warning("collapse_buttons failed: %s", e)


@activity.defn
async def post_thread_reply(input: PostThreadReplyInput) -> PostThreadReplyResult:
    client = AsyncWebClient(token=SLACK_BOT_TOKEN)
    try:
        resp = await client.chat_postMessage(
            channel=input.channel,
            thread_ts=input.thread_ts,
            text=input.text,
        )
    except SlackApiError as e:
        return PostThreadReplyResult(is_error=True, error_message=str(e))
    return PostThreadReplyResult(message_ts=resp["ts"])


@activity.defn
async def post_rich_thread_reply(input: PostRichThreadReplyInput) -> PostThreadReplyResult:
    """Post a Block-Kit reply in the ops-agent thread. SlackApiError (e.g.
    invalid_blocks) is RETURNED as is_error so the agent can self-correct on
    its next turn — same pattern as the rest of the Slack-targeting activities."""
    client = AsyncWebClient(token=SLACK_BOT_TOKEN)
    try:
        resp = await client.chat_postMessage(
            channel=input.channel,
            thread_ts=input.thread_ts,
            blocks=input.blocks,
            text=input.fallback_text or "(rich reply)",
        )
    except SlackApiError as e:
        return PostThreadReplyResult(is_error=True, error_message=str(e))
    return PostThreadReplyResult(message_ts=resp["ts"])


@activity.defn
async def post_thread_closed_notice(input: PostThreadClosedNoticeInput) -> None:
    client = AsyncWebClient(token=SLACK_BOT_TOKEN)
    try:
        await client.chat_postMessage(
            channel=input.channel,
            thread_ts=input.thread_ts,
            text="🌙 This conversation has gone idle for 24h. Mention me again to start fresh.",
        )
    except SlackApiError as e:
        activity.logger.warning("post_thread_closed_notice failed: %s", e)
