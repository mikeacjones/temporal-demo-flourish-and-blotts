"""Verify if a customer has the required credentials or permissions for a restricted item."""
from __future__ import annotations

import asyncio
import random
from datetime import timedelta

from temporalio import workflow

from shared.agent_harness import (
    ToolCategory,
    ToolCtx,
    ops_tool,
    repair_tool,
)

with workflow.unsafe.imports_passed_through():
    from worker.agent.tool_args import VerifyCustomerCredentialsArgs


_READ_TIMEOUT = timedelta(seconds=10)


async def _verify_credentials(customer_id: str, requirement_type: str) -> str:
    """Check Ministry records for customer credentials.

    PORT FROM: worker/activities/repair_activities.py:verify_customer_credentials
    """
    await asyncio.sleep(random.uniform(0.3, 0.8))
    found = random.random() > 0.3
    if found:
        return (
            f"Customer '{customer_id}' credential check PASSED for requirement "
            f"'{requirement_type}'. Records found in Ministry database."
        )
    return (
        f"Customer '{customer_id}' credential check INCONCLUSIVE for "
        f"'{requirement_type}'. Records not found. Manual verification required."
    )


@repair_tool(category=ToolCategory.READ, timeout=_READ_TIMEOUT)
@ops_tool(category=ToolCategory.READ, timeout=_READ_TIMEOUT)
async def verify_customer_credentials(args: VerifyCustomerCredentialsArgs, ctx: ToolCtx) -> str:
    """Verify if a customer has the required credentials or permissions for a restricted item.
    Checks Ministry records, Hogwarts enrollment, or N.E.W.T. qualifications."""
    return await ctx.activity(
        _verify_credentials,
        args.customer_id,
        args.requirement_type,
        summary=f"Verify credentials for customer '{args.customer_id}' ({args.requirement_type}).",
        start_to_close_timeout=_READ_TIMEOUT,
    )
