"""Worker entry point — registers all workflows, activities, and search attributes."""
import asyncio
import logging

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker
from temporalio.api.operatorservice.v1 import AddSearchAttributesRequest
from temporalio.api.enums.v1 import IndexedValueType

from worker.config import TEMPORAL_HOST, TEMPORAL_NAMESPACE, TASK_QUEUE
from worker.workflows.order_workflow import OrderWorkflow
from worker.workflows.order_repair_workflow import OrderRepairWorkflow
from worker.workflows.customer_confirmation_workflow import CustomerConfirmationWorkflow
from worker.workflows.slack_conversation_workflow import SlackConversationWorkflow
from worker.activities.order_activities import (
    process_payment,
    verify_credentials,
    pick_and_pack,
    dispatch_delivery,
)
from worker.activities.compensation_activities import (
    refund_payment,
    release_inventory_reservation,
    recall_delivery,
)
from worker.activities.claude_activities import call_claude
from shared.agent_harness import dispatch_tool_activity
from worker.activities.slack_activities import (
    post_initial_slack_message,
    post_slack_reply,
    process_conversation_message,
)
from worker.activities.email_activities import send_customer_confirmation_email
from worker.workflows.ops_agent_conversation_workflow import OpsAgentConversationWorkflow
from worker.activities.ops_activities import (
    post_confirmation_card,
    post_order_picker,
    collapse_buttons,
    post_thread_reply,
    post_thread_closed_notice,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
log = logging.getLogger(__name__)

SEARCH_ATTRIBUTES = {
    "OrderId": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
    "CustomerName": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
    "BookTitle": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
    "OrderStatus": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
    "FailureType": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
    "RepairOutcome": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
    "RequiresHITL": IndexedValueType.INDEXED_VALUE_TYPE_BOOL,
    "RepairAttempts": IndexedValueType.INDEXED_VALUE_TYPE_INT,
}


async def register_search_attributes(client: Client) -> None:
    try:
        await client.operator_service.add_search_attributes(
            AddSearchAttributesRequest(
                search_attributes=SEARCH_ATTRIBUTES,
                namespace=TEMPORAL_NAMESPACE,
            )
        )
        log.info("Custom search attributes registered")
    except Exception as error:
        # Usually means they already exist — safe to ignore
        log.info(f"Search attribute registration (may already exist): {error}")


async def main() -> None:
    log.info(f"Connecting to Temporal at {TEMPORAL_HOST}")
    client = await Client.connect(
        TEMPORAL_HOST,
        namespace=TEMPORAL_NAMESPACE,
        data_converter=pydantic_data_converter,
    )

    await register_search_attributes(client)

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[
            OrderWorkflow,
            OrderRepairWorkflow,
            CustomerConfirmationWorkflow,
            SlackConversationWorkflow,
            OpsAgentConversationWorkflow,
        ],
        activities=[
            process_payment,
            verify_credentials,
            pick_and_pack,
            dispatch_delivery,
            refund_payment,
            release_inventory_reservation,
            recall_delivery,
            call_claude,
            dispatch_tool_activity,
            post_initial_slack_message,
            post_slack_reply,
            process_conversation_message,
            send_customer_confirmation_email,
            post_confirmation_card,
            post_order_picker,
            collapse_buttons,
            post_thread_reply,
            post_thread_closed_notice,
        ],
    )

    log.info(f"Worker started on task queue '{TASK_QUEUE}'")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
