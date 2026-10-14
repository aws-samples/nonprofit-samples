# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Shared configuration for the benefits-navigator AgentCore Gateway/Harness demo.

Stage 5 (domain swap). The Harness, Gateway, Policy Engine, and Guardrail are
the reusable, domain-neutral platform layer — they are created by the
financial-advisor deploy and REUSED here by name, not recreated. This domain
adds only its own Lambdas, its own Gateway targets (registered on the SAME
Gateway alongside the financial-advisor targets), its own S3 skill prefix, and
its own Cedar policies (added to the SAME Policy Engine). Nothing financial-
advisor created is renamed or deleted.

No account IDs, role ARNs, or other account-specific values are hardcoded here.
Region and naming are resolved from environment variables or derived at
runtime (see deploy.py's use of boto3 STS for the account ID).
"""

import os

AWS_REGION = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION") or "us-east-1"

# ---------------------------------------------------------------------------
# SHARED platform resources — must match demo/financial_advisor/config.py
# exactly. These are looked up by name and reused, never created here.
# ---------------------------------------------------------------------------
GATEWAY_NAME = "advisor-gateway"
GATEWAY_ROLE_NAME = "advisor-gateway-role"
HARNESS_NAME = "advisor_harness"
HARNESS_ROLE_NAME = "advisor-harness-role"
POLICY_ENGINE_NAME = "advisor_policy_engine"
# The Guardrail (content safety: tax/legal/medical) is domain-neutral — a
# benefits navigator shouldn't give tax/legal/medical advice any more than a
# financial advisor should — so the same Guardrail is reused as-is.
GUARDRAIL_NAME = "advisor-scope-guardrail"
GUARDRAIL_VERSION = "1"

# ---------------------------------------------------------------------------
# Domain-specific resources for benefits-navigator. These are additive on top
# of the shared platform layer.
# ---------------------------------------------------------------------------
# Its own Lambda execution role, separate from financial-advisor's, so the two
# domains' Lambdas stay independently cleanable.
LAMBDA_ROLE_NAME = "benefits-navigator-lambda-role"

# One entry per Gateway target -> (Lambda function name, handler module under
# lambda/, tool schema file under lambda/). Target names are distinct from the
# financial-advisor targets so both sets coexist on the same Gateway.
TARGETS = {
    "household-lookup": {
        "lambda_function_name": "benefits-navigator-household-lookup",
        "handler_module": "household_lookup",
        "handler_function": "lambda_handler",
        "tool_schemas_file": "household_lookup_tool_schemas.json",
    },
    "eligibility-engine": {
        "lambda_function_name": "benefits-navigator-eligibility-engine",
        "handler_module": "eligibility_engine",
        "handler_function": "lambda_handler",
        "tool_schemas_file": "eligibility_engine_tool_schemas.json",
    },
    "program-library": {
        "lambda_function_name": "benefits-navigator-program-library",
        "handler_module": "program_library",
        "handler_function": "lambda_handler",
        "tool_schemas_file": "program_library_tool_schemas.json",
    },
}

MODEL_ID = os.environ.get("MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0")

_THIS_DIR = os.path.dirname(__file__)
LAMBDA_DIR = os.path.join(_THIS_DIR, "lambda")

# S3-based skill. Reuses the SAME bucket as financial-advisor (one demo skill
# bucket per account) but a distinct, versioned per-domain prefix, so both
# domains' reviewed skill artifacts live side by side and the swap is just a
# change of which prefix the Harness loads.
SKILL_S3_PREFIX = "benefits-navigator/v1/"
SKILL_LOCAL_DIR = os.path.join(_THIS_DIR, "skills", "benefits-navigator")


def skill_bucket_name(account_id: str) -> str:
    return f"aim333-advisor-demo-skills-{account_id}"


def skill_s3_uri(account_id: str) -> str:
    return f"s3://{skill_bucket_name(account_id)}/{SKILL_S3_PREFIX}"


# The glob patterns that scope a Harness invocation to ONLY this domain's
# tools, per harness-tools.md's allowedTools. Used at swap time so the
# benefits-navigator invocation cannot see the financial-advisor targets even
# though both are registered on the same Gateway.
#
# Pattern syntax (from harness-tools.md): tools from a named tool source use
# "@server/tool", where "server" is the attached tool's NAME on the Harness.
# financial-advisor's attach_gateway_to_harness attaches the Gateway under the
# name "gateway", so the source is "@gateway". The tool part is the Gateway's
# own tool name, "{target_name}___{tool_name}" (per gateway-tool-naming.md).
# A per-target glob (…___*) keeps it robust if a target ever exposes more than
# one tool.
ALLOWED_TOOLS = [
    "@gateway/household-lookup___*",
    "@gateway/eligibility-engine___*",
    "@gateway/program-library___*",
]
