"""Saga compensation activities — executed in reverse order when a repair is cancelled.

Each compensation is paired with its forward OMS step (see order_workflow.COMPENSATIONS).
They're idempotent enough for the demo: retries replay the same text, and real transient
errors (SMTP/network) will retry under the default activity retry policy.
"""
import asyncio
import random

import httpx
from temporalio import activity

from shared.catalog import get_book_by_id
from shared.models import CompensationInput
from worker.config import API_BASE_URL


@activity.defn
async def refund_payment(input: CompensationInput) -> str:
    """Reverse a successful process_payment step — refund to Gringotts."""
    await asyncio.sleep(random.uniform(0.4, 1.0))
    book = get_book_by_id(input.order_input.book_id)
    amount = (book.price_galleons if book else 0.0) * input.order_input.quantity
    activity.logger.info(
        "Refund issued for order %s: %.1f galleons returned to Gringotts vault",
        input.order_id, amount,
    )
    return (
        f"Refund issued for Order {input.order_id}: {amount:.1f}G returned via Gringotts."
        " Goblin teller has acknowledged receipt."
    )


@activity.defn
async def release_inventory_reservation(input: CompensationInput) -> str:
    """Reverse a successful pick_and_pack step — release the stock reservation
    placed at order-placement time. The API owns canonical inventory state;
    this activity calls back to /api/inventory/release. The endpoint is
    idempotent on order_id — a release without a matching prior reserve is
    a silent no-op, so it's safe to compensate even if the order was placed
    before the reserve-on-placement change shipped.
    """
    await asyncio.sleep(random.uniform(0.3, 0.7))
    book = get_book_by_id(input.order_input.book_id)
    title = book.title if book else input.order_input.book_title

    note = ""
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            response = await http.post(
                f"{API_BASE_URL}/api/inventory/release",
                json={"order_id": input.order_id},
            )
            response.raise_for_status()
            note = response.json().get("note", "")
    except Exception as error:
        activity.logger.warning(
            "release_inventory_reservation: API release failed for order %s: %s",
            input.order_id, error,
        )

    activity.logger.info(
        "Inventory reservation released for order %s: %dx '%s' (api note: %s)",
        input.order_id, input.order_input.quantity, title, note or "ok",
    )
    return (
        f"Inventory released for Order {input.order_id}: {input.order_input.quantity}x "
        f"'{title}' returned to shelf stock."
    )


@activity.defn
async def recall_delivery(input: CompensationInput) -> str:
    """Reverse a successful dispatch_delivery step — recall the owl / close the Floo route."""
    await asyncio.sleep(random.uniform(0.4, 0.8))
    method = input.order_input.delivery_method
    if method == "owl_post":
        detail = "Recall owl dispatched; original delivery owl returning to Eeylops Owl Emporium."
    elif method == "floo_network":
        detail = "Floo Regulation Panel notified; destination hearth will reject arrival."
    else:
        detail = "Portkey deactivated at destination coordinates."
    activity.logger.info("Delivery recalled for order %s via %s", input.order_id, method)
    return f"Delivery recalled for Order {input.order_id}: {detail}"
