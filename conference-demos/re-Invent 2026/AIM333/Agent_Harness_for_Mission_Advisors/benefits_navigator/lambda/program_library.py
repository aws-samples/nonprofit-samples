# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Synthetic program-library Lambda function for the AgentCore Gateway target.

Plain AWS Lambda function serving as a tool target for AgentCore Gateway.
See lambda/household_lookup.py for the shared input/context format notes.

Returns pre-authored enrollment milestone content by template_id, so the model
grounds its plan in real content instead of inventing steps. All content
below is synthetic demonstration data only and does not reproduce any real
program's actual application process.
"""

DELIMITER = "___"

# Synthetic demo data only.
SYNTHETIC_PROGRAM_TEMPLATES = {
    "food-assistance-enrollment": {
        "template_id": "food-assistance-enrollment",
        "milestones": [
            "Gather household identification, proof of income, and residency documents.",
            "Confirm household size and everyone who buys and prepares food together.",
            "Submit the food-assistance application through the administering agency.",
            "Complete the eligibility interview if the agency requests one.",
            "Review the agency's determination notice with a navigator and appeal if needed.",
        ],
    },
    "health-coverage-enrollment": {
        "template_id": "health-coverage-enrollment",
        "milestones": [
            "Gather household identification, proof of income, and residency documents.",
            "Note any categorical factors (pregnancy, disability, children) that may affect coverage.",
            "Submit the health-coverage application through the administering agency.",
            "Respond to any agency requests for additional verification promptly.",
            "Review the agency's determination notice with a navigator and appeal if needed.",
        ],
    },
}


def lambda_handler(event, context):
    """Handle a program-library tool call routed here by AgentCore Gateway.

    Event: {"template_id": "food-assistance-enrollment" | "health-coverage-enrollment"}.
    """
    tool_name = ""
    try:
        original_tool_name = context.client_context.custom["bedrockAgentCoreToolName"]
        tool_name = original_tool_name[original_tool_name.index(DELIMITER) + len(DELIMITER):]
    except (AttributeError, KeyError, ValueError):
        tool_name = event.pop("tool_name", "program_library")

    if tool_name != "program_library":
        return {"error": f"Unknown tool: {tool_name}", "available_tools": ["program_library"]}

    template_id = event.get("template_id", "")
    template = SYNTHETIC_PROGRAM_TEMPLATES.get(template_id)
    if template is None:
        return {
            "error": f"No synthetic program template exists for template_id={template_id!r}.",
            "available_template_ids": list(SYNTHETIC_PROGRAM_TEMPLATES.keys()),
        }
    return {**template, "milestones": list(template["milestones"])}
