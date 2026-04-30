"""Tool implementation activities — the actual repair actions the agent can take."""
import asyncio
import random

from temporalio import activity

from shared.catalog import get_book_by_id
from shared.models import ToolCallInput, RepairPlanStep


@activity.defn
async def execute_repair_tool(call: ToolCallInput) -> str:
    await asyncio.sleep(random.uniform(0.3, 0.8))

    name = call.name
    args = call.args
    order_id = call.order_id or args.get("order_id", "")

    if name == "check_inventory":
        book = get_book_by_id(args["item_id"])
        if not book:
            return f"Item '{args['item_id']}' not found in catalog."
        # Physical count is what matters at fulfilment time — that's what the
        # warehouse will actually pick from the shelf. We surface the OMS count
        # too when it disagrees so the agent can reason about the divergence.
        physical = book.physical_count
        if physical == book.in_stock:
            return (
                f"Inventory check: '{book.title}' — {physical} copies on the shelf "
                "at Diagon Alley warehouse."
            )
        return (
            f"Inventory check: '{book.title}' — only {physical} copies physically on "
            f"the shelf at Diagon Alley warehouse (OMS records {book.in_stock}; the "
            "OMS count is stale and cannot be filled against)."
        )

    if name == "apply_containment_charm":
        item_id = args.get("item_id", "")
        book = get_book_by_id(item_id)
        title = book.title if book else item_id
        outcome = random.choice([
            f"Containment charm applied successfully to '{title}'. Item subdued and ready for repackaging with dragon-hide reinforced box.",
            f"Enhanced containment charm applied to '{title}'. Three attempts required — book resisted. Now secured with Unbreakable Charm reinforcement.",
        ])
        return f"Order {order_id}: {outcome}"

    if name == "dispatch_house_elf":
        task = args.get("task", "unspecified task")
        elves = ["Dobby", "Kreacher", "Winky", "Hokey", "Mipsy"]
        elf = random.choice(elves)
        outcomes = [
            f"{elf} has been dispatched and completed the task successfully: {task}",
            f"{elf} reports task complete. Note: {elf} is very happy to help and requests no payment.",
        ]
        return f"Order {order_id}: House elf {random.choice(outcomes)}"

    if name == "reroute_via_floo":
        destination = args.get("destination", order_id)
        return (
            f"Order {order_id}: Floo Network rerouting initiated. "
            f"Package redirected to '{destination}'. "
            "Floo Regulation Panel notified. Estimated re-delivery: 2 hours."
        )

    if name == "update_order_status":
        status = args.get("status", "processing")
        message = args.get("message", "")
        return f"Order {order_id} status updated to '{status}': {message}"

    if name == "contact_customer":
        message = args.get("message", "")
        return f"Notification owl dispatched to customer for Order {order_id}: '{message}'"

    if name == "substitute_item":
        # substitute_item is a state-mutating tool and is handled inline in
        # OrderRepairWorkflow — never via this activity. Reaching this branch
        # means the workflow misrouted the call; surface that loudly rather
        # than silently lying that the swap happened.
        return (
            f"ERROR: substitute_item must be handled in the workflow, not as an "
            f"activity. The substitution did NOT take effect for order {order_id}. "
            "This is a worker routing bug — please report it."
        )

    if name == "verify_customer_credentials":
        customer_id = args.get("customer_id", "")
        req_type = args.get("requirement_type", "")
        # Simulate: sometimes credentials are found, sometimes not
        found = random.random() > 0.3
        if found:
            return f"Customer '{customer_id}' credential check PASSED for requirement '{req_type}'. Records found in Ministry database."
        return f"Customer '{customer_id}' credential check INCONCLUSIVE for '{req_type}'. Records not found. Manual verification required."

    return f"Unknown tool '{name}' — no action taken."


@activity.defn
async def execute_approved_plan_step(step: RepairPlanStep, order_id: str) -> str:
    """Execute a single step from a human-approved repair plan."""
    await asyncio.sleep(random.uniform(0.2, 0.6))

    if step.tool:
        call = ToolCallInput(name=step.tool, args=step.tool_args, order_id=order_id)
        return await execute_repair_tool(call)

    return f"Executed plan step '{step.action}': {step.description}"
