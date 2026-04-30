"""FastAPI server — REST API for the Flourish & Blotts OMS UI."""
import asyncio
import json
import random
import uuid
from datetime import timedelta
from typing import AsyncGenerator, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.common import RetryPolicy
from temporalio.contrib.pydantic import pydantic_data_converter

from shared.models import (
    OrderInput,
    SlackActionSignal,
    CustomerDecisionSignal,
)
from shared.catalog import CATALOG, get_book_by_id
from shared.hitl_tokens import verify_token
from worker.config import (
    TEMPORAL_HOST,
    TEMPORAL_NAMESPACE,
    TEMPORAL_UI_URL,
    TASK_QUEUE,
    HITL_TOKEN_SECRET,
)
from worker.workflows.order_workflow import OrderWorkflow
from worker.workflows.slack_conversation_workflow import SlackConversationWorkflow

# Customer HITL email links expire after 24h (matches the workflow's HITL timeout).
HITL_TOKEN_MAX_AGE_SECONDS = 24 * 60 * 60

app = FastAPI(title="Flourish & Blotts OMS API")


def _decode_sa(wf, key: str, default: Any = None) -> Any:
    """Read a search-attribute value from a Temporal workflow execution.

    As of temporalio 1.26 both `list_workflows()` yields and `describe()` results
    expose `search_attributes` as a plain dict[str, list[value]] — list-valued
    for Keyword, Int, Bool, etc. This peels the list to return a scalar.
    """
    try:
        sa = getattr(wf, 'search_attributes', None) or {}
        value = sa.get(key)
        if value is None:
            return default
        if isinstance(value, list):
            return value[0] if value else default
        return value
    except Exception:
        return default


def _wf_to_order(wf) -> dict:
    workflow_id = wf.id
    order_id = workflow_id.removeprefix("order-")
    return {
        "workflow_id": workflow_id,
        "order_id": order_id,
        "customer_name": _decode_sa(wf, "CustomerName", "Unknown"),
        "book_title": _decode_sa(wf, "BookTitle", "Unknown"),
        "order_status": _decode_sa(wf, "OrderStatus", "processing"),
        "failure_type": _decode_sa(wf, "FailureType", "none"),
        "repair_outcome": _decode_sa(wf, "RepairOutcome"),
        "requires_hitl": _decode_sa(wf, "RequiresHITL", False),
        "repair_attempts": _decode_sa(wf, "RepairAttempts", 0),
        "started_at": wf.start_time.isoformat() if wf.start_time else None,
        "close_time": wf.close_time.isoformat() if wf.close_time else None,
        "execution_status": wf.status.name if wf.status else "RUNNING",
        "temporal_url": f"{TEMPORAL_UI_URL}/namespaces/default/workflows/{workflow_id}",
    }

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_client: Client | None = None


async def get_client() -> Client:
    global _client
    if _client is None:
        _client = await Client.connect(
            TEMPORAL_HOST,
            namespace=TEMPORAL_NAMESPACE,
            data_converter=pydantic_data_converter,
        )
    return _client


# ---------------------------------------------------------------------------
# Request/response schemas
# ---------------------------------------------------------------------------

class PlaceOrderRequest(BaseModel):
    customer_name: str
    customer_email: str
    book_id: str
    quantity: int = 1
    delivery_method: str = "owl_post"
    delivery_address: str
    forced_failure: str | None = None


class BulkOrderRequest(BaseModel):
    count: int = 100


class ApproveOrderRequest(BaseModel):
    user_name: str = "Ops Dashboard"


# ---------------------------------------------------------------------------
# Bulk order helpers
# ---------------------------------------------------------------------------

HP_CUSTOMERS = [
    ("Harry Potter", "harry@hogwarts.wiz"),
    ("Ron Weasley", "ron@the-burrow.wiz"),
    ("Hermione Granger", "hermione@hogwarts.wiz"),
    ("Draco Malfoy", "draco@malfoy-manor.wiz"),
    ("Luna Lovegood", "luna@the-quibbler.wiz"),
    ("Neville Longbottom", "neville@hogwarts.wiz"),
    ("Ginny Weasley", "ginny@the-burrow.wiz"),
    ("Fred Weasley", "fred@weasleys-wizard-wheezes.wiz"),
    ("George Weasley", "george@weasleys-wizard-wheezes.wiz"),
    ("Albus Dumbledore", "headmaster@hogwarts.wiz"),
    ("Minerva McGonagall", "mcgonagall@hogwarts.wiz"),
    ("Severus Snape", "snape@hogwarts.wiz"),
    ("Rubeus Hagrid", "hagrid@hogwarts.wiz"),
    ("Sirius Black", "sirius@12-grimmauld-place.wiz"),
    ("Remus Lupin", "lupin@hogwarts.wiz"),
    ("Arthur Weasley", "arthur@ministry.wiz"),
    ("Molly Weasley", "molly@the-burrow.wiz"),
    ("Cedric Diggory", "cedric@hogwarts.wiz"),
    ("Cho Chang", "cho@hogwarts.wiz"),
    ("Viktor Krum", "viktor@durmstrang.wiz"),
    ("Fleur Delacour", "fleur@beauxbatons.wiz"),
    ("Nymphadora Tonks", "tonks@ministry.wiz"),
    ("Bill Weasley", "bill@shell-cottage.wiz"),
    ("Percy Weasley", "percy@ministry.wiz"),
    ("Charlie Weasley", "charlie@romania-dragon-sanctuary.wiz"),
]

HP_ADDRESSES = [
    "4 Privet Drive, Little Whinging, Surrey",
    "The Burrow, Ottery St Catchpole, Devon",
    "12 Grimmauld Place, London",
    "Hogwarts School of Witchcraft and Wizardry, Scottish Highlands",
    "Malfoy Manor, Wiltshire",
    "Shell Cottage, Cornwall",
    "Godric's Hollow, West Country",
    "10 Downing Street (via Ministerial Floo)",
    "St Mungo's Hospital, London",
    "Hogsmeade Village, Scottish Highlands",
]

# (book_id, forced_failure, weight)
BULK_DISTRIBUTION = [
    ("hom-001",    None,                           20),
    ("qta-001",    None,                           10),
    ("bosl-001",   None,                            5),
    ("fbwtft-001", None,                            5),
    ("bs-001",     None,                            5),
    ("mnbm-001",   "monster_book_escape",          15),
    ("fbwtft-001", "floo_misdirected",             10),
    ("tdda-001",   "owl_intercepted",               8),
    ("bs-001",     "inventory_mismatch",            7),
    ("mpp-001",    "ministry_approval_required",    8),
    ("drk-001",    "restricted_section",            5),
    ("qta-001",    "gringotts_failure",             2),
]

_weights = [w for _, _, w in BULK_DISTRIBUTION]
_choices = [(b, f) for b, f, _ in BULK_DISTRIBUTION]

DELIVERY_METHODS = ["owl_post", "floo_network", "portkey_express"]
DELIVERY_WEIGHTS = [0.5, 0.35, 0.15]


def _pick_weighted(population, weights):
    total = sum(weights)
    r = random.uniform(0, total)
    cum = 0
    for item, w in zip(population, weights):
        cum += w
        if r <= cum:
            return item
    return population[-1]


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    await get_client()


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

@app.get("/api/catalog")
async def get_catalog():
    return [
        {
            "id": b.id,
            "title": b.title,
            "author": b.author,
            "price_galleons": b.price_galleons,
            "description": b.description,
            "category": b.category,
            "in_stock": b.in_stock,
            "physical_in_stock": b.physical_in_stock,
            "requires_ministry_approval": b.requires_ministry_approval,
            "cover_color": b.cover_color,
        }
        for b in CATALOG
    ]


# ---------------------------------------------------------------------------
# Inventory mutations (canonical store)
#
# The API process owns the authoritative `in_stock` count for each book.
# Order placement reserves stock here; workflow saga compensation releases
# via the worker calling /api/inventory/release; the ops-agent's
# adjust_inventory tool also routes through /api/inventory/adjust. This
# keeps the storefront UI (which reads /api/catalog) in sync with all
# inventory mutations regardless of which process triggered them.
#
# Demo-grade idempotency: in-memory dicts of applied keys. Lost on restart.
# ---------------------------------------------------------------------------

# order_id → (book_id, quantity) for orders that hold an active reservation.
# Idempotency: re-reserving the same order_id is a no-op; releasing an order_id
# without an active reservation is a silent no-op.
_active_reservations: dict[str, tuple[str, int]] = {}
# Idempotency keys (e.g. tool_use_ids) that have already been applied via adjust.
_applied_adjust_keys: dict[str, int] = {}


def _reserve_stock(book_id: str, quantity: int, order_id: str) -> tuple[bool, str]:
    """Atomically check + decrement in_stock. Idempotent on order_id.
    Returns (ok, error_message). On insufficient stock, no mutation."""
    if order_id in _active_reservations:
        return True, ""  # already reserved — idempotent
    book = get_book_by_id(book_id)
    if book is None:
        return False, f"book '{book_id}' not found"
    if book.in_stock < quantity:
        return False, (
            f"insufficient stock for '{book.title}': have {book.in_stock}, need {quantity}"
        )
    book.in_stock -= quantity
    _active_reservations[order_id] = (book_id, quantity)
    return True, ""


def _release_stock(order_id: str) -> tuple[bool, str]:
    """Increment in_stock by the prior reservation's quantity. Idempotent on
    order_id — calling release twice (or on an order with no active reservation)
    is a silent no-op rather than an error."""
    pair = _active_reservations.pop(order_id, None)
    if pair is None:
        return False, "no active reservation for this order_id"
    book_id, quantity = pair
    book = get_book_by_id(book_id)
    if book is not None:
        book.in_stock += quantity
    return True, ""


def _adjust_stock(book_id: str, delta: int, key: str) -> tuple[bool, str, int]:
    """Apply delta to in_stock. Idempotent on key — second call returns the
    cached prior count without re-applying. Returns (ok, message, new_count)."""
    if key in _applied_adjust_keys:
        return True, "(already applied — idempotent replay)", _applied_adjust_keys[key]
    book = get_book_by_id(book_id)
    if book is None:
        return False, f"book '{book_id}' not found", 0
    book.in_stock = max(0, book.in_stock + delta)
    _applied_adjust_keys[key] = book.in_stock
    return True, "", book.in_stock


class ReserveInventoryRequest(BaseModel):
    book_id: str
    quantity: int
    order_id: str  # idempotency key


@app.post("/api/inventory/reserve")
async def reserve_inventory(req: ReserveInventoryRequest):
    ok, msg = _reserve_stock(req.book_id, req.quantity, req.order_id)
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    book = get_book_by_id(req.book_id)
    return {"book_id": req.book_id, "in_stock": book.in_stock if book else 0}


class ReleaseInventoryRequest(BaseModel):
    order_id: str  # idempotency key


@app.post("/api/inventory/release")
async def release_inventory(req: ReleaseInventoryRequest):
    ok, msg = _release_stock(req.order_id)
    return {"released": ok, "note": msg}


class AdjustInventoryRequest(BaseModel):
    book_id: str
    delta: int
    reason: str = ""
    idempotency_key: str


@app.post("/api/inventory/adjust")
async def adjust_inventory_endpoint(req: AdjustInventoryRequest):
    ok, msg, new_count = _adjust_stock(req.book_id, req.delta, req.idempotency_key)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"book_id": req.book_id, "in_stock": new_count, "note": msg}


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

@app.post("/api/orders")
async def place_order(req: PlaceOrderRequest):
    book = get_book_by_id(req.book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    order_id = f"ord-{uuid.uuid4().hex[:8].upper()}"

    # Reserve stock before starting the workflow. The workflow's saga
    # compensation will call /api/inventory/release if the order ends up
    # cancelled (via release_inventory_reservation activity → API).
    ok, msg = _reserve_stock(req.book_id, req.quantity, order_id)
    if not ok:
        raise HTTPException(status_code=409, detail=f"Cannot place order: {msg}")

    order = OrderInput(
        order_id=order_id,
        customer_name=req.customer_name,
        customer_email=req.customer_email,
        book_id=req.book_id,
        book_title=book.title,
        quantity=req.quantity,
        delivery_method=req.delivery_method,
        delivery_address=req.delivery_address,
        forced_failure=req.forced_failure,
    )

    try:
        client = await get_client()
        await client.start_workflow(
            OrderWorkflow.run,
            order,
            id=f"order-{order_id}",
            task_queue=TASK_QUEUE,
            execution_timeout=timedelta(hours=26),
        )
    except Exception as e:
        # Workflow start failed — release the reservation we just made so
        # stock isn't permanently lost.
        _release_stock(order_id)
        raise HTTPException(status_code=500, detail=f"Failed to start order workflow: {e}")

    return {
        "order_id": order_id,
        "workflow_id": f"order-{order_id}",
        "temporal_url": f"{TEMPORAL_UI_URL}/namespaces/default/workflows/order-{order_id}",
    }


@app.post("/api/orders/bulk")
async def bulk_orders(req: BulkOrderRequest):
    if req.count < 1 or req.count > 500:
        raise HTTPException(status_code=400, detail="count must be 1–500")

    client = await get_client()
    started = []

    for _ in range(req.count):
        book_id, forced_failure = _pick_weighted(_choices, _weights)
        book = get_book_by_id(book_id)
        customer_name, customer_email = random.choice(HP_CUSTOMERS)
        delivery_method = random.choices(DELIVERY_METHODS, weights=DELIVERY_WEIGHTS)[0]
        delivery_address = random.choice(HP_ADDRESSES)

        # Match forced delivery failures to the right delivery method
        if forced_failure == "floo_misdirected":
            delivery_method = "floo_network"
        elif forced_failure == "owl_intercepted":
            delivery_method = "owl_post"

        order_id = f"ord-{uuid.uuid4().hex[:8].upper()}"

        # Reserve stock before starting; skip this order if insufficient stock.
        # Bulk orders are best-effort — one out-of-stock entry shouldn't fail
        # the whole batch.
        ok, _msg = _reserve_stock(book_id, 1, order_id)
        if not ok:
            continue

        order = OrderInput(
            order_id=order_id,
            customer_name=customer_name,
            customer_email=customer_email,
            book_id=book_id,
            book_title=book.title,
            quantity=1,
            delivery_method=delivery_method,
            delivery_address=delivery_address,
            forced_failure=forced_failure,
        )

        try:
            await client.start_workflow(
                OrderWorkflow.run,
                order,
                id=f"order-{order_id}",
                task_queue=TASK_QUEUE,
                execution_timeout=timedelta(hours=26),
            )
            started.append(order_id)
        except Exception:
            # Workflow start failed — release the reservation.
            _release_stock(order_id)

    return {"started": len(started), "order_ids": started}


@app.get("/api/orders")
async def list_orders(
    status: str | None = Query(None),
    repair_outcome: str | None = Query(None),
    requires_hitl: bool | None = Query(None),
    failure_type: str | None = Query(None),
    limit: int = Query(100),
):
    client = await get_client()

    query_parts = ['WorkflowType = "OrderWorkflow"']
    if status:
        query_parts.append(f'OrderStatus = "{status}"')
    if repair_outcome:
        query_parts.append(f'RepairOutcome = "{repair_outcome}"')
    if requires_hitl is not None:
        query_parts.append(f'RequiresHITL = {"true" if requires_hitl else "false"}')
    if failure_type:
        query_parts.append(f'FailureType = "{failure_type}"')

    query = " AND ".join(query_parts)

    orders = []
    async for wf in client.list_workflows(query=query):
        orders.append(_wf_to_order(wf))
        if len(orders) >= limit:
            break

    return orders


@app.get("/api/orders/{order_id}")
async def get_order(order_id: str):
    """Fetch a single order (used by the customer order-status page)."""
    client = await get_client()
    workflow_id = f"order-{order_id}"
    try:
        wf = await client.get_workflow_handle(workflow_id).describe()
    except Exception:
        raise HTTPException(status_code=404, detail="Order not found")
    return _wf_to_order(wf)


@app.get("/api/stats")
async def get_stats():
    client = await get_client()

    async def count_query(q: str) -> int:
        n = 0
        async for _ in client.list_workflows(query=q):
            n += 1
        return n

    total, completed, awaiting_hitl, auto_repaired, hitl_approved, hitl_denied, cancelled = await asyncio.gather(
        count_query('WorkflowType = "OrderWorkflow"'),
        count_query('WorkflowType = "OrderWorkflow" AND OrderStatus = "completed"'),
        count_query('WorkflowType = "OrderWorkflow" AND OrderStatus = "awaiting_hitl"'),
        count_query('WorkflowType = "OrderWorkflow" AND RepairOutcome = "auto_repaired"'),
        count_query('WorkflowType = "OrderWorkflow" AND RepairOutcome = "hitl_approved"'),
        count_query('WorkflowType = "OrderWorkflow" AND RepairOutcome = "hitl_denied"'),
        count_query('WorkflowType = "OrderWorkflow" AND OrderStatus = "cancelled"'),
    )

    return {
        "total": total,
        "completed": completed,
        "awaiting_hitl": awaiting_hitl,
        "auto_repaired": auto_repaired,
        "hitl_approved": hitl_approved,
        "hitl_denied": hitl_denied,
        "cancelled": cancelled,
        "in_progress": total - completed - cancelled,
    }


@app.post("/api/orders/{order_id}/approve")
async def approve_order(order_id: str, req: ApproveOrderRequest):
    """Direct approve — fallback when Slack is not configured."""
    client = await get_client()
    import datetime
    try:
        handle = client.get_workflow_handle(f"slack-conv-{order_id}")
        await handle.signal(
            SlackConversationWorkflow.receive_slack_action,
            SlackActionSignal(
                action_id="approve",
                user_id="ops-dashboard",
                user_name=req.user_name,
                timestamp=str(datetime.datetime.utcnow().timestamp()),
            ),
        )
        return {"status": "approved"}
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/orders/{order_id}/deny")
async def deny_order(order_id: str, req: ApproveOrderRequest):
    """Direct deny — fallback when Slack is not configured."""
    client = await get_client()
    import datetime
    try:
        handle = client.get_workflow_handle(f"slack-conv-{order_id}")
        await handle.signal(
            SlackConversationWorkflow.receive_slack_action,
            SlackActionSignal(
                action_id="deny",
                user_id="ops-dashboard",
                user_name=req.user_name,
                timestamp=str(datetime.datetime.utcnow().timestamp()),
            ),
        )
        return {"status": "denied"}
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---------------------------------------------------------------------------
# Customer HITL — email link landing page + order-page JSON endpoint
# ---------------------------------------------------------------------------

_DECISION_PAGE_TEMPLATE = """<!DOCTYPE html>
<html>
  <head>
    <title>Flourish &amp; Blotts — {title}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
  </head>
  <body style="font-family: Georgia, serif; background:#f7f3e7; padding:48px; color:#1a1f3a; text-align:center;">
    <div style="max-width:520px; margin:0 auto; background:#fffdf5; border:1px solid #d4b24a; border-radius:8px; padding:40px;">
      <h1 style="margin-top:0; color:{color};">{heading}</h1>
      <p style="color:#333; font-size:16px;">{message}</p>
      {cta}
    </div>
  </body>
</html>"""


def _decision_html(title: str, heading: str, message: str, color: str = "#1a1f3a", cta: str = "") -> str:
    return _DECISION_PAGE_TEMPLATE.format(
        title=title, heading=heading, message=message, color=color, cta=cta,
    )


async def _deliver_customer_decision(order_id: str, decision: str, source: str) -> str:
    """Signal the CustomerConfirmationWorkflow. Returns 'delivered' | 'already_closed'."""
    import datetime
    client = await get_client()
    handle = client.get_workflow_handle(f"customer-confirm-{order_id}")

    signal_value = CustomerDecisionSignal(
        decision="approved" if decision == "approve" else "denied",
        source=source,
        timestamp=str(datetime.datetime.utcnow().timestamp()),
    )

    try:
        await handle.signal("receive_customer_decision", signal_value)
        return "delivered"
    except Exception as e:
        # Most common case: the workflow has already closed (another click won or it timed out).
        # The outer handler converts this into a friendly "already handled" HTML page.
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/hitl/{order_id}/decision", response_class=HTMLResponse)
async def customer_hitl_landing(order_id: str, result: str, token: str):
    """Landing page for the Approve/Deny email links. Validates token then signals."""
    if result not in ("approve", "deny"):
        raise HTTPException(status_code=400, detail="result must be 'approve' or 'deny'")

    try:
        token_order_id, token_decision = verify_token(
            token, HITL_TOKEN_SECRET, HITL_TOKEN_MAX_AGE_SECONDS,
        )
    except ValueError as e:
        return HTMLResponse(
            _decision_html(
                title="Link invalid",
                heading="Link invalid or expired",
                message=f"We couldn't verify that link ({e}). If you still need to respond, "
                        "please use the prompt on your order status page.",
                color="#8b0000",
            ),
            status_code=400,
        )

    if token_order_id != order_id or token_decision != result:
        return HTMLResponse(
            _decision_html(
                title="Link mismatch",
                heading="Link details don't match",
                message="This link appears to have been tampered with. Please respond from "
                        "your order status page instead.",
                color="#8b0000",
            ),
            status_code=400,
        )

    try:
        await _deliver_customer_decision(order_id, result, source="email")
    except HTTPException:
        return HTMLResponse(
            _decision_html(
                title="Already handled",
                heading="Thanks — this order's already been decided",
                message="Another response (or a timeout) arrived first. You don't need to do anything else.",
            )
        )

    if result == "approve":
        heading = "Thanks — we're updating your order"
        message = "We've recorded your approval. Your order will continue processing shortly."
        color = "#2d5a2d"
    else:
        heading = "Order cancelled"
        message = "Your order is being cancelled and any payment will be refunded. A confirmation will follow."
        color = "#8b0000"

    return HTMLResponse(_decision_html(
        title=heading, heading=heading, message=message, color=color,
    ))


class CustomerDecisionRequest(BaseModel):
    decision: str  # "approve" | "deny"
    user_note: str = ""


@app.post("/api/orders/{order_id}/customer-decision")
async def order_page_customer_decision(order_id: str, req: CustomerDecisionRequest):
    """Decision posted from the /orders/:id page's Pending Decision card."""
    if req.decision not in ("approve", "deny"):
        raise HTTPException(status_code=400, detail="decision must be 'approve' or 'deny'")
    await _deliver_customer_decision(order_id, req.decision, source="order_page")
    return {"status": "delivered", "decision": req.decision}


@app.get("/api/orders/{order_id}/pending-decision")
async def get_pending_decision(order_id: str):
    """Query the CustomerConfirmationWorkflow for its pending prompt (if any)."""
    client = await get_client()
    handle = client.get_workflow_handle(f"customer-confirm-{order_id}")
    try:
        pending = await handle.query("get_pending_decision")
    except Exception:
        # No workflow running or already decided.
        return {"pending": None}
    if pending is None:
        return {"pending": None}
    # pending crosses the pydantic data converter; it may come back as a dict, a
    # pydantic BaseModel, or still as the original dataclass depending on the SDK
    # version. Normalize to dict for JSON serialization.
    if isinstance(pending, dict):
        return {"pending": pending}
    if hasattr(pending, "model_dump"):
        return {"pending": pending.model_dump()}
    from dataclasses import asdict, is_dataclass
    if is_dataclass(pending):
        return {"pending": asdict(pending)}
    return {"pending": pending}


# ---------------------------------------------------------------------------
# SSE stream for real-time ops dashboard
# ---------------------------------------------------------------------------

@app.get("/api/orders/stream")
async def orders_stream():
    async def event_generator() -> AsyncGenerator[str, None]:
        client = await get_client()
        while True:
            try:
                orders = []
                async for wf in client.list_workflows(query='WorkflowType = "OrderWorkflow"'):
                    orders.append(_wf_to_order(wf))

                data = json.dumps(orders)
                yield f"data: {data}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

            await asyncio.sleep(3)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
