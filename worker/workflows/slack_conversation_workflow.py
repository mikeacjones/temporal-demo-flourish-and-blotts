"""SlackConversationWorkflow — durable HITL conversation as a Temporal entity workflow."""
from __future__ import annotations

from datetime import timedelta
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from shared.models import (
        SlackConversationInput,
        SlackConversationResult,
        SlackMessageSignal,
        SlackActionSignal,
        ConversationTurn,
        PostInitialMessageInput,
        PostReplyInput,
        ProcessMessageInput,
        ProcessMessageResult,
    )
    from worker.config import SLACK_BOT_TOKEN
    from worker.activities.slack_activities import (
        post_initial_slack_message,
        post_slack_reply,
        process_conversation_message,
    )

HITL_TIMEOUT = timedelta(hours=24)
SLACK_TIMEOUT = timedelta(seconds=30)
CLAUDE_TIMEOUT = timedelta(seconds=60)


@workflow.defn
class SlackConversationWorkflow:
    def __init__(self):
        self._pending_messages: list[SlackMessageSignal] = []
        self._pending_action: SlackActionSignal | None = None
        self._resolved = False
        self._thread_ts: str = ""
        self._history: list[ConversationTurn] = []

    @workflow.signal
    async def receive_slack_message(self, message: SlackMessageSignal) -> None:
        if not self._resolved:
            self._pending_messages.append(message)

    @workflow.signal
    async def receive_slack_action(self, action: SlackActionSignal) -> None:
        if not self._resolved:
            self._pending_action = action
            self._resolved = True

    @workflow.query
    def thread_ts(self) -> str:
        return self._thread_ts

    @workflow.query
    def conversation_history(self) -> list[dict]:
        return [{"role": t.role, "content": t.content, "timestamp": t.timestamp} for t in self._history]

    @workflow.run
    async def run(self, input: SlackConversationInput) -> SlackConversationResult:
        current_plan = input.initial_plan

        # Post the initial Slack message and store the thread timestamp
        self._thread_ts = await workflow.execute_activity(
            post_initial_slack_message,
            PostInitialMessageInput(
                channel=input.slack_channel,
                order_id=input.order_id,
                order_input=input.order_input,
                failure=input.failure,
                plan=current_plan,
                workflow_id=workflow.info().workflow_id,
            ),
            start_to_close_timeout=SLACK_TIMEOUT,
        )

        # Store context for operational visibility. The Slack bot does not use a search
        # attribute to find this workflow — its workflow ID is deterministic (slack-conv-{order_id}).
        workflow.upsert_search_attributes({
            "OrderId": [input.order_id],
            "OrderStatus": ["awaiting_hitl"],
        })

        # Main conversation loop
        while not self._resolved:
            try:
                await workflow.wait_condition(
                    lambda: len(self._pending_messages) > 0 or self._pending_action is not None,
                    timeout=HITL_TIMEOUT,
                )
            except Exception:
                # Timeout
                return SlackConversationResult(
                    status="timeout",
                    final_plan=None,
                    conversation_history=self._history,
                    notes="No response received within 24 hours. Order requires manual review.",
                )

            # Handle approve/deny action (takes priority)
            if self._pending_action:
                action = self._pending_action
                if action.action_id == "approve":
                    return SlackConversationResult(
                        status="approved",
                        final_plan=current_plan,
                        conversation_history=self._history,
                        decided_by=action.user_name,
                    )
                else:
                    return SlackConversationResult(
                        status="denied",
                        final_plan=None,
                        conversation_history=self._history,
                        decided_by=action.user_name,
                        notes="Operator denied the repair plan.",
                    )

            # Process all pending messages
            while self._pending_messages and not self._resolved:
                message = self._pending_messages.pop(0)

                self._history.append(
                    ConversationTurn(
                        role="human",
                        content=message.text,
                        timestamp=message.timestamp,
                    )
                )

                process_result: ProcessMessageResult = await workflow.execute_activity(
                    process_conversation_message,
                    ProcessMessageInput(
                        message=message,
                        order_input=input.order_input,
                        failure=input.failure,
                        current_plan=current_plan,
                        history=self._history,
                    ),
                    start_to_close_timeout=CLAUDE_TIMEOUT,
                )

                if process_result.updated_plan:
                    current_plan = process_result.updated_plan

                agent_ts = str(workflow.now().timestamp())
                self._history.append(
                    ConversationTurn(
                        role="agent",
                        content=process_result.response_text,
                        timestamp=agent_ts,
                    )
                )

                await workflow.execute_activity(
                    post_slack_reply,
                    PostReplyInput(
                        channel=input.slack_channel,
                        thread_ts=self._thread_ts,
                        message=process_result.response_text,
                        updated_plan=process_result.updated_plan,
                        workflow_id=workflow.info().workflow_id,
                    ),
                    start_to_close_timeout=SLACK_TIMEOUT,
                )

        # _resolved was set by receive_slack_action signal
        action = self._pending_action
        if action and action.action_id == "approve":
            return SlackConversationResult(
                status="approved",
                final_plan=current_plan,
                conversation_history=self._history,
                decided_by=action.user_name,
            )

        return SlackConversationResult(
            status="denied",
            final_plan=None,
            conversation_history=self._history,
            decided_by=action.user_name if action else None,
            notes="Order repair denied by operator.",
        )
