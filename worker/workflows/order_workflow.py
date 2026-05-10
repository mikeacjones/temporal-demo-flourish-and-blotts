"""OrderWorkflow — the main order entity workflow."""
from __future__ import annotations

from datetime import timedelta
from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from shared.models import (
        OrderInput,
        OrderRepairInput,
        OrderRepairResult,
        OrderStepFailure,
        CompensationInput,
        FailureType,
        OrderStatus,
    )
    # Activity references come through pass-through. Activity modules pull in
    # heavy SDKs (anthropic, slack_sdk, aiosmtplib) whose transitive imports
    # (urllib, etc.) trip the workflow sandbox if loaded inside it.
    from worker.activities.order_activities import (
        process_payment,
        verify_credentials,
        pick_and_pack,
        dispatch_delivery,
    )

# Child workflow class — safe at module level since the child workflow module
# is itself sandbox-clean.
from worker.workflows.order_repair_workflow import OrderRepairWorkflow

# Default retry policy used for OMS steps — transient errors retry with backoff,
# but domain failures are raised as ApplicationError(non_retryable=True) inside
# the activity and so bypass retries and go straight to the repair workflow.
STEP_TIMEOUT = timedelta(seconds=30)
COMPENSATION_TIMEOUT = timedelta(seconds=60)

OMS_STEPS = [
    ("process_payment",      "payment_processing"),
    ("verify_credentials",   "verifying_credentials"),
    ("pick_and_pack",        "pick_and_pack"),
    ("dispatch_delivery",    "dispatching"),
]

# forward step → compensation activity name. Steps without a compensation (read-only)
# are intentionally absent.
COMPENSATIONS: dict[str, str] = {
    "process_payment": "refund_payment",
    "pick_and_pack": "release_inventory_reservation",
    "dispatch_delivery": "recall_delivery",
}

# Terminal status chosen based on which HITL path denied the repair.
CANCELLATION_STATUS: dict[str, OrderStatus] = {
    "customer_denied": OrderStatus.CANCELLED_BY_CUSTOMER,
    "ops_denied": OrderStatus.CANCELLED_BY_OPS,
    "hitl_denied": OrderStatus.CANCELLED_BY_OPS,  # legacy alias
    "unresolved": OrderStatus.CANCELLED_UNRESOLVED,
}


@workflow.defn
class OrderWorkflow:
    def __init__(self):
        self._status = OrderStatus.PENDING
        self._failure_type = FailureType.NONE
        self._repair_outcome: str | None = None
        self._requires_hitl = False
        self._repair_attempts = 0
        self._steps_completed: list[str] = []
        self._compensations_executed: list[str] = []
        # Compensations are tracked in the order they must be rolled back.
        # Each entry is (forward_step_name, compensation_activity_name).
        self._pending_compensations: list[tuple[str, str]] = []
        self._notes = ""

    @workflow.query
    def status(self) -> str:
        return self._status.value

    @workflow.query
    def executed_steps(self) -> list[str]:
        return list(self._steps_completed)

    @workflow.query
    def compensations_run(self) -> list[str]:
        return list(self._compensations_executed)

    async def _run_compensations(self, order: OrderInput) -> None:
        """Run compensations in reverse order of execution."""
        if not self._pending_compensations:
            return

        self._status = OrderStatus.COMPENSATING
        workflow.upsert_search_attributes({"OrderStatus": [OrderStatus.COMPENSATING.value]})
        workflow.set_current_details(
            f"Rolling back {len(self._pending_compensations)} step(s) for order "
            f"`{order.order_id}` after repair could not resolve the failure."
        )

        # Reverse so newer steps compensate first.
        for forward_step, compensation_name in reversed(self._pending_compensations):
            try:
                result = await workflow.execute_activity(
                    compensation_name,
                    CompensationInput(
                        order_id=order.order_id,
                        order_input=order,
                        forward_step=forward_step,
                    ),
                    start_to_close_timeout=COMPENSATION_TIMEOUT,
                    summary=f"Compensate `{forward_step}` for {order.order_id}",
                )
                self._compensations_executed.append(f"{compensation_name}: {result}")
            except ActivityError as error:
                # Deliberately let permanent compensation failure surface as workflow
                # failure — operators need to see "refund stuck" loudly, not silently.
                self._compensations_executed.append(f"{compensation_name}: FAILED — {error}")
                raise

        self._pending_compensations.clear()

    @workflow.run
    async def run(self, order: OrderInput) -> dict:
        activity_fns = {
            "process_payment": process_payment,
            "verify_credentials": verify_credentials,
            "pick_and_pack": pick_and_pack,
            "dispatch_delivery": dispatch_delivery,
        }

        # Single initial upsert covers every stable-at-start facet plus the
        # zero-value mutables. Per-step OrderStatus is upserted inside the loop
        # below so visibility can show "where this order is right now".
        workflow.upsert_search_attributes({
            "OrderId": [order.order_id],
            "CustomerName": [order.customer_name],
            "BookTitle": [order.book_title],
            "OrderStatus": [OrderStatus.PROCESSING.value],
            "FailureType": [FailureType.NONE.value],
            "RequiresHITL": [False],
            "RepairAttempts": [0],
            "DeliveryMethod": [order.delivery_method],
        })
        workflow.set_current_details(
            f"Order `{order.order_id}` for {order.customer_name} — "
            f"*{order.book_title}* ×{order.quantity} via "
            f"`{order.delivery_method}`."
        )

        self._status = OrderStatus.PROCESSING
        cancel_status: OrderStatus | None = None

        try:
            for activity_name, status_value in OMS_STEPS:
                self._status = OrderStatus(status_value)
                workflow.upsert_search_attributes({"OrderStatus": [status_value]})

                try:
                    result = await workflow.execute_activity(
                        activity_fns[activity_name],
                        order,
                        start_to_close_timeout=STEP_TIMEOUT,
                        schedule_to_close_timeout=timedelta(minutes=5),
                        summary=f"OMS step `{activity_name}` for {order.order_id}",
                    )
                    self._steps_completed.append(f"{activity_name}: {result}")
                    # Record the compensation for this step so we can roll back later.
                    if activity_name in COMPENSATIONS:
                        self._pending_compensations.append(
                            (activity_name, COMPENSATIONS[activity_name])
                        )

                except ActivityError as error:
                    cause = error.cause
                    if isinstance(cause, ApplicationError) and cause.type == "OrderFailure":
                        # Activity passes structured data as the first "detail" of the
                        # ApplicationError; cause.args[0] is the human-readable message.
                        details = list(cause.details) if cause.details else []
                        failure_data = details[0] if details and isinstance(details[0], dict) else {}
                        failure_type = failure_data.get("failure_type", "unknown")
                        description = failure_data.get("description", str(cause))
                        context = failure_data.get("context", {})

                        self._failure_type = (
                            FailureType(failure_type)
                            if failure_type in FailureType._value2member_map_
                            else FailureType.NONE
                        )
                        self._status = OrderStatus.REPAIR_IN_PROGRESS
                        self._repair_attempts += 1

                        # Single upsert covers status + failure type + new
                        # attempt count — all known at this point.
                        workflow.upsert_search_attributes({
                            "OrderStatus": [OrderStatus.REPAIR_IN_PROGRESS.value],
                            "FailureType": [failure_type],
                            "RepairAttempts": [self._repair_attempts],
                        })
                        workflow.set_current_details(
                            f"Order `{order.order_id}` failed at `{activity_name}` "
                            f"with `{failure_type}` — repair agent is working "
                            f"(attempt {self._repair_attempts})."
                        )

                        repair_input = OrderRepairInput(
                            order_id=order.order_id,
                            order_input=order,
                            failure=OrderStepFailure(
                                step=activity_name,
                                failure_type=failure_type,
                                description=description,
                                context=context,
                            ),
                        )

                        repair_result: OrderRepairResult = await workflow.execute_child_workflow(
                            OrderRepairWorkflow,
                            repair_input,
                            id=f"repair-{order.order_id}",
                            task_queue="flourish-blotts-oms",
                            execution_timeout=timedelta(hours=25),
                            static_summary=(
                                f"Repair `{order.order_id}` — "
                                f"`{failure_type}` at `{activity_name}` "
                                f"(attempt {self._repair_attempts})"
                            ),
                            static_details=(
                                f"**Order:** `{order.order_id}` — "
                                f"{order.customer_name}\n"
                                f"**Book:** {order.book_title} (`{order.book_id}`) "
                                f"×{order.quantity}\n"
                                f"**Failed step:** `{activity_name}`\n"
                                f"**Failure type:** `{failure_type}`\n"
                                f"**Description:** {description}"
                            ),
                        )

                        self._repair_outcome = repair_result.outcome
                        self._requires_hitl = repair_result.requires_hitl

                        workflow.upsert_search_attributes({
                            "RepairOutcome": [repair_result.outcome],
                            "RequiresHITL": [repair_result.requires_hitl],
                        })

                        if repair_result.status == "cancelled":
                            self._notes = repair_result.notes
                            cancel_status = CANCELLATION_STATUS.get(
                                repair_result.outcome, OrderStatus.CANCELLED,
                            )
                            break

                        # If the repair workflow staged a customer-approved book
                        # substitution, swap it into our order reference now so
                        # this step (and any subsequent steps) act on the new book.
                        if repair_result.updated_order is not None:
                            order = repair_result.updated_order
                            self._steps_completed.append(
                                f"order updated during repair: book substituted to "
                                f"'{order.book_title}' (id {order.book_id})"
                            )
                            workflow.upsert_search_attributes({
                                "BookTitle": [order.book_title],
                            })

                        # Resolved — continue from this step (retry the step once after repair).
                        try:
                            result = await workflow.execute_activity(
                                activity_fns[activity_name],
                                order,
                                start_to_close_timeout=STEP_TIMEOUT,
                                summary=f"Retry `{activity_name}` after repair for {order.order_id}",
                            )
                            self._steps_completed.append(f"{activity_name} (after repair): {result}")
                            if activity_name in COMPENSATIONS:
                                self._pending_compensations.append(
                                    (activity_name, COMPENSATIONS[activity_name])
                                )
                        except ActivityError:
                            # Post-repair failure: don't loop forever in the demo.
                            self._steps_completed.append(f"{activity_name} (post-repair): proceeded")
                    else:
                        raise

            if cancel_status is not None:
                await self._run_compensations(order)
                self._status = cancel_status
                workflow.upsert_search_attributes({"OrderStatus": [cancel_status.value]})
                workflow.set_current_details(
                    f"Order `{order.order_id}` ended `{cancel_status.value}` — "
                    f"{len(self._compensations_executed)} compensation(s) run. "
                    f"{self._notes or ''}"
                )
            else:
                self._status = OrderStatus.COMPLETED
                workflow.upsert_search_attributes({"OrderStatus": [OrderStatus.COMPLETED.value]})
                workflow.set_current_details(
                    f"Order `{order.order_id}` completed — "
                    f"*{order.book_title}* ×{order.quantity} dispatched via "
                    f"`{order.delivery_method}`."
                )

        except BaseException:
            # Workflow was cancelled (or some other unexpected failure) mid-flight.
            # Roll back whatever forward work succeeded, then re-raise so the failure
            # surfaces faithfully in history.
            await self._run_compensations(order)
            workflow.upsert_search_attributes({
                "OrderStatus": [OrderStatus.CANCELLED.value],
            })
            raise

        return {
            "order_id": order.order_id,
            "status": self._status.value,
            "failure_type": self._failure_type.value,
            "repair_outcome": self._repair_outcome,
            "requires_hitl": self._requires_hitl,
            "steps_completed": self._steps_completed,
            "compensations_executed": self._compensations_executed,
            "notes": self._notes,
        }
