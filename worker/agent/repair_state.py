"""Repair-agent workflow-side state types.

Owned by OrderRepairWorkflow. Interactions write to fields on RepairAgentState
(via agent_ctx.domain_state); the workflow's _shape_repair_result reads them after
run_agent_turn returns to build OrderRepairResult."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from shared.models import SlackConversationResult


@dataclass
class EscalationOutcome:
    """Set by the escalate_to_human interaction."""
    slack_result: SlackConversationResult
    plan_steps_executed: list[str]
    skip_note: str = ""


@dataclass
class CustomerDenial:
    """Set by the request_customer_confirmation interaction when the
    customer denies or times out — the repair workflow then terminates
    as cancelled_by_customer."""
    status: Literal["denied", "timeout"]
    note: str = ""


@dataclass
class RepairAgentState:
    # (original_item_id, substitute_item_id, reason). Set by the
    # substitute_item_repair interaction after its customer-confirmation
    # guard passes.
    staged_substitution: Optional[tuple[str, str, str]] = None
    escalation_outcome: Optional[EscalationOutcome] = None
    customer_denial: Optional[CustomerDenial] = None
