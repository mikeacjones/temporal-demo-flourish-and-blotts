"""Post a richly-formatted reply in the ops thread using Slack Block Kit."""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

from shared.agent_harness import ToolCategory, ToolCtx, ops_tool

with workflow.unsafe.imports_passed_through():
    from shared.models import PostRichThreadReplyInput, PostThreadReplyResult
    from worker.agent.tool_args import PostRichReplyArgs


_DEFAULT_TIMEOUT = timedelta(seconds=30)


async def _post_blocks(input_: PostRichThreadReplyInput) -> PostThreadReplyResult:
    """Post a Block-Kit reply to Slack.

    PORT FROM: worker/activities/ops_activities.py:post_rich_thread_reply body.
    """
    from slack_sdk.errors import SlackApiError
    from slack_sdk.web.async_client import AsyncWebClient

    from worker.config import SLACK_BOT_TOKEN

    client = AsyncWebClient(token=SLACK_BOT_TOKEN)
    try:
        response = await client.chat_postMessage(
            channel=input_.channel,
            thread_ts=input_.thread_ts,
            blocks=input_.blocks,
            text=input_.fallback_text or "(rich reply)",
        )
    except SlackApiError as error:
        return PostThreadReplyResult(is_error=True, error_message=str(error))
    return PostThreadReplyResult(message_ts=response["ts"])


@ops_tool(category=ToolCategory.SLACK_OUTPUT, timeout=_DEFAULT_TIMEOUT)
async def post_rich_reply(args: PostRichReplyArgs, ctx: ToolCtx) -> str:
    """Post a richly-formatted reply in the thread using Slack Block Kit. Use this when \
plain Slack-mrkdwn isn't enough — comparisons across many fields, multi-section \
breakdowns, key-value lists, or anything where you want headers/dividers/contextual \
footers. Pass a list of Block Kit block objects. Section text uses Slack mrkdwn \
(single-asterisk *bold*, _italic_, `code`, <https://url|label>) — NOT Markdown. \
No tables — use a section with `fields` for columns. On error returns is_error=True. \
When you call this tool, do NOT also include a redundant prose response — let the \
rich reply speak for itself."""
    assert ctx.channel and ctx.thread_ts, "post_rich_reply requires Slack ctx"
    result = await ctx.activity(
        _post_blocks,
        PostRichThreadReplyInput(
            channel=ctx.channel,
            thread_ts=ctx.thread_ts,
            blocks=args.blocks,
            fallback_text=args.fallback_text,
        ),
        summary="Post a Block-Kit-formatted reply in the ops thread.",
        start_to_close_timeout=_DEFAULT_TIMEOUT,
    )
    if result.is_error:
        return f"Could not post rich reply: {result.error_message}"
    return f"Rich reply posted (message_ts={result.message_ts})."
