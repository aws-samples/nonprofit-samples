# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Synthetic advisor-library Lambda function for the AgentCore Gateway target.

Plain AWS Lambda function serving as a tool target for AgentCore Gateway.
See lambda/member_lookup.py for the shared input/context format notes.

Returns pre-authored plan milestone content by template_id, so the model
grounds its plan in real content instead of inventing steps. All content
below is synthetic demonstration data only.
"""

DELIMITER = "___"

# Synthetic demo data only.
SYNTHETIC_PLAN_TEMPLATES = {
    "retirement-readiness": {
        "template_id": "retirement-readiness",
        "milestones": [
            "Confirm current retirement account contributions and employer match.",
            "Estimate monthly income needed at target retirement age.",
            "Identify any contribution gap against a recommended savings rate.",
            "Review catch-up contribution eligibility if age 50 or older.",
            "Review progress with a qualified advisor annually.",
        ],
    },
    "home-buying-readiness": {
        "template_id": "home-buying-readiness",
        "milestones": [
            "Confirm current savings earmarked for a down payment.",
            "Check debt-to-income ratio against typical mortgage guidelines.",
            "Review mortgage pre-qualification options and estimated rate.",
            "Estimate closing costs and other one-time purchase expenses.",
            "Review progress with a qualified advisor before making an offer.",
        ],
    },
}


def lambda_handler(event, context):
    """Handle an advisor-library tool call routed here by AgentCore Gateway.

    Event: {"template_id": "retirement-readiness" | "home-buying-readiness"}.
    """
    tool_name = ""
    try:
        original_tool_name = context.client_context.custom["bedrockAgentCoreToolName"]
        tool_name = original_tool_name[original_tool_name.index(DELIMITER) + len(DELIMITER):]
    except (AttributeError, KeyError, ValueError):
        tool_name = event.pop("tool_name", "advisor_library")

    if tool_name != "advisor_library":
        return {"error": f"Unknown tool: {tool_name}", "available_tools": ["advisor_library"]}

    template_id = event.get("template_id", "")
    template = SYNTHETIC_PLAN_TEMPLATES.get(template_id)
    if template is None:
        return {
            "error": f"No synthetic plan template exists for template_id={template_id!r}.",
            "available_template_ids": list(SYNTHETIC_PLAN_TEMPLATES.keys()),
        }
    return {**template, "milestones": list(template["milestones"])}
