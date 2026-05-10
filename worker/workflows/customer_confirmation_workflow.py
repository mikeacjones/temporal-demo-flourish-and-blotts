"""CustomerConfirmationWorkflow — customer-facing HITL.

Sends an Approve/Deny email to the customer (captured by MailHog in the demo) and
simultaneously exposes the pending decision via a query so the /orders/:id page can
surface it. Whichever channel the customer responds through first wins.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from shared.models import (
        CustomerConfirmationInput,
        CustomerConfirmationResult,
        CustomerDecisionSignal,
        PendingCustomerDecision,
        SendConfirmationEmailInput,
    )
    from shared.hitl_tokens import make_token
    from worker.config import API_PUBLIC_URL, HITL_TOKEN_SECRET
    from worker.activities.email_activities import send_customer_confirmation_email

EMAIL_TIMEOUT = timedelta(seconds=30)
DECISION_TIMEOUT = timedelta(hours=24)
REMINDER_AFTER = timedelta(hours=4)


@workflow.defn
class CustomerConfirmationWorkflow:
    def __init__(self) -> None:
        self._decision: CustomerDecisionSignal | None = None
        self._input: CustomerConfirmationInput | None = None

    @workflow.signal
    def receive_customer_decision(self, signal: CustomerDecisionSignal) -> None:
        # Signal handlers only mutate state; the main run loop reacts.
        if self._decision is None:
            self._decision = signal

    @workflow.query
    def get_pending_decision(self) -> PendingCustomerDecision | None:
        if self._decision is not None or self._input is None:
            return None
        return PendingCustomerDecision(
            order_id=self._input.order_id,
            question=self._input.question,
            description=self._input.description,
            proposed_action=self._input.proposed_action,
            options=self._input.options,
        )

    @workflow.run
    async def run(self, input: CustomerConfirmationInput) -> CustomerConfirmationResult:
        self._input = input
        workflow.upsert_search_attributes({
            "OrderId": [input.order_id],
            "OrderStatus": ["awaiting_customer"],
            "RequiresHITL": [True],
        })
        workflow.set_current_details(
            f"Awaiting customer decision on `{input.order_id}` — "
            f"{input.question} (24h timeout)"
        )

        # Mint signed Approve/Deny links. The workflow mints these rather than the
        # activity because they're deterministic for a given workflow (order_id +
        # decision + secret). The activity only sends the email.
        approve_token = make_token(input.order_id, "approve", HITL_TOKEN_SECRET)
        deny_token = make_token(input.order_id, "deny", HITL_TOKEN_SECRET)
        approve_url = f"{API_PUBLIC_URL}/hitl/{input.order_id}/decision?result=approve&token={approve_token}"
        deny_url = f"{API_PUBLIC_URL}/hitl/{input.order_id}/decision?result=deny&token={deny_token}"

        expires_at = workflow.now() + DECISION_TIMEOUT

        await workflow.execute_activity(
            send_customer_confirmation_email,
            SendConfirmationEmailInput(
                order_id=input.order_id,
                to_email=input.order_input.customer_email,
                customer_name=input.order_input.customer_name,
                question=input.question,
                description=input.description,
                approve_url=approve_url,
                deny_url=deny_url,
                expires_at_iso=expires_at.isoformat(),
            ),
            start_to_close_timeout=EMAIL_TIMEOUT,
            summary=f"Email decision request to {input.order_input.customer_email}",
        )

        # Wait for a decision, with a reminder resend at 4h if no response yet.
        # workflow.wait_condition returns None on success (immediately if the predicate
        # is already true) and raises asyncio.TimeoutError on timeout — we inspect
        # self._decision directly rather than trusting the return value.
        try:
            await workflow.wait_condition(
                lambda: self._decision is not None, timeout=REMINDER_AFTER,
            )
        except asyncio.TimeoutError:
            pass

        if self._decision is None:
            # 4h passed with no response — send a reminder, then wait the rest.
            # Same signed URLs; the email is idempotent from the customer's view.
            workflow.set_current_details(
                f"Customer has not responded after 4h — sent reminder to "
                f"{input.order_input.customer_email}. Waiting up to 20h more."
            )
            await workflow.execute_activity(
                send_customer_confirmation_email,
                SendConfirmationEmailInput(
                    order_id=input.order_id,
                    to_email=input.order_input.customer_email,
                    customer_name=input.order_input.customer_name,
                    question=f"Reminder: {input.question}",
                    description=input.description,
                    approve_url=approve_url,
                    deny_url=deny_url,
                    expires_at_iso=expires_at.isoformat(),
                ),
                start_to_close_timeout=EMAIL_TIMEOUT,
                summary=f"Resend reminder to {input.order_input.customer_email}",
            )
            try:
                await workflow.wait_condition(
                    lambda: self._decision is not None,
                    timeout=DECISION_TIMEOUT - REMINDER_AFTER,
                )
            except asyncio.TimeoutError:
                pass

        if self._decision is None:
            workflow.upsert_search_attributes({"OrderStatus": ["cancelled_unresolved"]})
            return CustomerConfirmationResult(
                status="timeout",
                note="Customer did not respond within 24 hours.",
            )

        decision = self._decision
        return CustomerConfirmationResult(
            status=decision.decision,  # "approved" | "denied"
            source=decision.source,
            note=decision.user_note,
        )
