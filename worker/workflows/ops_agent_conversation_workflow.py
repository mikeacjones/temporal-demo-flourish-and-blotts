"""OpsAgentConversationWorkflow — per-Slack-thread conversational entity."""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from shared.agent_harness import AgentCtx, run_agent_turn
    from shared.models import (
        OpsActionSignal,
        OpsAgentConversationInput,
        PostThreadClosedNoticeInput,
        PostThreadReplyInput,
        SlackMessageSignal,
    )
    from worker.activities.ops_activities import (
        post_thread_closed_notice,
        post_thread_reply,
    )
    from worker.agent.ops_tools import OPS_TOOLS, build_ops_system_prompt


IDLE_TIMEOUT = timedelta(days=1)
SLACK_TIMEOUT = timedelta(seconds=30)
MAX_TOOL_TURNS_PER_MESSAGE = 8


@workflow.defn
class OpsAgentConversationWorkflow:
    def __init__(self) -> None:
        self._inbox: list[SlackMessageSignal] = []
        self._closed = False
        self._messages: list[dict] = []
        self._history: list[dict] = []
        self._ctx = AgentCtx()  # populated in run()

    @workflow.signal
    async def receive_slack_message(self, message: SlackMessageSignal) -> None:
        if self._closed:
            return
        self._inbox.append(message)

    @workflow.signal
    async def receive_slack_action(self, action: OpsActionSignal) -> None:
        if self._closed:
            return
        future = self._ctx.pending_actions.get(action.tool_use_id)
        if future is not None and not future.done():
            future.set_result(action.value)

    @workflow.query
    def conversation_history(self) -> list[dict]:
        return list(self._history)

    @workflow.run
    async def run(self, input: OpsAgentConversationInput) -> str:
        self._ctx = AgentCtx(
            channel=input.channel,
            thread_ts=input.thread_ts,
            domain_input=input,
        )
        system = build_ops_system_prompt(input.user_name)

        while not self._closed:
            try:
                await workflow.wait_condition(
                    lambda: len(self._inbox) > 0,
                    timeout=IDLE_TIMEOUT,
                )
            except TimeoutError:
                self._closed = True
                await workflow.execute_activity(
                    post_thread_closed_notice,
                    PostThreadClosedNoticeInput(
                        channel=input.channel, thread_ts=input.thread_ts,
                    ),
                    start_to_close_timeout=SLACK_TIMEOUT,
                )
                return "idle_timeout"

            while self._inbox:
                msg = self._inbox.pop(0)
                self._history.append({
                    "role": "human", "content": msg.text, "timestamp": msg.timestamp,
                })
                self._messages.append({"role": "user", "content": msg.text})

            turn = await run_agent_turn(
                messages=self._messages,
                system=system,
                tools=OPS_TOOLS,
                ctx=self._ctx,
                max_iterations=MAX_TOOL_TURNS_PER_MESSAGE,
            )

            if turn.final_text:
                self._history.append({
                    "role": "agent",
                    "content": turn.final_text,
                    "timestamp": str(workflow.now().timestamp()),
                })
                reply_result = await workflow.execute_activity(
                    post_thread_reply,
                    PostThreadReplyInput(
                        channel=input.channel,
                        thread_ts=input.thread_ts,
                        text=turn.final_text,
                    ),
                    start_to_close_timeout=SLACK_TIMEOUT,
                )
                if reply_result.is_error:
                    workflow.logger.warning(
                        "post_thread_reply failed: %s — agent reply not delivered to Slack",
                        reply_result.error_message,
                    )

        return "closed"
