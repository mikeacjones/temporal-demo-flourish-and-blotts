"""Pydantic arg models for every agent-visible tool.

Each model defines the JSON Schema Claude sees and validates the tool_use
input. For tools where the activity expects a different input shape (e.g.
includes tool_use_id), the ToolDef supplies a make_impl_input adapter."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Repair toolkit (used by both REPAIR and OPS agents)
# ---------------------------------------------------------------------------

class CheckInventoryArgs(BaseModel):
    item_id: str = Field(description="The book item ID (e.g. 'mnbm-001')")


class ApplyContainmentCharmArgs(BaseModel):
    order_id: str
    item_id: str


class DispatchHouseElfArgs(BaseModel):
    order_id: str
    task: str = Field(description="Detailed description of what the house elf should do")


class RerouteViaFlooArgs(BaseModel):
    order_id: str
    destination: str = Field(description="Corrected Floo Network destination address")


class UpdateOrderStatusArgs(BaseModel):
    order_id: str
    status: str = Field(description="One of: processing, repaired, awaiting_delivery, on_hold, delayed")
    message: str = Field(description="Human-readable status update message")


class ContactCustomerArgs(BaseModel):
    order_id: str
    message: str = Field(description="Message to send to the customer")


class SubstituteItemArgs(BaseModel):
    order_id: str
    original_item_id: str
    substitute_item_id: str
    reason: str


class VerifyCustomerCredentialsArgs(BaseModel):
    customer_id: str
    requirement_type: str = Field(
        description="Type of credential needed (e.g. 'ministry_approval', 'newt_credentials')",
    )


# ---------------------------------------------------------------------------
# Repair-only HITL_INTERACTION tools
# ---------------------------------------------------------------------------

class RequestCustomerConfirmationArgs(BaseModel):
    question: str = Field(description="Short, customer-friendly headline question")
    description: str = Field(description="Longer explanation shown in the email body and order page")
    proposed_action: str = Field(description="Agent-readable description of what will happen on approval")
    approve_label: str = Field(default="Yes, proceed")
    deny_label: str = Field(default="No, cancel my order")


class EscalateToHumanPlanStep(BaseModel):
    action: str
    description: str
    tool: Optional[str] = None
    tool_args: dict = Field(default_factory=dict)


class EscalateToHumanArgs(BaseModel):
    context: str = Field(description="Clear description of the issue, diagnosis, what's been tried")
    proposed_plan: list[EscalateToHumanPlanStep] = Field(default_factory=list)
    rationale: str = Field(default="")
    urgency: str = Field(default="medium", description="One of: low, medium, high, critical")


# ---------------------------------------------------------------------------
# Ops-only read tools
# ---------------------------------------------------------------------------

class ListOrdersArgs(BaseModel):
    status: Optional[str] = None
    failure_type: Optional[str] = None
    since_hours: Optional[int] = None
    limit: int = 50


class DescribeOrderArgs(BaseModel):
    order_id: str


class DescribeWorkflowArgs(BaseModel):
    workflow_id: str


class GetWorkflowHistoryArgs(BaseModel):
    workflow_id: str
    max_events: int = 200


class AggregateRepairFailuresArgs(BaseModel):
    since_hours: Optional[int] = None


class ListInventoryArgs(BaseModel):
    """No fields — list_inventory is parameterless."""


class GetBookArgs(BaseModel):
    book_id: str


# ---------------------------------------------------------------------------
# Ops-only mutation tools
# ---------------------------------------------------------------------------

class CancelOrderArgs(BaseModel):
    order_id: str
    reason: str


class AdjustInventoryArgs(BaseModel):
    book_id: str
    delta: int = Field(description="Positive to add stock, negative to remove")
    reason: str


# ---------------------------------------------------------------------------
# Ops-only Slack tools
# ---------------------------------------------------------------------------

class PostRichReplyArgs(BaseModel):
    blocks: list[dict] = Field(description="List of Block Kit block objects")
    fallback_text: str = Field(description="Plain-text fallback for notifications")


class PostOrderPickerArgs(BaseModel):
    prompt: str
    status_filter: Optional[str] = Field(
        default=None,
        description="Optional OrderStatus to limit the picker to",
    )
