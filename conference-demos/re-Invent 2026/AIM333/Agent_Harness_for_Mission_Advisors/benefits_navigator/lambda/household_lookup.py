# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Synthetic household-lookup Lambda function for the AgentCore Gateway target.

This is a plain AWS Lambda function that serves as a tool target for
AgentCore Gateway. It does NOT implement the MCP protocol itself — AgentCore
Gateway handles MCP protocol translation and routes tool calls to this
function.

Input format from AgentCore Gateway:
- Event: a flat dict of the tool's input properties (e.g. {"household_id": "household-201"}).
- Context: contains the tool name in
  context.client_context.custom["bedrockAgentCoreToolName"], formatted as
  "{target_name}___{tool_name}".

All household data below is synthetic and used for demonstration only. It does
not represent any real household, and none of the income figures or
categorical factors reflect any real person or any real program's rules.
"""

DELIMITER = "___"

# Synthetic demo data only — not a real household.
SYNTHETIC_HOUSEHOLDS = {
    "household-201": {
        "household_id": "household-201",
        "display_name": "Household A",
        "household_size": 3,
        "monthly_income": 2100,
        "state": "WA",
        "has_children": True,
        "is_pregnant": False,
        "has_disability": False,
        "oldest_member_age": 34,
        "stated_need": "Screen for food assistance and plan enrollment.",
    },
    "household-202": {
        "household_id": "household-202",
        "display_name": "Household B",
        "household_size": 1,
        "monthly_income": 1450,
        "state": "WA",
        "has_children": False,
        "is_pregnant": False,
        "has_disability": True,
        "oldest_member_age": 58,
        "stated_need": "Screen for health coverage and plan enrollment.",
    },
}


def lambda_handler(event, context):
    """Handle a household-lookup tool call routed here by AgentCore Gateway.

    Event: tool input properties as a flat dict (e.g. {"household_id": "household-201"}).
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
        tool_name = event.pop("tool_name", "household_lookup")

    if tool_name != "household_lookup":
        return {"error": f"Unknown tool: {tool_name}", "available_tools": ["household_lookup"]}

    household_id = event.get("household_id", "")
    household = SYNTHETIC_HOUSEHOLDS.get(household_id)
    if household is None:
        return {
            "error": f"No synthetic household exists for household_id={household_id!r}.",
            "available_household_ids": list(SYNTHETIC_HOUSEHOLDS.keys()),
        }
    return household
