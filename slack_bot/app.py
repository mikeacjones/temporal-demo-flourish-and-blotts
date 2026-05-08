"""Slack bot — Socket Mode app routing Slack events to Temporal workflow signals."""
import asyncio
import logging
import re

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter

from shared.models import SlackMessageSignal, SlackActionSignal, OpsAgentConversationInput, OpsActionSignal, CollapseButtonsInput  # noqa: F401 — imported for model-symmetry with Task 12
from worker.config import (
    SLACK_BOT_TOKEN,
    SLACK_APP_TOKEN,
    TEMPORAL_HOST,
    TEMPORAL_NAMESPACE,
    TASK_QUEUE,
)
from worker.workflows.slack_conversation_workflow import SlackConversationWorkflow
from worker.workflows.ops_agent_conversation_workflow import OpsAgentConversationWorkflow

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
log = logging.getLogger(__name__)

app = AsyncApp(token=SLACK_BOT_TOKEN)
_temporal_client: Client | None = None


async def get_temporal_client() -> Client:
    global _temporal_client
    if _temporal_client is None:
        _temporal_client = await Client.connect(
            TEMPORAL_HOST,
            namespace=TEMPORAL_NAMESPACE,
            data_converter=pydantic_data_converter,
        )
    return _temporal_client


async def _workflow_id_for_thread(channel_id: str, thread_ts: str) -> str | None:
    """Resolve the SlackConversationWorkflow ID for a thread by reading the root
    message's metadata. The worker stamps the metadata at post time — no Visibility
    query, no eventual-consistency race.
    """
    try:
        response = await app.client.conversations_replies(
            channel=channel_id, ts=thread_ts, limit=1, include_all_metadata=True,
        )
    except Exception as error:
        log.error(f"conversations_replies failed for thread_ts={thread_ts}: {error}")
        return None

    messages = response.get("messages", []) if response else []
    if not messages:
        return None

    metadata = messages[0].get("metadata") or {}
    if metadata.get("event_type") != "flourish_blotts_hitl":
        # Thread root isn't one of ours.
        return None
    payload = metadata.get("event_payload") or {}
    workflow_id = payload.get("workflow_id")
    if workflow_id:
        return workflow_id
    order_id = payload.get("order_id")
    if order_id:
        return f"slack-conv-{order_id}"
    return None


_bot_user_id_cache: str | None = None


async def _get_bot_user_id() -> str | None:
    """Resolve and cache the bot's own Slack user ID via auth.test.

    Used to dedupe events: when an @-mention arrives in a thread, Slack delivers
    BOTH a `message` event and an `app_mention` event for the same message. We
    let `handle_app_mention` handle mentions, so `handle_message` skips messages
    containing the bot's own mention to avoid double-signal.
    """
    global _bot_user_id_cache
    if _bot_user_id_cache is not None:
        return _bot_user_id_cache
    try:
        response = await app.client.auth_test()
        _bot_user_id_cache = response.get("user_id")
        return _bot_user_id_cache
    except Exception as error:
        log.warning(f"auth_test failed: {error}")
        return None


async def _resolve_user_name(user_id: str) -> str:
    """Best-effort lookup of a friendly display name for `user_id`."""
    try:
        info = await app.client.users_info(user=user_id)
        return info["user"].get("real_name") or info["user"].get("name") or user_id
    except Exception as error:
        log.warning(f"users_info failed for {user_id}: {error}")
        return user_id


@app.event("message")
async def handle_message(event, say):
    """Route Slack thread replies to the right workflow.

    Resolution order:
    1. If the thread root has HITL metadata → signal the SlackConversationWorkflow
       (existing behavior).
    2. Otherwise, if an OpsAgentConversationWorkflow exists at the deterministic
       ID `ops-agent-{channel}-{thread_ts}` → signal it. This handles plain
       thread replies after the user has @-mentioned the bot once.
    3. Otherwise → ignore (no workflow to deliver to).

    Messages that @-mention the bot are skipped here — `handle_app_mention`
    handles them, avoiding double-delivery.
    """
    thread_ts = event.get("thread_ts")
    if not thread_ts:
        # Top-level message — not a thread reply, ignore.
        return

    if event.get("bot_id") or event.get("subtype") == "bot_message":
        return

    text = event.get("text", "").strip()
    if not text:
        return

    channel_id = event.get("channel")
    if not channel_id:
        return

    # Skip @-mentions; handle_app_mention catches them. Avoids double-signal
    # when Slack delivers both `message` and `app_mention` for the same event.
    bot_user_id = await _get_bot_user_id()
    if bot_user_id and f"<@{bot_user_id}>" in text:
        return

    user_id = event.get("user", "unknown")
    timestamp = event.get("ts", "")
    client = await get_temporal_client()

    # 1) HITL repair-thread fast-path.
    hitl_workflow_id = await _workflow_id_for_thread(channel_id, thread_ts)
    if hitl_workflow_id:
        signal = SlackMessageSignal(
            user_id=user_id, user_name=user_id, text=text, timestamp=timestamp,
        )
        try:
            handle = client.get_workflow_handle(hitl_workflow_id)
            await handle.signal(SlackConversationWorkflow.receive_slack_message, signal)
            log.info(f"Forwarded message to HITL workflow {hitl_workflow_id}")
        except Exception as error:
            log.error(f"Failed to signal {hitl_workflow_id}: {error}")
        return

    # 2) Ops-agent thread: signal at the deterministic workflow ID if it exists.
    ops_workflow_id = f"ops-agent-{channel_id}-{thread_ts}"
    user_name = await _resolve_user_name(user_id)
    signal = SlackMessageSignal(
        user_id=user_id, user_name=user_name, text=text, timestamp=timestamp,
    )
    try:
        handle = client.get_workflow_handle(ops_workflow_id)
        await handle.signal(OpsAgentConversationWorkflow.receive_slack_message, signal)
        log.info(f"Forwarded message to ops-agent workflow {ops_workflow_id}")
    except Exception as error:
        # Most likely WorkflowNotFoundError — no ops-agent workflow at this ID.
        # That just means this thread isn't an ops-agent conversation; ignore.
        log.info(f"No ops-agent workflow at {ops_workflow_id} ({error}); ignoring.")


@app.event("app_mention")
async def handle_app_mention(event, say):
    """Start (or signal) an OpsAgentConversationWorkflow rooted at this thread.

    Rules:
    - thread_ts present → continuation of an existing thread; signal the workflow.
    - no thread_ts → new top-level mention; root the conversation at event.ts and
      start a fresh workflow.
    - if a SlackConversationWorkflow (HITL approve/deny) already exists for the
      same (channel, thread_ts), do NOT start an ops workflow — politely tell the
      user to mention the bot in a different thread.
    """
    channel = event.get("channel")
    thread_ts = event.get("thread_ts") or event.get("ts")
    user_id = event.get("user", "unknown")
    text = event.get("text", "").strip()
    if not channel or not thread_ts or not text:
        return

    user_name = await _resolve_user_name(user_id)

    # Guard: thread already busy with a HITL approval?
    existing_hitl = await _workflow_id_for_thread(channel, thread_ts)
    if existing_hitl and existing_hitl.startswith("slack-conv-"):
        try:
            await app.client.chat_postEphemeral(
                channel=channel, user=user_id,
                text="This thread is busy with an order-repair approval. Mention me in a new thread.",
            )
        except Exception as error:
            log.warning(f"chat_postEphemeral failed: {error}")
        return

    workflow_id = f"ops-agent-{channel}-{thread_ts}"
    client = await get_temporal_client()

    message_signal = SlackMessageSignal(
        user_id=user_id, user_name=user_name, text=text, timestamp=event.get("ts", ""),
    )

    # `start_signal` routes through the server-side SignalWithStart RPC, which
    # is atomic and idempotent against an already-running workflow ID — so we
    # don't need to set id_conflict_policy. If the workflow is running, the
    # message is signalled to it; if not, the workflow starts and immediately
    # receives the signal as its first inbox entry.
    try:
        await client.start_workflow(
            OpsAgentConversationWorkflow.run,
            OpsAgentConversationInput(
                channel=channel, thread_ts=thread_ts,
                initial_message=text, user_id=user_id, user_name=user_name,
            ),
            id=workflow_id,
            task_queue=TASK_QUEUE,
            start_signal="receive_slack_message",
            start_signal_args=[message_signal],
        )
        log.info(f"Started or signalled ops-agent workflow {workflow_id}")
    except Exception as error:
        log.error(f"Failed to start/signal ops-agent workflow {workflow_id}: {error}")


async def _collapse_hitl_buttons(body: dict, signal: SlackActionSignal) -> None:
    """Replace the Approve/Deny button row with a static confirmation line.

    Called after the workflow signal has fired so the operator can't double-click.
    A failed chat_update is logged but never raises — the signal is the source of
    truth; the visual collapse is best-effort.
    """
    message = body.get("message", {})
    channel = body.get("channel", {}).get("id") or body.get("container", {}).get("channel_id", "")
    message_ts = message.get("ts", "")
    if channel and message_ts:
        try:
            await app.client.chat_update(
                channel=channel,
                ts=message_ts,
                blocks=[{
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"✅ {signal.action_id.title()}d by <@{signal.user_id}>",
                    },
                }],
                text=f"{signal.action_id.title()}d by {signal.user_name}",
            )
        except Exception as error:
            log.warning(f"chat_update (button collapse) failed: {error}")


@app.action("approve")
async def handle_approve(ack, body, action):
    """Handle Approve button click."""
    await ack()

    workflow_id = action.get("value", "")
    user = body.get("user", {})

    if not workflow_id:
        return

    client = await get_temporal_client()
    handle = client.get_workflow_handle(workflow_id)

    signal = SlackActionSignal(
        action_id="approve",
        user_id=user.get("id", "unknown"),
        user_name=user.get("name", user.get("username", "Operator")),
        timestamp=body.get("actions", [{}])[0].get("action_ts", ""),
    )

    try:
        await handle.signal(SlackConversationWorkflow.receive_slack_action, signal)
        log.info(f"Approved workflow {workflow_id} by {signal.user_name}")
    except Exception as error:
        log.error(f"Failed to approve workflow {workflow_id}: {error}")

    await _collapse_hitl_buttons(body, signal)


@app.action("deny")
async def handle_deny(ack, body, action):
    """Handle Deny button click."""
    await ack()

    workflow_id = action.get("value", "")
    user = body.get("user", {})

    if not workflow_id:
        return

    client = await get_temporal_client()
    handle = client.get_workflow_handle(workflow_id)

    signal = SlackActionSignal(
        action_id="deny",
        user_id=user.get("id", "unknown"),
        user_name=user.get("name", user.get("username", "Operator")),
        timestamp=body.get("actions", [{}])[0].get("action_ts", ""),
    )

    try:
        await handle.signal(SlackConversationWorkflow.receive_slack_action, signal)
        log.info(f"Denied workflow {workflow_id} by {signal.user_name}")
    except Exception as error:
        log.error(f"Failed to deny workflow {workflow_id}: {error}")

    await _collapse_hitl_buttons(body, signal)


def _parse_ops_value(encoded_value: str) -> tuple[str, str, str] | None:
    parts = encoded_value.split("|")
    if len(parts) != 3:
        return None
    return parts[0], parts[1], parts[2]


async def _post_ephemeral_stale(channel: str, user_id: str) -> None:
    """The operator clicked a button whose workflow has ended (idle timeout, etc.).
    Tell them so they don't sit confused."""
    try:
        await app.client.chat_postEphemeral(
            channel=channel, user=user_id,
            text="This conversation has ended (idle timeout or already resolved). Mention me again to start fresh.",
        )
    except Exception as error:
        log.warning(f"chat_postEphemeral failed: {error}")


async def _signal_or_warn_stale(handle, signal_method, signal_payload, channel: str, user_id: str, label: str) -> None:
    try:
        await handle.signal(signal_method, signal_payload)
    except Exception as error:
        # Most likely WorkflowNotFoundError (workflow ended). The catch is broad on
        # purpose: even unexpected signal failures shouldn't leave the operator's
        # click silently swallowed.
        log.warning(f"{label} signal failed: {error}")
        await _post_ephemeral_stale(channel, user_id)


async def _handle_ops_confirm_deny(ack, body, action, *, choice: str) -> None:
    await ack()
    # Use 'or ""' rather than get("value", "") so that a key present with
    # value None doesn't reach _parse_ops_value as None (which would raise on
    # .split("|")).
    parsed = _parse_ops_value(action.get("value") or "")
    if not parsed:
        return
    workflow_id, tool_use_id, _action_value = parsed
    user = body.get("user", {}) or {}
    user_id = user.get("id", "unknown")
    channel = body.get("channel", {}).get("id", "") if isinstance(body.get("channel"), dict) else ""
    signal = OpsActionSignal(
        tool_use_id=tool_use_id, value=choice,
        user_id=user_id,
        user_name=user.get("name", user.get("username", "operator")),
        timestamp=(body.get("actions") or [{}])[0].get("action_ts", ""),
    )
    client = await get_temporal_client()
    handle = client.get_workflow_handle(workflow_id)
    await _signal_or_warn_stale(
        handle, OpsAgentConversationWorkflow.receive_slack_action, signal,
        channel=channel, user_id=user_id, label=f"ops_{choice}",
    )


@app.action(re.compile(r"^ops_confirm_.+$"))
async def handle_ops_confirm(ack, body, action):
    await _handle_ops_confirm_deny(ack, body, action, choice="confirm")


@app.action(re.compile(r"^ops_deny_.+$"))
async def handle_ops_deny(ack, body, action):
    await _handle_ops_confirm_deny(ack, body, action, choice="deny")


@app.action(re.compile(r"^ops_picker_select_.+$"))
async def handle_ops_picker_select(ack, body, action):
    await ack()
    # Picker action_ids encode the tool_use_id in the action_id suffix; the
    # selected value is the order_id chosen from the dropdown.
    # action_id has the regex-enforced 'ops_picker_select_' prefix.
    action_id = action.get("action_id", "")
    tool_use_id = action_id[len("ops_picker_select_"):]
    selected = action.get("selected_option", {}).get("value", "")
    if not selected:
        return

    # The workflow_id is reconstructed from the channel/thread the action came in on.
    channel = body.get("channel", {}).get("id", "")
    message = body.get("message", {})
    thread_ts = message.get("thread_ts") or message.get("ts", "")
    workflow_id = f"ops-agent-{channel}-{thread_ts}"

    user = body.get("user", {})
    user_id = user.get("id", "unknown")
    signal = OpsActionSignal(
        tool_use_id=tool_use_id, value=selected,
        user_id=user_id,
        user_name=user.get("name", user.get("username", "operator")),
        timestamp=body.get("actions", [{}])[0].get("action_ts", ""),
    )
    client = await get_temporal_client()
    handle = client.get_workflow_handle(workflow_id)
    await _signal_or_warn_stale(
        handle, OpsAgentConversationWorkflow.receive_slack_action, signal,
        channel=channel, user_id=user_id, label="ops_picker_select",
    )


async def main():
    if not SLACK_BOT_TOKEN or not SLACK_APP_TOKEN:
        log.warning("SLACK_BOT_TOKEN or SLACK_APP_TOKEN not set — Slack bot will not start")
        return

    handler = AsyncSocketModeHandler(app, SLACK_APP_TOKEN)
    log.info("Starting Slack bot in Socket Mode")
    await handler.start_async()


if __name__ == "__main__":
    asyncio.run(main())
