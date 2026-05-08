"""ToolDef list for the ops conversation agent.

Replaces the hand-written OPS_AGENT_TOOLS schema dicts and the
MUTATING_TOOL_NAMES / PICKER_TOOL_NAMES drift assertions. The harness's
import-time policy validator now enforces the rule (every MUTATING tool
must carry an OPS_CONFIRMATION guard).

Repair-toolkit ops tools (apply_containment_charm, dispatch_house_elf,
reroute_via_floo, update_order_status, contact_customer, substitute_item)
share the underlying execute_repair_tool activity with the repair agent
but carry an ops_confirmation guard so the operator must approve each."""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from shared.agent_harness import (
        ToolCategory,
        ToolDef,
        register_tool,
    )
    from shared.models import (
        AdjustInventoryInput,
        CancelOrderInput,
        ClaudeToolUse,
        ToolCallInput,
    )
    from worker.activities.ops_activities import (
        adjust_inventory,
        aggregate_repair_failures,
        cancel_order,
        describe_order,
        describe_workflow,
        get_book,
        get_workflow_history,
        list_inventory,
        list_orders,
        post_rich_thread_reply,
    )
    from worker.activities.repair_activities import execute_repair_tool
    from worker.agent.guards import ops_confirmation
    from worker.agent.interactions import post_order_picker_interaction
    from worker.agent.tool_args import (
        AdjustInventoryArgs,
        AggregateRepairFailuresArgs,
        ApplyContainmentCharmArgs,
        CancelOrderArgs,
        CheckInventoryArgs,
        ContactCustomerArgs,
        DescribeOrderArgs,
        DescribeWorkflowArgs,
        DispatchHouseElfArgs,
        GetBookArgs,
        GetWorkflowHistoryArgs,
        ListInventoryArgs,
        ListOrdersArgs,
        PostOrderPickerArgs,
        PostRichReplyArgs,
        RerouteViaFlooArgs,
        SubstituteItemArgs,
        UpdateOrderStatusArgs,
        VerifyCustomerCredentialsArgs,
    )


_LONG_TIMEOUT = timedelta(seconds=120)
_DEFAULT_TIMEOUT = timedelta(seconds=30)
_READ_TIMEOUT = timedelta(seconds=10)


def _repair_tool_call_input(tool_name: str):
    """Same factory as in repair_tools.py — build a ToolCallInput targeted
    at execute_repair_tool with the given tool name."""
    def make(args, tool_use: ClaudeToolUse, agent_ctx) -> ToolCallInput:
        return ToolCallInput(
            name=tool_name,
            args=args.model_dump(),
            order_id=args.model_dump().get("order_id", ""),
        )
    return make


def _cancel_order_make_input(
    args: CancelOrderArgs, tool_use: ClaudeToolUse, _agent_ctx,
) -> CancelOrderInput:
    return CancelOrderInput(order_id=args.order_id, reason=args.reason, tool_use_id=tool_use.id)


def _adjust_inventory_make_input(
    args: AdjustInventoryArgs, tool_use: ClaudeToolUse, _agent_ctx,
) -> AdjustInventoryInput:
    return AdjustInventoryInput(
        book_id=args.book_id, delta=args.delta, reason=args.reason, tool_use_id=tool_use.id,
    )


def _post_rich_reply_make_input(args: PostRichReplyArgs, tool_use: ClaudeToolUse, agent_ctx):
    from shared.models import PostRichThreadReplyInput
    return PostRichThreadReplyInput(
        channel=agent_ctx.channel,
        thread_ts=agent_ctx.thread_ts,
        blocks=args.blocks,
        fallback_text=args.fallback_text,
    )


# ---------------------------------------------------------------------------
# READ tools — Temporal Visibility, catalog, repair-toolkit reads
# ---------------------------------------------------------------------------

LIST_ORDERS_OPS_TOOL = register_tool(ToolDef(
    name="list_orders",
    description=(
        "List Flourish & Blotts orders matching optional filters. Returns one "
        "row per OrderId. Backed by Temporal Visibility (~1-2s eventual consistency lag). "
        "When a `status` filter is set, results may include any workflow type carrying "
        "that OrderStatus — some states live on the active child workflow, not the "
        "parent OrderWorkflow. The returned `workflow_id` reflects whichever workflow "
        "currently carries that status — feed it into `describe_workflow` or "
        "`get_workflow_history` to drill in."
    ),
    args_model=ListOrdersArgs,
    category=ToolCategory.READ,
    impl=list_orders,
    timeout=_READ_TIMEOUT,
))

DESCRIBE_ORDER_OPS_TOOL = register_tool(ToolDef(
    name="describe_order",
    description=(
        "Describe an order's workflow execution AND every related workflow tagged "
        "with the same OrderId — repair workflow, customer-confirmation child, "
        "slack-conversation HITL child. The parent OrderWorkflow's search attributes "
        "go stale once it's awaiting a child workflow. Look at the `related_workflows` "
        "array for the live HITL/repair state."
    ),
    args_model=DescribeOrderArgs,
    category=ToolCategory.READ,
    impl=describe_order,
    timeout=_READ_TIMEOUT,
))

DESCRIBE_WORKFLOW_OPS_TOOL = register_tool(ToolDef(
    name="describe_workflow",
    description=(
        "Describe ANY workflow by its workflow_id — useful when describe_order's "
        "related_workflows hands you a child's workflow_id and you want to drill in. "
        "Returns status, timing, and search attributes."
    ),
    args_model=DescribeWorkflowArgs,
    category=ToolCategory.READ,
    impl=describe_workflow,
    timeout=_READ_TIMEOUT,
))

GET_WORKFLOW_HISTORY_OPS_TOOL = register_tool(ToolDef(
    name="get_workflow_history",
    description=(
        "Return the structured event history of a workflow — every activity scheduled/"
        "completed/failed, signals received, child workflows started, timers, search-"
        "attribute upserts. Use this when you need to know exactly WHAT HAPPENED — e.g. "
        "'why is this order stuck?', 'what activities did the repair agent run?', "
        "'which signals has this workflow received?'. Each event is summarized with a "
        "one-line detail. Bounded by max_events to keep responses tight; default 200."
    ),
    args_model=GetWorkflowHistoryArgs,
    category=ToolCategory.READ,
    impl=get_workflow_history,
    timeout=_READ_TIMEOUT,
))

AGGREGATE_REPAIR_FAILURES_OPS_TOOL = register_tool(ToolDef(
    name="aggregate_repair_failures",
    description=(
        "Group recent OrderRepairWorkflows by FailureType and return counts — "
        "useful for answering 'what's been breaking lately?'"
    ),
    args_model=AggregateRepairFailuresArgs,
    category=ToolCategory.READ,
    impl=aggregate_repair_failures,
    timeout=_READ_TIMEOUT,
))

LIST_INVENTORY_OPS_TOOL = register_tool(ToolDef(
    name="list_inventory",
    description="List the entire book catalog with current OMS stock counts.",
    args_model=ListInventoryArgs,
    category=ToolCategory.READ,
    impl=list_inventory,
    timeout=_READ_TIMEOUT,
))

GET_BOOK_OPS_TOOL = register_tool(ToolDef(
    name="get_book",
    description="Get a single book by ID.",
    args_model=GetBookArgs,
    category=ToolCategory.READ,
    impl=get_book,
    timeout=_READ_TIMEOUT,
))

CHECK_INVENTORY_OPS_TOOL = register_tool(ToolDef(
    name="check_inventory",
    description="Check current physical inventory level for a book at the warehouse.",
    args_model=CheckInventoryArgs,
    category=ToolCategory.READ,
    impl=execute_repair_tool,
    make_impl_input=_repair_tool_call_input("check_inventory"),
    timeout=_READ_TIMEOUT,
))

VERIFY_CUSTOMER_CREDENTIALS_OPS_TOOL = register_tool(ToolDef(
    name="verify_customer_credentials",
    description="Verify whether a customer holds a credential (e.g. ministry approval).",
    args_model=VerifyCustomerCredentialsArgs,
    category=ToolCategory.READ,
    impl=execute_repair_tool,
    make_impl_input=_repair_tool_call_input("verify_customer_credentials"),
    timeout=_READ_TIMEOUT,
))


# ---------------------------------------------------------------------------
# MUTATING tools — every one carries ops_confirmation guard
# ---------------------------------------------------------------------------

CANCEL_ORDER_OPS_TOOL = register_tool(ToolDef(
    name="cancel_order",
    description=(
        "Cancel an order's workflow. Confirmation required from the operator before this "
        "runs. Naturally idempotent — cancelling an already-cancelled order is a no-op."
    ),
    args_model=CancelOrderArgs,
    category=ToolCategory.MUTATING,
    guards=(ops_confirmation,),
    impl=cancel_order,
    make_impl_input=_cancel_order_make_input,
    timeout=_READ_TIMEOUT,
))

ADJUST_INVENTORY_OPS_TOOL = register_tool(ToolDef(
    name="adjust_inventory",
    description=(
        "Adjust the OMS in_stock count for a book by a positive or negative delta. "
        "Confirmation required."
    ),
    args_model=AdjustInventoryArgs,
    category=ToolCategory.MUTATING,
    guards=(ops_confirmation,),
    impl=adjust_inventory,
    make_impl_input=_adjust_inventory_make_input,
    timeout=_READ_TIMEOUT,
))

APPLY_CONTAINMENT_CHARM_OPS_TOOL = register_tool(ToolDef(
    name="apply_containment_charm",
    description=(
        "Apply a magical containment charm to a dangerous book in a specific order. "
        "Confirmation required."
    ),
    args_model=ApplyContainmentCharmArgs,
    category=ToolCategory.MUTATING,
    guards=(ops_confirmation,),
    impl=execute_repair_tool,
    make_impl_input=_repair_tool_call_input("apply_containment_charm"),
    timeout=_DEFAULT_TIMEOUT,
))

DISPATCH_HOUSE_ELF_OPS_TOOL = register_tool(ToolDef(
    name="dispatch_house_elf",
    description=(
        "Dispatch a house elf for manual intervention on a specific order. "
        "Confirmation required."
    ),
    args_model=DispatchHouseElfArgs,
    category=ToolCategory.MUTATING,
    guards=(ops_confirmation,),
    impl=execute_repair_tool,
    make_impl_input=_repair_tool_call_input("dispatch_house_elf"),
    timeout=_LONG_TIMEOUT,
))

REROUTE_VIA_FLOO_OPS_TOOL = register_tool(ToolDef(
    name="reroute_via_floo",
    description=(
        "Reroute a delivery via Floo Network. Confirmation required. PRECONDITION: the "
        "package must have been retrieved first — never call this in parallel with "
        "dispatch_house_elf in the same turn; chain them across turns."
    ),
    args_model=RerouteViaFlooArgs,
    category=ToolCategory.MUTATING,
    guards=(ops_confirmation,),
    impl=execute_repair_tool,
    make_impl_input=_repair_tool_call_input("reroute_via_floo"),
    timeout=_LONG_TIMEOUT,
))

UPDATE_ORDER_STATUS_OPS_TOOL = register_tool(ToolDef(
    name="update_order_status",
    description="Update an order's status. Confirmation required.",
    args_model=UpdateOrderStatusArgs,
    category=ToolCategory.MUTATING,
    guards=(ops_confirmation,),
    impl=execute_repair_tool,
    make_impl_input=_repair_tool_call_input("update_order_status"),
    timeout=_DEFAULT_TIMEOUT,
))

SUBSTITUTE_ITEM_OPS_TOOL = register_tool(ToolDef(
    name="substitute_item",
    description=(
        "Replace a book in an order with a substitute. Confirmation required. "
        "Only valid for orders currently in repair."
    ),
    args_model=SubstituteItemArgs,
    category=ToolCategory.MUTATING,
    guards=(ops_confirmation,),
    impl=execute_repair_tool,
    make_impl_input=_repair_tool_call_input("substitute_item"),
    timeout=_DEFAULT_TIMEOUT,
))

CONTACT_CUSTOMER_OPS_TOOL = register_tool(ToolDef(
    name="contact_customer",
    description="Send a notification message to the customer about an order. Confirmation required.",
    args_model=ContactCustomerArgs,
    category=ToolCategory.MUTATING,
    guards=(ops_confirmation,),
    impl=execute_repair_tool,
    make_impl_input=_repair_tool_call_input("contact_customer"),
    timeout=_DEFAULT_TIMEOUT,
))


# ---------------------------------------------------------------------------
# Slack-output + HITL_INTERACTION
# ---------------------------------------------------------------------------

POST_RICH_REPLY_OPS_TOOL = register_tool(ToolDef(
    name="post_rich_reply",
    description=(
        "Post a richly-formatted reply in the thread using Slack Block Kit. Use this when "
        "plain Slack-mrkdwn isn't enough — comparisons across many fields, multi-section "
        "breakdowns, key-value lists, or anything where you want headers/dividers/contextual "
        "footers. Pass a list of Block Kit block objects. Section text uses Slack mrkdwn "
        "(single-asterisk *bold*, _italic_, `code`, <https://url|label>) — NOT Markdown. "
        "No tables — use a section with `fields` for columns. On error returns is_error=True. "
        "When you call this tool, do NOT also include a redundant prose response — let the "
        "rich reply speak for itself."
    ),
    args_model=PostRichReplyArgs,
    category=ToolCategory.SLACK_OUTPUT,
    impl=post_rich_thread_reply,
    make_impl_input=_post_rich_reply_make_input,
    timeout=_DEFAULT_TIMEOUT,
))

POST_ORDER_PICKER_OPS_TOOL = register_tool(ToolDef(
    name="post_order_picker",
    description=(
        "Post an interactive dropdown of in-flight orders in the thread and return the "
        "order_id the operator selects. Use when the operator should choose which order "
        "to act on. Returns the selected order_id."
    ),
    args_model=PostOrderPickerArgs,
    category=ToolCategory.HITL_INTERACTION,
    interaction=post_order_picker_interaction,
))


OPS_TOOLS: list[ToolDef] = [
    # READ
    LIST_ORDERS_OPS_TOOL,
    DESCRIBE_ORDER_OPS_TOOL,
    DESCRIBE_WORKFLOW_OPS_TOOL,
    GET_WORKFLOW_HISTORY_OPS_TOOL,
    AGGREGATE_REPAIR_FAILURES_OPS_TOOL,
    LIST_INVENTORY_OPS_TOOL,
    GET_BOOK_OPS_TOOL,
    CHECK_INVENTORY_OPS_TOOL,
    VERIFY_CUSTOMER_CREDENTIALS_OPS_TOOL,
    # MUTATING
    CANCEL_ORDER_OPS_TOOL,
    ADJUST_INVENTORY_OPS_TOOL,
    APPLY_CONTAINMENT_CHARM_OPS_TOOL,
    DISPATCH_HOUSE_ELF_OPS_TOOL,
    REROUTE_VIA_FLOO_OPS_TOOL,
    UPDATE_ORDER_STATUS_OPS_TOOL,
    SUBSTITUTE_ITEM_OPS_TOOL,
    CONTACT_CUSTOMER_OPS_TOOL,
    # Slack output
    POST_RICH_REPLY_OPS_TOOL,
    # HITL_INTERACTION
    POST_ORDER_PICKER_OPS_TOOL,
]


def build_ops_system_prompt(user_name: str) -> str:
    return f"""You are the Flourish & Blotts wizarding bookshop OPS AGENT in Slack.
You are talking to operator {user_name} about the live OMS.

Your role is to introspect and (with explicit confirmation) act on Temporal-managed
order workflows, repairs, and inventory. You have read access via Temporal Visibility
and a small set of mutating tools that REQUIRE the operator to click a confirmation
button before they actually run.

Tool-call rules:
- Within a single turn you may issue MULTIPLE tool calls — they run in parallel.
  Only batch INDEPENDENT calls.
- Some tools are dependent: e.g. `reroute_via_floo` is invalid until
  `dispatch_house_elf` has retrieved the package. For dependent tools, call the
  prerequisite first and wait for its result, then call the dependent tool in the
  next turn.
- For mutating tools, the operator sees a Block-Kit confirmation card with the
  description you provide. Be precise about what the action will do.
- For `post_order_picker`, return-value is the chosen order_id; do nothing else
  in that turn — wait for the next turn to use the chosen ID.
- Visibility query results are eventually consistent (~1-2s) — recent state
  changes may not show up immediately. Don't assert stale data is missing.
- Reading order/workflow state correctly:
  * `describe_order(order_id)` returns the parent OrderWorkflow PLUS every
    related child (repair, customer-confirmation, slack HITL). The parent's
    own search attributes go STALE while it awaits a child — e.g. you may
    see `RequiresHITL=False` on the parent even when a child is actively
    awaiting customer input. ALWAYS check `related_workflows` for the live
    state. The repair child's `OrderStatus` (`awaiting_customer`,
    `awaiting_ops`, `repair_in_progress`) is the source of truth.
  * Need to know exactly what happened in a workflow? Call
    `get_workflow_history(workflow_id)` — returns every activity, signal,
    child-workflow start, timer, and failure as a structured timeline.
  * Need to inspect a child workflow you saw in `related_workflows`? Use
    `describe_workflow(workflow_id)` directly on its ID.

Analyzing "what happened" / "why did X occur" — RIGOR REQUIREMENTS:
When the operator asks an analytical question — "why did this fail?",
"what was the root cause?", "what did the agent do?", "did anyone reconcile
the counts?" — you MUST ground your answer in evidence from the workflow
history before answering. Do not infer, guess, or back-rationalize from
the current state.

- ALWAYS call `get_workflow_history(workflow_id)` on the relevant workflow
  before claiming you know what happened. Search attributes describe the
  CURRENT state, not the timeline. The history is the timeline.
- Several search-attribute values are LOSSY summaries that hide important
  detail:
    * `RepairOutcome=auto_repaired` covers BOTH cases:
        (a) the agent fixed the failure with no human input, AND
        (b) the agent proposed a substitution, the customer approved it via
            a CustomerConfirmationWorkflow, the agent then executed
            `substitute_item` and ended the turn.
      You CANNOT tell these apart from search attributes alone. Pull the
      repair workflow's history and look for `request_customer_confirmation`
      and `substitute_item` events to know whether substitution happened.
- After a substitution, the order's CURRENT book differs from the
  ORIGINALLY-FAILING book. The operator's question is almost always about
  the original failure, not the substitute. The original `book_id` lives
  in the OrderWorkflow's input event (the very first event in its history)
  and in the OrderRepairInput passed to the repair workflow. The substitute
  is what the order was redirected to, not what failed.
- If you find yourself about to say "the counts must have been reconciled"
  or "the agent likely corrected the discrepancy" without an event in
  history showing a reconciliation/correction step, STOP. That's
  back-rationalizing. Either find the event or say "I can see the failure
  was resolved but the history doesn't show a reconciliation step — the
  resolution path was [whatever the history actually shows]".
- When asked "why did an inventory_mismatch occur for order X", the answer
  almost always involves the divergence between OMS-believed `in_stock`
  and warehouse-actual `physical_in_stock` for the ORIGINAL book in that
  order. Pull the original book via `get_book(<original_book_id>)` — not
  the substitute.

Slack formatting rules — IMPORTANT:
You are posting to Slack, NOT GitHub or generic Markdown. Slack uses its own
"mrkdwn" flavor with key differences:
- Bold: `*bold*` (single asterisks). `**double**` renders LITERALLY — don't use it.
- Italic: `_italic_` (underscores). `*italic*` is bold in Slack.
- Strikethrough: `~strike~`. Code: `` `code` ``. Codeblock: triple backticks.
- Bulleted lists: `• item` or `- item` on their own lines. Numbered: `1. item`.
- Links: `<https://example.com|label>` — angle brackets, pipe separator.
  NOT `[label](url)` — that renders literally.
- Mentions: `<@USERID>` for users, `<#CHANNELID|name>` for channels.
- Quote: line prefixed with `>`.
- NO TABLES. Markdown pipe-tables (`| col | col |`) render as literal text. Use
  one of these instead:
    a) plain bullet list with key-value pairs: `• *Order ID:* ord-123 — *status:* …`
    b) the `post_rich_reply` tool with a `section` block whose `fields:` is a
       2-column array of `{{type:'mrkdwn', text:'*Header*\\nvalue'}}` pairs.

When to call `post_rich_reply` instead of replying with plain text:
- More than ~5 rows of structured data
- Multi-section responses where headers and dividers help readability
- Anything you'd be tempted to format as a table

When you call `post_rich_reply`, leave your assistant prose response empty (or one
short sentence at most) — the Block Kit reply IS the message; don't duplicate it.

Style: brief, professional, with a touch of wizarding charm. Don't over-explain.
"""
