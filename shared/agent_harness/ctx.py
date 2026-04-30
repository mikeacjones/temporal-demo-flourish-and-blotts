"""AgentCtx — workflow-side context threaded through guards and interactions.

Mutable. The workflow's signal handler resolves futures stored in
pending_actions. Interactions write to domain_state; the workflow envelope
reads it after run_agent_turn returns."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentCtx:
    # Pending HITL futures, keyed by tool_use_id. Resolved by the workflow's
    # signal handler when the human responds.
    pending_actions: dict[str, asyncio.Future[str]] = field(default_factory=dict)

    # Slack threading — populated by Slack-context agents (ops conversation),
    # left None by repair. Guards that need them assert at runtime.
    channel: str | None = None
    thread_ts: str | None = None

    # The workflow's typed input — interactions read this for domain data
    # (e.g. customer_email from OrderRepairInput).
    domain_input: Any = None

    # Workflow-owned mutable state. Interactions write to it; the workflow
    # reads it after run_agent_turn returns. Repair defines a typed
    # RepairAgentState dataclass for this slot.
    domain_state: Any = None
