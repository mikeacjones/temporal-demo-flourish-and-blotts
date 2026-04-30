"""OMS step activities — simulated order processing with Harry Potter-themed failures."""
import asyncio
import random

from temporalio import activity
from temporalio.exceptions import ApplicationError

from shared.models import OrderInput, OrderStepFailure, FailureType
from shared.catalog import get_book_by_id

# Failure type → step mapping (used to determine which activity raises forced failures)
PAYMENT_FAILURES = {FailureType.PAYMENT_TIMEOUT, FailureType.GRINGOTTS_FAILURE}
CREDENTIAL_FAILURES = {FailureType.MINISTRY_APPROVAL_REQUIRED, FailureType.RESTRICTED_SECTION}
PACK_FAILURES = {FailureType.MONSTER_BOOK_ESCAPE, FailureType.INVENTORY_MISMATCH, FailureType.WAREHOUSE_FAILURE}
DISPATCH_FAILURES = {FailureType.OWL_INTERCEPTED, FailureType.FLOO_MISDIRECTED}


def _raise_failure(step: str, failure_type: str, description: str, context: dict = {}):
    raise ApplicationError(
        description,
        {"step": step, "failure_type": failure_type, "description": description, "context": context},
        type="OrderFailure",
        non_retryable=True,
    )


def _raise_transient(description: str, context: dict = {}):
    """Raise a retryable error so Temporal's automatic activity retry kicks in.

    Used for randomly-sampled transient infrastructure failures (e.g. flaky
    Gringotts connections) that are great visual demo events but should not
    require human intervention — by the time the activity retries, the next
    random sample almost certainly succeeds, and the agent never sees the
    failure. Forced failures (set on order.forced_failure) keep using
    _raise_failure so the demo path still triggers the repair agent."""
    raise ApplicationError(
        description,
        {"transient": True, "context": context},
        type="OrderTransientError",
        # Default non_retryable=False — Temporal will retry per the
        # activity's retry policy (default: exponential backoff, bounded by
        # the activity's schedule_to_close_timeout).
    )


@activity.defn
async def process_payment(order: OrderInput) -> str:
    await asyncio.sleep(random.uniform(0.5, 1.5))

    forced = order.forced_failure
    book = get_book_by_id(order.book_id)

    if forced in {FailureType.PAYMENT_TIMEOUT, "payment_timeout"}:
        _raise_failure(
            "process_payment",
            FailureType.PAYMENT_TIMEOUT,
            f"Payment timed out for Order {order.order_id}. Gringotts bank connection unresponsive after 3 attempts.",
            {"amount_galleons": book.price_galleons * order.quantity if book else 0},
        )

    if forced in {FailureType.GRINGOTTS_FAILURE, "gringotts_failure"}:
        _raise_failure(
            "process_payment",
            FailureType.GRINGOTTS_FAILURE,
            f"Gringotts vault security lockdown triggered for Order {order.order_id}. "
            "Large order amount flagged suspicious. Goblin authentication required.",
            {"vault_error": "SECURITY_LOCKDOWN", "amount_galleons": book.price_galleons * order.quantity if book else 0},
        )

    # Natural (non-forced) failure probability — transient, Temporal retries
    # automatically. The random sample on the next attempt almost certainly
    # succeeds; the failure is visible in workflow history but never reaches
    # the repair agent.
    if not forced:
        if order.delivery_method == "portkey_express" and random.random() < 0.12:
            _raise_transient(
                f"Gringotts vault flagged Portkey Express premium charge for Order {order.order_id} — auto-retrying.",
                {"vault_error": "PREMIUM_REVIEW_REQUIRED"},
            )
        elif random.random() < 0.08:
            _raise_transient(
                f"Gringotts bank connection timed out processing payment for Order {order.order_id} — auto-retrying.",
            )

    return f"Payment of {book.price_galleons * order.quantity:.1f}G processed via Gringotts"


@activity.defn
async def verify_credentials(order: OrderInput) -> str:
    await asyncio.sleep(random.uniform(0.3, 1.0))

    forced = order.forced_failure
    book = get_book_by_id(order.book_id)

    if forced in {FailureType.MINISTRY_APPROVAL_REQUIRED, "ministry_approval_required"} or (
        book and book.requires_ministry_approval
    ):
        _raise_failure(
            "verify_credentials",
            FailureType.MINISTRY_APPROVAL_REQUIRED,
            f"Order {order.order_id} for '{order.book_title}' requires Ministry of Magic approval. "
            "This item is classified as restricted magical literature. "
            "Customer must obtain an MoM Form 27B/6 (Restricted Publications Permit) before dispatch.",
            {"book_category": book.category if book else "restricted", "requires_ministry": True},
        )

    if forced in {FailureType.RESTRICTED_SECTION, "restricted_section"}:
        _raise_failure(
            "verify_credentials",
            FailureType.RESTRICTED_SECTION,
            f"Order {order.order_id} for '{order.book_title}' requires Restricted Section access credentials. "
            "No signed permission slip from a qualified instructor found in customer record. "
            "N.E.W.T.-level qualifications or Headmaster's signature required.",
            {"credential_type": "instructor_permission"},
        )

    if book and book.age_restriction and random.random() < 0.1:
        _raise_failure(
            "verify_credentials",
            FailureType.RESTRICTED_SECTION,
            f"Age verification failed for Order {order.order_id}. "
            f"'{order.book_title}' requires proof of age ({book.age_restriction}+). Customer record incomplete.",
        )

    return f"Credentials verified for {order.customer_name}"


@activity.defn
async def pick_and_pack(order: OrderInput) -> str:
    await asyncio.sleep(random.uniform(0.8, 2.0))

    forced = order.forced_failure
    book = get_book_by_id(order.book_id)

    if forced in {FailureType.MONSTER_BOOK_ESCAPE, "monster_book_escape"} or (
        book and book.id == "mnbm-001" and random.random() < 0.75
    ):
        _raise_failure(
            "pick_and_pack",
            FailureType.MONSTER_BOOK_ESCAPE,
            f"SAFETY INCIDENT — Order {order.order_id}: The Monster Book of Monsters has escaped its packaging "
            "and is terrorising shelf section C-7. Two warehouse elves have retreated to break room. "
            "Standard containment protocols failed. Item requires senior staff intervention with knobbly walking stick.",
            {"shelf_section": "C-7", "injuries": "minor bites to two house elves", "books_destroyed": 2},
        )

    if forced in {FailureType.INVENTORY_MISMATCH, "inventory_mismatch"}:
        _raise_failure(
            "pick_and_pack",
            FailureType.INVENTORY_MISMATCH,
            f"Inventory mismatch for Order {order.order_id}: '{order.book_title}' shows {order.quantity} in stock "
            "on the OMS but physical shelf count found 0. System inventory requires reconciliation.",
            {"oms_count": order.quantity, "physical_count": 0},
        )

    if forced in {FailureType.WAREHOUSE_FAILURE, "warehouse_failure"}:
        _raise_failure(
            "pick_and_pack",
            FailureType.WAREHOUSE_FAILURE,
            f"Warehouse pick failure for Order {order.order_id}: Pick station automation charm malfunctioned. "
            "Manual intervention required to locate and package item.",
        )

    # Natural failures.
    if not forced and book:
        # Physical-shelf reality vs the OMS belief. When they diverge, this is
        # the inventory_mismatch scenario — the customer was allowed to order
        # because the OMS said there was stock, but the shelf is actually empty.
        physical = book.physical_count
        if physical < order.quantity:
            _raise_failure(
                "pick_and_pack",
                FailureType.INVENTORY_MISMATCH,
                f"Inventory mismatch for Order {order.order_id}: OMS shows "
                f"{book.in_stock} copies of '{order.book_title}' in stock, but the "
                f"physical shelf count found only {physical}. System inventory "
                "requires reconciliation.",
                {
                    "oms_count": book.in_stock,
                    "physical_count": physical,
                    "requested": order.quantity,
                },
            )
        elif random.random() < 0.12:
            _raise_failure(
                "pick_and_pack",
                FailureType.WAREHOUSE_FAILURE,
                f"Pick automation charm failure for Order {order.order_id}. Station C-{random.randint(1, 12)} offline.",
            )

    return f"Packaged {order.quantity}x '{order.book_title}' for {order.customer_name}"


@activity.defn
async def dispatch_delivery(order: OrderInput) -> str:
    await asyncio.sleep(random.uniform(0.5, 1.5))

    forced = order.forced_failure

    if order.delivery_method == "owl_post" or forced in {FailureType.OWL_INTERCEPTED, "owl_intercepted"}:
        if forced in {FailureType.OWL_INTERCEPTED, "owl_intercepted"} or random.random() < 0.18:
            _raise_failure(
                "dispatch_delivery",
                FailureType.OWL_INTERCEPTED,
                f"Owl Post failure for Order {order.order_id}: Delivery owl 'Archimedes' intercepted en route. "
                "Believed detained by a Niffler attracted to the Gringotts receipt in the parcel. "
                "Owl currently recovering at Eeylops Owl Emporium. Package status unknown.",
                {"owl_name": "Archimedes", "last_location": "Knockturn Alley junction", "delivery_method": "owl_post"},
            )

    if order.delivery_method == "floo_network" or forced in {FailureType.FLOO_MISDIRECTED, "floo_misdirected"}:
        if forced in {FailureType.FLOO_MISDIRECTED, "floo_misdirected"} or random.random() < 0.22:
            wrong_addresses = [
                "The Leaky Cauldron, London (package slightly singed)",
                "Borgin and Burkes, Knockturn Alley (package returned with suspicious residue)",
                "The Hog's Head Inn, Hogsmeade (landlord Aberforth claims no knowledge)",
                "Ministry of Magic Atrium (Aurors now investigating)",
            ]
            wrong = random.choice(wrong_addresses)
            _raise_failure(
                "dispatch_delivery",
                FailureType.FLOO_MISDIRECTED,
                f"Floo Network misdirection for Order {order.order_id}: Package delivered to '{wrong}' "
                f"instead of '{order.delivery_address}'. Mispronunciation suspected.",
                {"intended": order.delivery_address, "actual": wrong, "delivery_method": "floo_network"},
            )

    return f"Dispatched via {order.delivery_method.replace('_', ' ').title()} to {order.delivery_address}"
