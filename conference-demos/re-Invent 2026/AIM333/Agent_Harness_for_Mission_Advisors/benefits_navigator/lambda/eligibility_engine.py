# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Synthetic eligibility-engine Lambda function for the AgentCore Gateway target.

Plain AWS Lambda function serving as a tool target for AgentCore Gateway.
See lambda/household_lookup.py for the shared input/context format notes.

Returns a PRELIMINARY, SYNTHETIC eligibility SCREENING for a household against
a program category — never an official determination. The thresholds below are
invented for demonstration and do not reproduce any real program's actual
income limits, household-size adjustments, deductions, or categorical rules.
The administering agency makes every binding eligibility decision.
"""

DELIMITER = "___"

# Synthetic demo thresholds only. These are illustrative round numbers, not
# any real program's figures. Monthly gross income limit is expressed as a
# base plus a per-additional-person increment purely to show household-size
# sensitivity in the screening.
SYNTHETIC_PROGRAMS = {
    "food-assistance": {
        "program": "food-assistance",
        "program_label": "Food assistance (illustrative, modeled loosely on SNAP)",
        "base_monthly_income_limit": 1600,
        "per_additional_person": 560,
    },
    "health-coverage": {
        "program": "health-coverage",
        "program_label": "Health coverage (illustrative, modeled loosely on Medicaid)",
        "base_monthly_income_limit": 1800,
        "per_additional_person": 640,
        # Illustrative categorical fast-track factors for the screening only.
        "categorical_factors": ["is_pregnant", "has_disability", "has_children"],
    },
}


def _screen(program_rules, household):
    """Return a synthetic screening result dict for one program + household.

    Screening logic is deliberately simple and illustrative: compute a
    household-size-adjusted income limit, compare the household's monthly
    income, and (for health-coverage) allow a categorical fast-track to
    "needs review" when income is over the limit but a categorical factor is
    present. This is a screening, not a determination.
    """
    size = household.get("household_size", 1)
    income = household.get("monthly_income", 0)
    limit = program_rules["base_monthly_income_limit"] + program_rules["per_additional_person"] * max(size - 1, 0)

    within_income = income <= limit

    categorical_hit = False
    for factor in program_rules.get("categorical_factors", []):
        if household.get(factor):
            categorical_hit = True
            break

    if within_income:
        result = "likely-eligible"
        basis = "Household monthly income is at or below the illustrative size-adjusted limit."
    elif categorical_hit:
        result = "needs-review"
        basis = (
            "Household monthly income is above the illustrative limit, but a categorical "
            "factor may still support eligibility; a navigator should review."
        )
    else:
        result = "likely-ineligible"
        basis = "Household monthly income is above the illustrative size-adjusted limit."

    return {
        "program": program_rules["program"],
        "program_label": program_rules["program_label"],
        "screening_result": result,
        "illustrative_income_limit_monthly": limit,
        "household_monthly_income": income,
        "household_size": size,
        "basis": basis,
        "disclaimer": (
            "Preliminary synthetic screening only, not an official eligibility "
            "determination. The administering agency makes the final decision."
        ),
    }


def lambda_handler(event, context):
    """Handle an eligibility-engine tool call routed here by AgentCore Gateway.

    Event: {
        "program": "food-assistance" | "health-coverage",
        "household_size": int,
        "monthly_income": number,
        # optional categorical factors, as returned by household-lookup:
        "is_pregnant": bool, "has_disability": bool, "has_children": bool
    }
    """
    tool_name = ""
    try:
        original_tool_name = context.client_context.custom["bedrockAgentCoreToolName"]
        tool_name = original_tool_name[original_tool_name.index(DELIMITER) + len(DELIMITER):]
    except (AttributeError, KeyError, ValueError):
        tool_name = event.pop("tool_name", "eligibility_engine")

    if tool_name != "eligibility_engine":
        return {"error": f"Unknown tool: {tool_name}", "available_tools": ["eligibility_engine"]}

    program = event.get("program", "")
    program_rules = SYNTHETIC_PROGRAMS.get(program)
    if program_rules is None:
        return {
            "error": f"No synthetic program exists for program={program!r}.",
            "available_programs": list(SYNTHETIC_PROGRAMS.keys()),
        }

    # The navigator agent passes the household attributes returned by
    # household-lookup; default missing ones conservatively.
    household = {
        "household_size": event.get("household_size", 1),
        "monthly_income": event.get("monthly_income", 0),
        "is_pregnant": event.get("is_pregnant", False),
        "has_disability": event.get("has_disability", False),
        "has_children": event.get("has_children", False),
    }
    return _screen(program_rules, household)
