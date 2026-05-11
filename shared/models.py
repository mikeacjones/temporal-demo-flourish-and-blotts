from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class OrderStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    PAYMENT_PROCESSING = "payment_processing"
    VERIFYING_CREDENTIALS = "verifying_credentials"
    PICK_AND_PACK = "pick_and_pack"
    DISPATCHING = "dispatching"
    REPAIR_IN_PROGRESS = "repair_in_progress"
    AWAITING_HITL = "awaiting_hitl"
    AWAITING_CUSTOMER = "awaiting_customer"
    AWAITING_OPS = "awaiting_ops"
    COMPENSATING = "compensating"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    CANCELLED_BY_CUSTOMER = "cancelled_by_customer"
    CANCELLED_BY_OPS = "cancelled_by_ops"
    CANCELLED_UNRESOLVED = "cancelled_unresolved"


class FailureType(str, Enum):
    NONE = "none"
    MONSTER_BOOK_ESCAPE = "monster_book_escape"
    MINISTRY_APPROVAL_REQUIRED = "ministry_approval_required"
    FLOO_MISDIRECTED = "floo_misdirected"
    GRINGOTTS_FAILURE = "gringotts_failure"
    OWL_INTERCEPTED = "owl_intercepted"
    RESTRICTED_SECTION = "restricted_section"
    INVENTORY_MISMATCH = "inventory_mismatch"
    WAREHOUSE_FAILURE = "warehouse_failure"
    PAYMENT_TIMEOUT = "payment_timeout"


class RepairOutcome(str, Enum):
    AUTO_REPAIRED = "auto_repaired"
    HITL_APPROVED = "hitl_approved"
    HITL_DENIED = "hitl_denied"
    CUSTOMER_APPROVED = "customer_approved"
    CUSTOMER_DENIED = "customer_denied"
    OPS_APPROVED = "ops_approved"
    OPS_DENIED = "ops_denied"
    UNRESOLVED = "unresolved"


@dataclass
class BookItem:
    id: str
    title: str
    author: str
    price_galleons: float
    description: str
    category: str  # "standard" | "restricted" | "dangerous" | "rare"
    # OMS-believed stock — this is what the storefront shows to customers and
    # what they're allowed to order against. May diverge from physical reality
    # (that divergence is the whole point of the inventory_mismatch scenario).
    in_stock: int
    requires_ministry_approval: bool = False
    age_restriction: Optional[int] = None
    cover_color: str = "#1a1f3a"
    # Actual count on the warehouse shelf right now. None means it matches
    # `in_stock` (no divergence). When set explicitly and lower than `in_stock`,
    # pick_and_pack will fail with INVENTORY_MISMATCH at fulfilment time and
    # check_inventory will report this physical truth.
    physical_in_stock: Optional[int] = None

    @property
    def physical_count(self) -> int:
        """The actual on-shelf count (falls back to OMS count when not divergent)."""
        return self.physical_in_stock if self.physical_in_stock is not None else self.in_stock


@dataclass
class OrderInput:
    order_id: str
    customer_name: str
    customer_email: str
    book_id: str
    book_title: str
    quantity: int
    delivery_method: str  # "owl_post" | "floo_network" | "portkey_express"
    delivery_address: str
    forced_failure: Optional[str] = None


@dataclass
class OrderStepFailure:
    step: str
    failure_type: str
    description: str
    context: dict = field(default_factory=dict)


@dataclass
class RepairPlanStep:
    action: str
    description: str
    tool: Optional[str] = None
    tool_args: dict = field(default_factory=dict)


@dataclass
class RepairPlan:
    steps: list[RepairPlanStep] = field(default_factory=list)
    rationale: str = ""
    urgency: str = "medium"


@dataclass
class SlackMessageSignal:
    user_id: str
    user_name: str
    text: str
    timestamp: str


@dataclass
class SlackActionSignal:
    action_id: str  # "approve" or "deny"
    user_id: str
    user_name: str
    timestamp: str


@dataclass
class ConversationTurn:
    role: str  # "agent" | "human"
    content: str
    timestamp: str


@dataclass
class SlackConversationInput:
    order_id: str
    order_input: OrderInput
    failure: OrderStepFailure
    initial_plan: Optional[RepairPlan]
    slack_channel: str


@dataclass
class SlackConversationResult:
    status: str  # "approved" | "denied" | "timeout"
    final_plan: Optional[RepairPlan]
    conversation_history: list[ConversationTurn] = field(default_factory=list)
    decided_by: Optional[str] = None
    notes: str = ""


@dataclass
class OrderRepairInput:
    order_id: str
    order_input: OrderInput
    failure: OrderStepFailure


@dataclass
class OrderRepairResult:
    status: str  # "resolved" | "cancelled" | "unresolved"
    outcome: str  # RepairOutcome value
    repair_steps_executed: list[str] = field(default_factory=list)
    requires_hitl: bool = False
    notes: str = ""
    # If the agent (with customer approval) swapped the ordered book during repair,
    # this is the post-substitution OrderInput. The parent OrderWorkflow uses it
    # for any subsequent OMS steps so they pick the substituted book, not the
    # original. None means no substitution was applied.
    updated_order: Optional[OrderInput] = None


@dataclass
class CallClaudeInput:
    messages: list[dict]
    system: str
    tools: list[dict] = field(default_factory=list)


@dataclass
class ClaudeToolUse:
    id: str
    name: str
    input: dict


@dataclass
class ClaudeResponse:
    stop_reason: str
    text: str
    content: list[dict]  # serialized content blocks
    tool_uses: list[ClaudeToolUse] = field(default_factory=list)


@dataclass
class ToolCallInput:
    name: str
    args: dict
    order_id: str = ""


@dataclass
class PostInitialMessageInput:
    channel: str
    order_id: str
    order_input: OrderInput
    failure: OrderStepFailure
    plan: Optional[RepairPlan]
    workflow_id: str


@dataclass
class PostReplyInput:
    channel: str
    thread_ts: str
    message: str
    updated_plan: Optional[RepairPlan]
    workflow_id: str


@dataclass
class ProcessMessageInput:
    message: SlackMessageSignal
    order_input: OrderInput
    failure: OrderStepFailure
    current_plan: Optional[RepairPlan]
    history: list[ConversationTurn]


@dataclass
class ProcessMessageResult:
    response_text: str
    updated_plan: Optional[RepairPlan]


# ---------------------------------------------------------------------------
# Customer HITL (email + order-status page) — confirmation workflow
# ---------------------------------------------------------------------------

@dataclass
class CustomerConfirmationOption:
    value: str  # "approve" | "deny"
    label: str  # display text, e.g. "Yes, substitute it"


@dataclass
class CustomerConfirmationInput:
    order_id: str
    order_input: OrderInput
    question: str
    description: str  # longer HTML-safe explanation for the email body
    proposed_action: str  # agent-readable description of what will happen on approval
    options: list[CustomerConfirmationOption] = field(default_factory=list)


@dataclass
class CustomerDecisionSignal:
    decision: str  # "approved" | "denied"
    source: str  # "email" | "order_page"
    user_note: str = ""
    timestamp: str = ""


@dataclass
class CustomerConfirmationResult:
    status: str  # "approved" | "denied" | "timeout"
    source: str = ""  # which channel delivered the decision
    note: str = ""


@dataclass
class PendingCustomerDecision:
    """Shape exposed via workflow query so the API can surface it to the UI."""
    order_id: str
    question: str
    description: str
    proposed_action: str
    options: list[CustomerConfirmationOption] = field(default_factory=list)


@dataclass
class SendConfirmationEmailInput:
    order_id: str
    to_email: str
    customer_name: str
    question: str
    description: str
    approve_url: str
    deny_url: str
    expires_at_iso: str


# ---------------------------------------------------------------------------
# Saga compensation
# ---------------------------------------------------------------------------

@dataclass
class CompensationInput:
    order_id: str
    order_input: OrderInput
    forward_step: str
    context: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Slack Ops-Agent (per-thread conversational entity workflow)
# ---------------------------------------------------------------------------

@dataclass
class ToolResult:
    """Result of executing a single tool call. Used as a tool_result content block
    when round-tripping back to Claude. is_error=True signals to the model that the
    tool failed and it should self-correct rather than treat content as ground truth."""
    tool_use_id: str
    content: str
    is_error: bool = False


@dataclass
class OpsAgentConversationInput:
    """Input to OpsAgentConversationWorkflow.run — one workflow per Slack thread.
    thread_ts is the Slack epoch-format timestamp of the thread root and is part of
    the deterministic workflow ID, so the same thread always maps to the same workflow."""
    channel: str
    thread_ts: str
    initial_message: str
    user_id: str
    user_name: str


@dataclass
class OpsActionSignal:
    """Slack interaction (button click / picker selection) tagged with the
    originating tool_use_id so the workflow can route the result to the right
    pending future when multiple human-input tools are in flight in a turn."""
    tool_use_id: str
    value: str  # confirmation: "confirm" | "deny"; picker: selected order_id
    user_id: str
    user_name: str
    timestamp: str


@dataclass
class PostConfirmationCardInput:
    channel: str
    thread_ts: str
    workflow_id: str
    tool_use_id: str
    title: str  # e.g. "Cancel order ORD-1?"
    description: str  # agent's reasoning / what will happen
    confirm_label: str = "Confirm"
    deny_label: str = "Deny"


@dataclass
class PickerOption:
    value: str
    label: str


@dataclass
class PostOrderPickerInput:
    channel: str
    thread_ts: str
    workflow_id: str
    tool_use_id: str
    prompt: str
    options: list[PickerOption] = field(default_factory=list)


@dataclass
class CollapseButtonsInput:
    """Replace the action-row of a previously-posted Slack message with a static
    summary line so the buttons can no longer be clicked."""
    channel: str
    message_ts: str
    summary_line: str  # e.g. "✅ Approved by @michael" or "Selected ORD-1"


@dataclass
class PostThreadReplyInput:
    """Plain agent reply in the ops-agent thread (no plan, no buttons)."""
    channel: str
    thread_ts: str
    text: str


@dataclass
class PostThreadClosedNoticeInput:
    """Routes channel+thread_ts to post_thread_closed_notice activity, which posts
    a fixed 'idle-closed' notice in the thread when the workflow's 24h idle
    timeout fires. No body field — the notice text is hard-coded in the activity."""
    channel: str
    thread_ts: str


# ---------------------------------------------------------------------------
# Ops-agent activities — read tool inputs/outputs
# ---------------------------------------------------------------------------

@dataclass
class ListOrdersInput:
    status: Optional[str] = None
    failure_type: Optional[str] = None
    since_hours: Optional[int] = None
    limit: int = 50
    start_time_after_iso: Optional[str] = None


@dataclass
class OrderSummary:
    order_id: str
    workflow_id: str
    workflow_type: str
    status: str
    order_status: str = ""  # from search attribute


@dataclass
class ListOrdersResult:
    orders: list[OrderSummary] = field(default_factory=list)


@dataclass
class DescribeOrderInput:
    order_id: str


@dataclass
class DescribeOrderResult:
    order_id: str
    workflow_id: str
    status: str  # RUNNING / COMPLETED / etc
    start_time_iso: str = ""
    close_time_iso: str = ""
    search_attributes: dict = field(default_factory=dict)
    # Other workflows tagged with the same OrderId search attribute — repair
    # workflow, customer-confirmation child, slack-conversation HITL child.
    # This is how the agent gets the *real* in-flight state: the parent
    # OrderWorkflow's search attributes are stale once it's awaiting a child,
    # but the child's own attributes (e.g. RequiresHITL=True, OrderStatus=
    # awaiting_customer) reflect the live HITL state.
    related_workflows: list["RelatedWorkflowSummary"] = field(default_factory=list)


@dataclass
class RelatedWorkflowSummary:
    workflow_id: str
    workflow_type: str
    status: str
    search_attributes: dict = field(default_factory=dict)


@dataclass
class DescribeWorkflowInput:
    workflow_id: str


@dataclass
class DescribeWorkflowResult:
    workflow_id: str
    workflow_type: str
    status: str
    start_time_iso: str = ""
    close_time_iso: str = ""
    search_attributes: dict = field(default_factory=dict)


@dataclass
class GetWorkflowHistoryInput:
    workflow_id: str
    max_events: int = 200


@dataclass
class WorkflowHistoryEvent:
    event_id: int
    timestamp_iso: str
    event_type: str  # e.g. "WorkflowExecutionStarted", "ActivityTaskScheduled"
    summary: str = ""  # short human-readable detail (activity name, signal name, failure msg)


@dataclass
class GetWorkflowHistoryResult:
    workflow_id: str
    events: list[WorkflowHistoryEvent] = field(default_factory=list)
    truncated: bool = False  # True if max_events cut the result short


@dataclass
class AggregateFailuresInput:
    since_hours: Optional[int] = None
    start_time_after_iso: Optional[str] = None


@dataclass
class FailureBucket:
    failure_type: str
    count: int


@dataclass
class AggregateFailuresResult:
    buckets: list[FailureBucket] = field(default_factory=list)
    total: int = 0


@dataclass
class InventoryItem:
    book_id: str
    title: str
    author: str
    in_stock: int
    physical_in_stock: Optional[int] = None
    category: str = ""


@dataclass
class ListInventoryResult:
    items: list[InventoryItem] = field(default_factory=list)


@dataclass
class GetBookInput:
    book_id: str


@dataclass
class GetBookResult:
    found: bool
    item: Optional[InventoryItem] = None


# ---------------------------------------------------------------------------
# Ops-agent mutation inputs/outputs
# ---------------------------------------------------------------------------

@dataclass
class CancelOrderInput:
    order_id: str
    reason: str
    tool_use_id: str  # idempotency key — Claude-generated, deterministic per replay


@dataclass
class CancelOrderResult:
    cancelled: bool
    note: str = ""


@dataclass
class AdjustInventoryInput:
    book_id: str
    delta: int  # positive or negative
    reason: str
    tool_use_id: str  # idempotency key


@dataclass
class AdjustInventoryResult:
    applied: bool
    new_in_stock: int
    note: str = ""


@dataclass
class PostCardResult:
    message_ts: str = ""
    is_error: bool = False
    error_message: str = ""


@dataclass
class PostThreadReplyResult:
    message_ts: str = ""
    is_error: bool = False
    error_message: str = ""


@dataclass
class PostRichThreadReplyInput:
    """Block-Kit reply in the ops-agent thread. The agent calls this via the
    post_rich_reply tool when plain mrkdwn isn't expressive enough."""
    channel: str
    thread_ts: str
    blocks: list[dict] = field(default_factory=list)
    fallback_text: str = ""
