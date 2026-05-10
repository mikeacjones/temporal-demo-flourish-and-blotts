"""Post an interactive order picker in the ops thread and await operator selection."""
from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow

from shared.agent_harness import ToolCategory, ToolCtx, ops_tool

with workflow.unsafe.imports_passed_through():
    from shared.models import (
        CollapseButtonsInput,
        ListOrdersInput,
        ListOrdersResult,
        OrderSummary,
        PickerOption,
        PostCardResult,
        PostOrderPickerInput,
    )
    from worker.agent.tool_args import PostOrderPickerArgs


_SLACK_TIMEOUT = timedelta(seconds=30)
_READ_TOOL_TIMEOUT = timedelta(seconds=10)


def _first(search_attributes: dict, search_attribute_name: str) -> str:
    """Search-attribute values are lists in Temporal — pull the first scalar."""
    attribute_value = search_attributes.get(search_attribute_name)
    if isinstance(attribute_value, list):
        return str(attribute_value[0]) if attribute_value else ""
    if attribute_value is None:
        return ""
    return str(attribute_value)


async def _list_orders_for_picker(input_: ListOrdersInput) -> ListOrdersResult:
    """Query Temporal Visibility for orders matching the filter.

    PORT FROM: worker/activities/ops_activities.py:list_orders body.
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
    if input_.status:
        parts.append(f"OrderStatus='{input_.status}'")
    else:
        parts.append("WorkflowType='OrderWorkflow'")
    if input_.failure_type:
        parts.append(f"FailureType='{input_.failure_type}'")
    query = " AND ".join(parts)

    fetch_cap = input_.limit * 3 if input_.status else input_.limit
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
        if len(by_order) >= input_.limit:
            break

    return ListOrdersResult(orders=list(by_order.values()))


def _picker_blocks(input_: PostOrderPickerInput) -> list[dict]:
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": input_.prompt}},
        {
            "type": "actions",
            "block_id": f"ops_picker_block_{input_.tool_use_id}",
            "elements": [
                {
                    "type": "static_select",
                    "action_id": f"ops_picker_select_{input_.tool_use_id}",
                    "placeholder": {"type": "plain_text", "text": "Choose…"},
                    "options": [
                        {
                            "text": {"type": "plain_text", "text": option.label[:75]},
                            "value": option.value,
                        }
                        for option in input_.options
                    ],
                }
            ],
        },
    ]


async def _post_picker(input_: PostOrderPickerInput) -> PostCardResult:
    """Post a Block-Kit dropdown of orders to Slack.

    PORT FROM: worker/activities/ops_activities.py:post_order_picker body.
    """
    from slack_sdk.errors import SlackApiError
    from slack_sdk.web.async_client import AsyncWebClient

    from worker.config import SLACK_BOT_TOKEN

    client = AsyncWebClient(token=SLACK_BOT_TOKEN)
    blocks = _picker_blocks(input_)
    try:
        response = await client.chat_postMessage(
            channel=input_.channel,
            thread_ts=input_.thread_ts,
            blocks=blocks,
            text=input_.prompt,
        )
    except SlackApiError as error:
        return PostCardResult(is_error=True, error_message=str(error))
    return PostCardResult(message_ts=response["ts"])


async def _collapse_picker_buttons(input_: CollapseButtonsInput) -> None:
    """Replace the picker's button row with a static summary line.

    PORT FROM: worker/activities/ops_activities.py:collapse_buttons body.
    """
    from slack_sdk.errors import SlackApiError
    from slack_sdk.web.async_client import AsyncWebClient
    from temporalio import activity

    from worker.config import SLACK_BOT_TOKEN

    client = AsyncWebClient(token=SLACK_BOT_TOKEN)
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": input_.summary_line}},
    ]
    try:
        await client.chat_update(
            channel=input_.channel,
            ts=input_.message_ts,
            blocks=blocks,
            text=input_.summary_line,
        )
    except SlackApiError as error:
        # Best-effort: a failed update doesn't break correctness; log only.
        activity.logger.warning("_collapse_picker_buttons failed: %s", error)


@ops_tool(category=ToolCategory.HITL_INTERACTION)
async def post_order_picker(args: PostOrderPickerArgs, ctx: ToolCtx) -> str:
    """Post an interactive dropdown of in-flight orders in the thread and return the \
order_id the operator selects. Use when the operator should choose which order \
to act on. Returns the selected order_id."""
    assert ctx.channel and ctx.thread_ts, "post_order_picker requires Slack ctx"

    list_result = await ctx.activity(
        _list_orders_for_picker,
        ListOrdersInput(status=args.status_filter),
        summary=f"List orders to populate the picker (status filter: {args.status_filter or 'any'}).",
        start_to_close_timeout=_READ_TOOL_TIMEOUT,
    )
    if not list_result.orders:
        return "No in-flight orders match the requested filter."

    options = [
        PickerOption(value=order.order_id, label=f"{order.order_id} ({order.order_status})")
        for order in list_result.orders[:25]
    ]

    future: asyncio.Future[str] = asyncio.Future()
    ctx.pending_actions[ctx.tool_use_id] = future
    try:
        post_result = await ctx.activity(
            _post_picker,
            PostOrderPickerInput(
                channel=ctx.channel,
                thread_ts=ctx.thread_ts,
                workflow_id=workflow.info().workflow_id,
                tool_use_id=ctx.tool_use_id,
                prompt=args.prompt,
                options=options,
            ),
            summary="Post the order-picker dropdown to Slack.",
            start_to_close_timeout=_SLACK_TIMEOUT,
        )
        if post_result.is_error:
            return f"Could not post picker: {post_result.error_message}"
        selected = await future
    finally:
        ctx.pending_actions.pop(ctx.tool_use_id, None)

    await ctx.activity(
        _collapse_picker_buttons,
        CollapseButtonsInput(
            channel=ctx.channel,
            message_ts=post_result.message_ts,
            summary_line=f"\U0001f4cc Selected: {selected}",
        ),
        summary=f"Collapse picker buttons after operator selected {selected}.",
        start_to_close_timeout=_SLACK_TIMEOUT,
    )
    return f"Operator selected order_id={selected}"
