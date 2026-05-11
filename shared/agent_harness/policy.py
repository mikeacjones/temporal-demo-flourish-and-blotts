"""Org-level tool policy: which categories require which guard kinds.

Validation runs at tool-registration time (i.e. module import) so policy
violations crash the worker on startup rather than at first invocation."""
from __future__ import annotations

from typing import TYPE_CHECKING

from shared.agent_harness.guards import GuardKind
from shared.agent_harness.tooldef import ToolCategory, ToolDef

if TYPE_CHECKING:
    pass


HUMAN_CONFIRMATION_KINDS = frozenset({
    GuardKind.CUSTOMER_CONFIRMATION,
    GuardKind.OPS_CONFIRMATION,
})


# Org-level policy. To add a new rule (e.g. "all SLACK_OUTPUT tools must
# carry an audit guard"), add a kind here and the registry validator will
# enforce it from the next worker startup.
CATEGORY_POLICIES: dict[ToolCategory, frozenset[GuardKind]] = {
    ToolCategory.READ:             frozenset(),
    ToolCategory.MUTATING:         HUMAN_CONFIRMATION_KINDS,  # at least one
    ToolCategory.AUTONOMOUS:       frozenset(),
    ToolCategory.HITL_INTERACTION: frozenset(),
    ToolCategory.SLACK_OUTPUT:     frozenset(),
}


class ToolPolicyError(Exception):
    """Raised at registration time when a ToolDef does not satisfy its
    category's policy. Crashes the worker on startup — that is the point."""


def validate_tool(tool: ToolDef) -> None:
    """Check structural invariants and category policy for a ToolDef.

    Structural:
      * Exactly one of `impl`, `interaction`, or `body` is set.
      * `terminates_loop=True` is only allowed for HITL_INTERACTION.
      * HITL_INTERACTION tools may not use `impl` (must use `interaction` or `body`).
    Policy:
      * For categories listed in CATEGORY_POLICIES with required kinds, at
        least one guard of one of the required kinds must be present.
    """
    has_impl = tool.impl is not None
    has_interaction = tool.interaction is not None
    has_body = tool.body is not None
    set_count = sum([has_impl, has_interaction, has_body])
    if set_count != 1:
        raise ToolPolicyError(
            f"Tool {tool.name!r}: exactly one of impl/interaction/body must be set "
            f"(got impl={has_impl}, interaction={has_interaction}, body={has_body})"
        )
    if tool.category == ToolCategory.HITL_INTERACTION and has_impl:
        raise ToolPolicyError(
            f"HITL_INTERACTION tool {tool.name!r} must use `interaction` or `body`, not `impl`"
        )
    if tool.terminates_loop and tool.category != ToolCategory.HITL_INTERACTION:
        raise ToolPolicyError(
            f"Tool {tool.name!r}: terminates_loop=True is only valid for HITL_INTERACTION"
        )

    required = CATEGORY_POLICIES.get(tool.category, frozenset())
    if not required:
        return
    present = {getattr(g, "kind", None) for g in tool.guards}
    if not (present & required):
        raise ToolPolicyError(
            f"Tool {tool.name!r} (category={tool.category.value}) needs "
            f"at least one guard of kind {sorted(k.value for k in required)}; "
            f"has {sorted(k.value for k in present if k)}"
        )
