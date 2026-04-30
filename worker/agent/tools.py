"""SlackConversationWorkflow's `update_plan` tool schema.

REPAIR_AGENT_TOOLS used to live here too, but the agent harness refactor
replaced it with ToolDefs in worker/agent/repair_tools.py. CONVERSATION_TOOLS
remains for the slack-conversation child workflow's plan-amendment tool —
that flow doesn't run through the harness."""

CONVERSATION_TOOLS = [
    {
        "name": "update_plan",
        "description": (
            "Update the repair plan based on the human operator's instructions. "
            "Call this when the human wants to modify, add, or remove steps from the plan. "
            "Also provide the message to post back to Slack."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "description": "Updated ordered list of repair steps",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string"},
                            "description": {"type": "string"},
                            "tool": {"type": "string"},
                            "tool_args": {"type": "object"},
                        },
                        "required": ["action", "description"],
                    },
                },
                "rationale": {"type": "string"},
                "response_to_human": {
                    "type": "string",
                    "description": "What to post back in the Slack thread (acknowledging their change, confirming the updated plan)",
                },
            },
            "required": ["steps", "rationale", "response_to_human"],
        },
    }
]
