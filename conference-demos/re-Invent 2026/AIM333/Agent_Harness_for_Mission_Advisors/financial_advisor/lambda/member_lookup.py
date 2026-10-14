# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Synthetic member-lookup Lambda function for the AgentCore Gateway target.

This is a plain AWS Lambda function that serves as a tool target for
AgentCore Gateway. It does NOT implement the MCP protocol itself — AgentCore
Gateway handles MCP protocol translation and routes tool calls to this
function.

Input format from AgentCore Gateway:
- Event: a flat dict of the tool's input properties (e.g. {"member_id": "member-101"}).
- Context: contains the tool name in
  context.client_context.custom["bedrockAgentCoreToolName"], formatted as
  "{target_name}___{tool_name}".

All member data below is synthetic and used for demonstration only. It does
not represent any real credit union member.
"""

DELIMITER = "___"

# Synthetic demo data only — not a real credit union member.
SYNTHETIC_MEMBERS = {
    "member-101": {
        "member_id": "member-101",
        "display_name": "John Miller",
        "annual_income": 96000,
        "current_retirement_contribution_monthly": 450,
        "employer_match_percent": 4,
        "target_retirement_age": 65,
        "current_age": 42,
        "financial_goal": "Assess retirement readiness and close any savings gap before age 65.",
    },
    "member-102": {
        "member_id": "member-102",
        "display_name": "Jane Carter",
        "annual_income": 88000,
        "current_savings_balance": 24000,
        "monthly_savings_rate": 900,
        "target_home_price": 320000,
        "financial_goal": "Save toward a down payment and assess mortgage pre-qualification for a first home purchase.",
    },
}


def lambda_handler(event, context):
    """Handle a member-lookup tool call routed here by AgentCore Gateway.

    Event: tool input properties as a flat dict (e.g. {"member_id": "member-101"}).
    Context: client_context.custom contains bedrockAgentCoreToolName with the
        format "{target_name}___{tool_name}".

    Returns a JSON-serializable result; the gateway handles response framing.
    """
    tool_name = ""
    try:
        original_tool_name = context.client_context.custom["bedrockAgentCoreToolName"]
        tool_name = original_tool_name[original_tool_name.index(DELIMITER) + len(DELIMITER):]
    except (AttributeError, KeyError, ValueError):
        # Fallback for local/manual invocation without a Gateway-supplied context.
        tool_name = event.pop("tool_name", "member_lookup")

    if tool_name != "member_lookup":
        return {"error": f"Unknown tool: {tool_name}", "available_tools": ["member_lookup"]}

    member_id = event.get("member_id", "")
    member = SYNTHETIC_MEMBERS.get(member_id)
    if member is None:
        return {
            "error": f"No synthetic member exists for member_id={member_id!r}.",
            "available_member_ids": list(SYNTHETIC_MEMBERS.keys()),
        }
    return member
