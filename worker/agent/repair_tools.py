"""ToolDef list for the repair agent.

Each ToolDef is registered (validated) at import time. A MUTATING tool
without a human-confirmation guard fails registration and crashes the
worker."""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from shared.agent_harness import (
        ToolCategory,
        ToolDef,
        register_tool,
    )
    from shared.models import ClaudeToolUse, ToolCallInput
    from worker.activities.ops_activities import list_inventory
    from worker.activities.repair_activities import execute_repair_tool
    from worker.agent.guards import substitute_item_customer_confirmation
    from worker.agent.interactions import (
        escalate_to_human_interaction,
        request_customer_confirmation_interaction,
        substitute_item_repair_interaction,
    )
    from worker.agent.tool_args import (
        ApplyContainmentCharmArgs,
        CheckInventoryArgs,
        ContactCustomerArgs,
        DispatchHouseElfArgs,
        EscalateToHumanArgs,
        ListInventoryArgs,
        RequestCustomerConfirmationArgs,
        RerouteViaFlooArgs,
        SubstituteItemArgs,
        UpdateOrderStatusArgs,
        VerifyCustomerCredentialsArgs,
    )


def _repair_tool_call_input(tool_name: str):
    """Factory: build a make_impl_input that wraps args into a ToolCallInput
    targeted at execute_repair_tool with the given tool name."""
    def make(args, tool_use: ClaudeToolUse, agent_ctx) -> ToolCallInput:
        return ToolCallInput(
            name=tool_name,
            args=args.model_dump(),
            order_id=getattr(args, "order_id", "") or args.model_dump().get("order_id", ""),
        )
    return make


_LONG_TIMEOUT = timedelta(seconds=120)
_DEFAULT_TIMEOUT = timedelta(seconds=30)
_READ_TIMEOUT = timedelta(seconds=10)


LIST_INVENTORY_REPAIR_TOOL = register_tool(ToolDef(
    name="list_inventory",
    description=(
        "List the entire book catalog with current OMS stock counts and physical "
        "warehouse counts. Useful for browsing substitute candidates when a book "
        "is out of stock — returns every book with its title, author, OMS in_stock, "
        "and physical_in_stock so you can pick a viable substitute in one call "
        "rather than running check_inventory book by book."
    ),
    args_model=ListInventoryArgs,
    category=ToolCategory.READ,
    impl=list_inventory,
    timeout=_READ_TIMEOUT,
))


CHECK_INVENTORY_REPAIR_TOOL = register_tool(ToolDef(
    name="check_inventory",
    description=(
        "Check current inventory levels for a book item at Flourish & Blotts warehouse."
    ),
    args_model=CheckInventoryArgs,
    category=ToolCategory.READ,
    impl=execute_repair_tool,
    make_impl_input=_repair_tool_call_input("check_inventory"),
    timeout=_DEFAULT_TIMEOUT,
))

APPLY_CONTAINMENT_CHARM_REPAIR_TOOL = register_tool(ToolDef(
    name="apply_containment_charm",
    description=(
        "Apply a magical containment charm to an escaped or dangerous magical item. "
        "Use for Monster Book of Monsters escapes or other dangerous book incidents. "
        "The charm restrains the item and makes it safe for repackaging."
    ),
    args_model=ApplyContainmentCharmArgs,
    category=ToolCategory.AUTONOMOUS,
    impl=execute_repair_tool,
    make_impl_input=_repair_tool_call_input("apply_containment_charm"),
    timeout=_DEFAULT_TIMEOUT,
))

DISPATCH_HOUSE_ELF_REPAIR_TOOL = register_tool(ToolDef(
    name="dispatch_house_elf",
    description=(
        "Dispatch a house elf for magical manual intervention. Use for tasks requiring "
        "physical wizarding assistance: retrieving intercepted deliveries, capturing escaped "
        "magical items, emergency repackaging, or any on-site intervention."
    ),
    args_model=DispatchHouseElfArgs,
    category=ToolCategory.AUTONOMOUS,
    impl=execute_repair_tool,
    make_impl_input=_repair_tool_call_input("dispatch_house_elf"),
    timeout=_LONG_TIMEOUT,
))

REROUTE_VIA_FLOO_REPAIR_TOOL = register_tool(ToolDef(
    name="reroute_via_floo",
    description=(
        "Reroute a delivery via the Floo Network to a corrected or alternative destination. "
        "Use when Floo misdirection has occurred or as a fallback for failed owl deliveries."
    ),
    args_model=RerouteViaFlooArgs,
    category=ToolCategory.AUTONOMOUS,
    impl=execute_repair_tool,
    make_impl_input=_repair_tool_call_input("reroute_via_floo"),
    timeout=_LONG_TIMEOUT,
))

UPDATE_ORDER_STATUS_REPAIR_TOOL = register_tool(ToolDef(
    name="update_order_status",
    description="Update the status and add a note to an order in the OMS.",
    args_model=UpdateOrderStatusArgs,
    category=ToolCategory.AUTONOMOUS,
    impl=execute_repair_tool,
    make_impl_input=_repair_tool_call_input("update_order_status"),
    timeout=_DEFAULT_TIMEOUT,
))

CONTACT_CUSTOMER_REPAIR_TOOL = register_tool(ToolDef(
    name="contact_customer",
    description="Send a notification owl (email) to the customer about their order status.",
    args_model=ContactCustomerArgs,
    category=ToolCategory.AUTONOMOUS,
    impl=execute_repair_tool,
    make_impl_input=_repair_tool_call_input("contact_customer"),
    timeout=_DEFAULT_TIMEOUT,
))

VERIFY_CUSTOMER_CREDENTIALS_REPAIR_TOOL = register_tool(ToolDef(
    name="verify_customer_credentials",
    description=(
        "Verify if a customer has the required credentials or permissions for a restricted item. "
        "Checks Ministry records, Hogwarts enrollment, or N.E.W.T. qualifications."
    ),
    args_model=VerifyCustomerCredentialsArgs,
    category=ToolCategory.READ,
    impl=execute_repair_tool,
    make_impl_input=_repair_tool_call_input("verify_customer_credentials"),
    timeout=_DEFAULT_TIMEOUT,
))

SUBSTITUTE_ITEM_REPAIR_TOOL = register_tool(ToolDef(
    name="substitute_item",
    description=(
        "Commit a substitution of the customer's ordered book with a different in-stock book. "
        "The harness automatically asks the customer for approval (via email + on the order page) "
        "before the substitution is applied — Claude does NOT need to ask first via "
        "request_customer_confirmation. If the customer denies, this tool returns an error reason "
        "and you may propose a different substitute or escalate. The substitute must exist in the "
        "catalog and have enough physical stock; otherwise the tool returns an ERROR result. "
        "After a successful substitute_item, call update_order_status('repaired', ...) once and "
        "end your turn."
    ),
    args_model=SubstituteItemArgs,
    category=ToolCategory.MUTATING,
    guards=(substitute_item_customer_confirmation,),
    interaction=substitute_item_repair_interaction,
    timeout=_DEFAULT_TIMEOUT,
))

REQUEST_CUSTOMER_CONFIRMATION_TOOL = register_tool(ToolDef(
    name="request_customer_confirmation",
    description=(
        "Ask the ordering customer directly to attest, accept, or confirm something whose answer "
        "is itself the resolution — there is no follow-up tool to gate. Use for: "
        "(a) Ministry of Magic approval / Form 27B/6 for Restricted Publications, "
        "(b) age-verification or Restricted Section credential attestations, "
        "(c) accepting an extended delivery window, "
        "(d) other customer-action problems where the customer's confirmation IS the action. "
        "Do NOT use this for substitutions — call substitute_item directly; the harness will "
        "ask the customer for substitution approval automatically. "
        "The customer gets an email with Approve/Deny links AND sees the same prompt on their "
        "/orders/:id page. Returns 'approved' / 'denied' / 'timeout'. On 'denied' or 'timeout' "
        "the order will be cancelled."
    ),
    args_model=RequestCustomerConfirmationArgs,
    category=ToolCategory.HITL_INTERACTION,
    interaction=request_customer_confirmation_interaction,
))

ESCALATE_TO_HUMAN_TOOL = register_tool(ToolDef(
    name="escalate_to_human",
    description=(
        "Escalate to a Flourish & Blotts OPS OPERATOR via Slack — only for decisions an operator "
        "can resolve within the shop, without waiting on the customer. Use for: "
        "(a) approving a large refund above the agent's authority, "
        "(b) overriding an automated fraud or security hold, "
        "(c) authorising a one-off manual workaround, "
        "(d) edge cases requiring human judgment that don't depend on the customer submitting "
        "anything. The proposed_plan must be EXECUTABLE end-to-end at approval time — no "
        "'wait for the customer to submit X' steps. Anything customer-driven belongs in "
        "request_customer_confirmation instead. Calling this tool ends the repair turn — the "
        "operator's decision is the final resolution."
    ),
    args_model=EscalateToHumanArgs,
    category=ToolCategory.HITL_INTERACTION,
    interaction=escalate_to_human_interaction,
    terminates_loop=True,
))


REPAIR_TOOLS: list[ToolDef] = [
    LIST_INVENTORY_REPAIR_TOOL,
    CHECK_INVENTORY_REPAIR_TOOL,
    APPLY_CONTAINMENT_CHARM_REPAIR_TOOL,
    DISPATCH_HOUSE_ELF_REPAIR_TOOL,
    REROUTE_VIA_FLOO_REPAIR_TOOL,
    UPDATE_ORDER_STATUS_REPAIR_TOOL,
    CONTACT_CUSTOMER_REPAIR_TOOL,
    SUBSTITUTE_ITEM_REPAIR_TOOL,
    VERIFY_CUSTOMER_CREDENTIALS_REPAIR_TOOL,
    REQUEST_CUSTOMER_CONFIRMATION_TOOL,
    ESCALATE_TO_HUMAN_TOOL,
]
