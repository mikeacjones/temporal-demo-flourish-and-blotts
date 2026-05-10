"""Activities for the Slack ops-agent.

Slack-targeting activities (post_confirmation_card, post_order_picker,
collapse_buttons, post_thread_reply, post_thread_closed_notice) are registered
with the Temporal worker and called from non-tool workflows.

Idempotency caches (_cancel_idempotency_cache, _adjust_idempotency_cache) and
the lazy Temporal client (_get_client) are shared with the per-tool activity
bodies in worker/agent/tools/cancel_order.py and
worker/agent/tools/adjust_inventory.py which import them by name.

The Temporal client is lazily constructed and cached at module level. For
tests, monkeypatch `_get_client` to return a stub.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient
from temporalio import activity
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter

from shared.models import (
    AdjustInventoryResult,
    CancelOrderResult,
    CollapseButtonsInput,
    PostCardResult,
    PostConfirmationCardInput,
    PostOrderPickerInput,
    PostThreadClosedNoticeInput,
    PostThreadReplyInput,
    PostThreadReplyResult,
)
from worker.config import SLACK_BOT_TOKEN, TEMPORAL_HOST, TEMPORAL_NAMESPACE


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


# ---------------------------------------------------------------------------
# Idempotency caches shared with per-tool activity bodies.
# worker/agent/tools/cancel_order.py and worker/agent/tools/adjust_inventory.py
# import these by name so that repeated tool invocations across workflow replay
# are de-duplicated in a single process-memory store.
# ---------------------------------------------------------------------------

_cancel_idempotency_cache: dict[str, CancelOrderResult] = {}
_adjust_idempotency_cache: dict[str, AdjustInventoryResult] = {}


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
        response = await client.chat_postMessage(
            channel=input.channel,
            thread_ts=input.thread_ts,
            blocks=blocks,
            text=input.title,
        )
    except SlackApiError as error:
        return PostCardResult(is_error=True, error_message=str(error))
    return PostCardResult(message_ts=response["ts"])


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
                            "text": {"type": "plain_text", "text": option.label[:75]},
                            "value": option.value,
                        }
                        for option in input.options
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
        response = await client.chat_postMessage(
            channel=input.channel,
            thread_ts=input.thread_ts,
            blocks=blocks,
            text=input.prompt,
        )
    except SlackApiError as error:
        return PostCardResult(is_error=True, error_message=str(error))
    return PostCardResult(message_ts=response["ts"])


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
    except SlackApiError as error:
        # Best-effort: a failed update doesn't break correctness; log only.
        activity.logger.warning("collapse_buttons failed: %s", error)


@activity.defn
async def post_thread_reply(input: PostThreadReplyInput) -> PostThreadReplyResult:
    client = AsyncWebClient(token=SLACK_BOT_TOKEN)
    try:
        response = await client.chat_postMessage(
            channel=input.channel,
            thread_ts=input.thread_ts,
            text=input.text,
        )
    except SlackApiError as error:
        return PostThreadReplyResult(is_error=True, error_message=str(error))
    return PostThreadReplyResult(message_ts=response["ts"])


@activity.defn
async def post_thread_closed_notice(input: PostThreadClosedNoticeInput) -> None:
    client = AsyncWebClient(token=SLACK_BOT_TOKEN)
    try:
        await client.chat_postMessage(
            channel=input.channel,
            thread_ts=input.thread_ts,
            text="🌙 This conversation has gone idle for 24h. Mention me again to start fresh.",
        )
    except SlackApiError as error:
        activity.logger.warning("post_thread_closed_notice failed: %s", error)
