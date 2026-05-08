"""Slack activities — posting messages and managing thread conversations."""
from temporalio import activity
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.errors import SlackApiError

from shared.models import (
    PostInitialMessageInput,
    PostReplyInput,
    ProcessMessageInput,
    ProcessMessageResult,
    RepairPlan,
    RepairPlanStep,
)
import anthropic
from worker.config import SLACK_BOT_TOKEN, TEMPORAL_UI_URL, ANTHROPIC_API_KEY
from worker.agent.tools import CONVERSATION_TOOLS
from worker.activities.claude_activities import _serialize_content


def _format_plan_blocks(plan: RepairPlan | None) -> list[dict]:
    if not plan or not plan.steps:
        return [{"type": "section", "text": {"type": "mrkdwn", "text": "_No automated plan available. Human judgment required._"}}]

    steps_text = "\n".join(
        f"*{step_index + 1}.* {step.description}"
        for step_index, step in enumerate(plan.steps)
    )
    urgency_emoji = {"low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴"}.get(plan.urgency, "🟡")

    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{urgency_emoji} *Proposed Repair Plan* ({plan.urgency.upper()} urgency)\n\n{steps_text}",
            },
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Rationale:* {plan.rationale}"},
        },
    ]


@activity.defn
async def post_initial_slack_message(input: PostInitialMessageInput) -> str:
    """Post the initial HITL message to Slack. Returns the thread_ts."""
    client = AsyncWebClient(token=SLACK_BOT_TOKEN)

    order = input.order_input
    failure = input.failure
    temporal_url = f"{TEMPORAL_UI_URL}/namespaces/default/workflows/{input.workflow_id}"

    failure_emoji = {
        "monster_book_escape": "📚💥",
        "ministry_approval_required": "🏛️📜",
        "restricted_section": "🔒📖",
        "gringotts_failure": "🏦⚠️",
        "owl_intercepted": "🦉❌",
        "floo_misdirected": "🔥🌀",
        "inventory_mismatch": "📦❓",
        "warehouse_failure": "🏭⚡",
        "payment_timeout": "💳⏱️",
    }.get(failure.failure_type, "⚠️")

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🧙 Flourish & Blotts — Order Repair Required", "emoji": True},
        },
        {"type": "divider"},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Order ID:*\n`{order.order_id}`"},
                {"type": "mrkdwn", "text": f"*Customer:*\n{order.customer_name}"},
                {"type": "mrkdwn", "text": f"*Book:*\n_{order.book_title}_"},
                {"type": "mrkdwn", "text": f"*Delivery:*\n{order.delivery_method.replace('_', ' ').title()}"},
            ],
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{failure_emoji} *Failure Detected — Step: `{failure.step}`*\n\n{failure.description}",
            },
        },
        {"type": "divider"},
        *_format_plan_blocks(input.plan),
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "_Reply to this thread to modify the plan, ask questions, or provide additional context. Use the buttons below to approve or deny._",
            },
        },
        {
            "type": "actions",
            "block_id": f"approval_{input.workflow_id}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✅ Approve Plan", "emoji": True},
                    "style": "primary",
                    "action_id": "approve",
                    "value": input.workflow_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "❌ Deny & Cancel Order", "emoji": True},
                    "style": "danger",
                    "action_id": "deny",
                    "value": input.workflow_id,
                },
            ],
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"<{temporal_url}|View in Temporal Web UI>"},
            ],
        },
    ]

    response = await client.chat_postMessage(
        channel=input.channel,
        blocks=blocks,
        text=f"Order repair required: {order.order_id}",
        # Slack message metadata — the bot reads this back on thread replies to
        # route signals deterministically (no Visibility query required).
        metadata={
            "event_type": "flourish_blotts_hitl",
            "event_payload": {
                "order_id": order.order_id,
                "workflow_id": input.workflow_id,
            },
        },
    )
    return response["ts"]


@activity.defn
async def post_slack_reply(input: PostReplyInput) -> None:
    """Post a reply to an existing Slack conversation thread."""
    client = AsyncWebClient(token=SLACK_BOT_TOKEN)

    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"🤖 *Agent Response:*\n\n{input.message}"},
        },
    ]

    if input.updated_plan:
        blocks.append({"type": "divider"})
        blocks.extend(_format_plan_blocks(input.updated_plan))
        blocks.append(
            {
                "type": "actions",
                "block_id": f"approval_{input.workflow_id}_update",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✅ Approve Updated Plan", "emoji": True},
                        "style": "primary",
                        "action_id": "approve",
                        "value": input.workflow_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "❌ Deny & Cancel Order", "emoji": True},
                        "style": "danger",
                        "action_id": "deny",
                        "value": input.workflow_id,
                    },
                ],
            }
        )

    await client.chat_postMessage(
        channel=input.channel,
        thread_ts=input.thread_ts,
        blocks=blocks,
        text=input.message,
    )


@activity.defn
async def process_conversation_message(input: ProcessMessageInput) -> ProcessMessageResult:
    """Use Claude to interpret a human's Slack reply and update the plan if needed."""
    order = input.order_input
    failure = input.failure

    history_text = "\n".join(
        f"[{turn.role.upper()}]: {turn.content}" for turn in input.history
    ) if input.history else "_No prior conversation_"

    current_plan_text = ""
    if input.current_plan:
        steps = "\n".join(
            f"{step_index + 1}. {step.description}"
            for step_index, step in enumerate(input.current_plan.steps)
        )
        current_plan_text = f"\n\nCurrent Plan:\n{steps}\n\nRationale: {input.current_plan.rationale}"

    system = f"""You are the AI assistant for Flourish & Blotts OMS managing a human approval conversation.

Order context:
- Order ID: {order.order_id}
- Customer: {order.customer_name}
- Book: {order.book_title}
- Delivery: {order.delivery_method}
- Failure: {failure.description}
{current_plan_text}

Conversation so far:
{history_text}

Your role:
1. Interpret what the human operator wants
2. If they want to modify the plan, call the update_plan tool with the revised steps and a response message
3. If they're asking a question or just chatting, respond helpfully without calling update_plan
4. Keep responses concise and professional, with a touch of wizarding charm
5. Never approve or deny yourself — that's the human's job via the buttons"""

    messages = [{"role": "user", "content": input.message.text}]

    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY, max_retries=0)
    raw_response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=system,
        messages=messages,
        tools=CONVERSATION_TOOLS,
    )
    serialized = _serialize_content(raw_response.content)
    text = next((block["text"] for block in serialized if block["type"] == "text"), "")
    tool_use_blocks = [block for block in serialized if block["type"] == "tool_use"]

    from shared.models import ClaudeResponse, ClaudeToolUse
    response = ClaudeResponse(
        stop_reason=raw_response.stop_reason,
        text=text,
        content=serialized,
        tool_uses=[
            ClaudeToolUse(
                id=tool_use_block["id"],
                name=tool_use_block["name"],
                input=tool_use_block["input"],
            )
            for tool_use_block in tool_use_blocks
        ],
    )

    updated_plan = None
    response_text = response.text

    for tool_use in response.tool_uses:
        if tool_use.name == "update_plan":
            tool_input = tool_use.input
            updated_plan = RepairPlan(
                steps=[
                    RepairPlanStep(
                        action=step_data.get("action", ""),
                        description=step_data.get("description", ""),
                        tool=step_data.get("tool"),
                        tool_args=step_data.get("tool_args", {}),
                    )
                    for step_data in tool_input.get("steps", [])
                ],
                rationale=tool_input.get("rationale", ""),
                urgency=input.current_plan.urgency if input.current_plan else "medium",
            )
            response_text = tool_input.get("response_to_human", response.text)

    if not response_text:
        response_text = "Understood. The plan remains as proposed. Use the buttons above to approve or deny."

    return ProcessMessageResult(response_text=response_text, updated_plan=updated_plan)
