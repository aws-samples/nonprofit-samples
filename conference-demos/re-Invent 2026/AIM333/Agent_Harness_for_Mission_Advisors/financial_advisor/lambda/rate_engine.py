# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Synthetic rate-engine Lambda function for the AgentCore Gateway target.

Plain AWS Lambda function serving as a tool target for AgentCore Gateway.
See lambda/member_lookup.py for the shared input/context format notes.

All rate data below is synthetic demonstration data only. It does not
represent any real credit union product, rate, or offer.
"""

DELIMITER = "___"

# Synthetic demo data only.
SYNTHETIC_RATES = {
    "retirement-savings": {
        "product": "Synthetic credit union retirement certificate (IRA CD ladder)",
        "annual_percentage_yield": 4.5,
        "term_months": 60,
    },
    "home-purchase": {
        "product": "Synthetic fixed-rate first-time homebuyer mortgage",
        "annual_percentage_rate": 6.75,
        "term_months": 360,
    },
}


def lambda_handler(event, context):
    """Handle a rate-engine tool call routed here by AgentCore Gateway.

    Event: {"purpose": "retirement-savings" | "home-purchase"}.
    """
    tool_name = ""
    try:
        original_tool_name = context.client_context.custom["bedrockAgentCoreToolName"]
        tool_name = original_tool_name[original_tool_name.index(DELIMITER) + len(DELIMITER):]
    except (AttributeError, KeyError, ValueError):
        tool_name = event.pop("tool_name", "rate_engine")

    if tool_name != "rate_engine":
        return {"error": f"Unknown tool: {tool_name}", "available_tools": ["rate_engine"]}

    purpose = event.get("purpose", "")
    rate = SYNTHETIC_RATES.get(purpose)
    if rate is None:
        return {
            "error": f"No synthetic rate exists for purpose={purpose!r}.",
            "available_purposes": list(SYNTHETIC_RATES.keys()),
        }
    return rate
