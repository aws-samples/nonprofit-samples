# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Shared configuration for the financial-advisor AgentCore Gateway/Harness demo.

No account IDs, role ARNs, or other account-specific values are hardcoded here.
Region and naming are resolved from environment variables or derived at
runtime (see deploy.py's use of boto3 STS for the account ID).
"""

import os

AWS_REGION = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION") or "us-east-1"

# The Harness and Gateway are the reusable, domain-neutral platform layer —
# the same two resources are meant to keep working across a domain swap
# (financial-advisor -> benefits-navigator), with only the Gateway's targets
# and the Harness's skill changing. Naming them "advisor_*"/"advisor-*"
# rather than "financial-advisor-*" reflects that; only the Lambda, target,
# skill directory, and S3 skill prefix are meant to be domain-specific.
GATEWAY_NAME = "advisor-gateway"
GATEWAY_ROLE_NAME = "advisor-gateway-role"
# Harness names must match [a-zA-Z][a-zA-Z0-9_]{0,39} — letters, digits, and
# underscores only, no hyphens, unlike Lambda/Gateway/IAM names below.
HARNESS_NAME = "advisor_harness"
HARNESS_ROLE_NAME = "advisor-harness-role"

# Stage 3: Cedar-based tool-call authorization. The policy engine's
# association with a gateway lives entirely on the gateway side
# (policyEngineConfiguration) — the engine has no record of its own
# callers, and deleting a gateway does NOT delete or detach the policy
# engine. cleanup.py deletes this explicitly as its own step.
POLICY_ENGINE_NAME = "advisor_policy_engine"

# Stage 3: Bedrock Guardrail (content safety). Blocks tax/legal/medical
# topics, matching skills/financial-advisor/SKILL.md's existing
# out-of-scope rule. Guardrails require a numbered version (not DRAFT) to
# attach to a Harness invocation.
GUARDRAIL_NAME = "advisor-scope-guardrail"
GUARDRAIL_VERSION = "1"

# Domain-specific: Gateway targets and their backing Lambdas, for the
# financial-advisor domain. A domain swap (Stage 5) would add a different
# set of targets/Lambdas for benefits-navigator rather than renaming these.
LAMBDA_ROLE_NAME = "financial-advisor-lambda-role"

# One entry per Gateway target: target name -> (Lambda function name, handler
# module name under lambda/, tool schema file under lambda/).
TARGETS = {
    "member-lookup": {
        "lambda_function_name": "financial-advisor-member-lookup",
        "handler_module": "member_lookup",
        "handler_function": "lambda_handler",
        "tool_schemas_file": "tool_schemas.json",
    },
    "rate-engine": {
        "lambda_function_name": "financial-advisor-rate-engine",
        "handler_module": "rate_engine",
        "handler_function": "lambda_handler",
        "tool_schemas_file": "rate_engine_tool_schemas.json",
    },
    "advisor-library": {
        "lambda_function_name": "financial-advisor-advisor-library",
        "handler_module": "advisor_library",
        "handler_function": "lambda_handler",
        "tool_schemas_file": "advisor_library_tool_schemas.json",
    },
}

# Kept for backward compatibility with test_invoke.py and any script that
# still refers to a single target; Stage 1 only had one.
GATEWAY_TARGET_NAME = "member-lookup"

MODEL_ID = os.environ.get("MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0")

_THIS_DIR = os.path.dirname(__file__)
LAMBDA_DIR = os.path.join(_THIS_DIR, "lambda")
STATE_FILE = os.path.join(_THIS_DIR, ".deploy_state.json")

# S3-based skill (see steering: "S3-based Harness domain skills"). The
# bucket name is derived from the account ID at call time (skill_bucket_name
# below), never hardcoded, matching every other resource name in this file.
# SKILL_S3_PREFIX is release-specific ("v1") rather than a mutable "latest"
# path, so rehearsals and any published demo always load a reviewed,
# versioned skill artifact rather than whatever happens to be on disk at
# upload time.
SKILL_S3_PREFIX = "financial-advisor/v1/"
SKILL_LOCAL_DIR = os.path.join(_THIS_DIR, "skills", "financial-advisor")


def skill_bucket_name(account_id: str) -> str:
    return f"aim333-advisor-demo-skills-{account_id}"


def skill_s3_uri(account_id: str) -> str:
    return f"s3://{skill_bucket_name(account_id)}/{SKILL_S3_PREFIX}"


# The glob patterns that scope a Harness invocation to ONLY the financial-
# advisor tools, per harness-tools.md's allowedTools. Symmetric with
# demo/benefits_navigator/config.py's ALLOWED_TOOLS. Since Stage 5 registers
# BOTH domains' targets on the same shared Gateway, an invocation with no
# allowedTools would see all six tools (both domains); this allowlist keeps a
# financial-advisor invocation scoped to only its own three tools, and trims
# the token overhead of the benefits-navigator tool definitions it never uses.
#
# Syntax (harness-tools.md): "@server/tool", where "server" is the attached
# tool's NAME on the Harness ("gateway", set by attach_gateway_to_harness) and
# the tool part is the Gateway tool name "{target_name}___{tool_name}"
# (gateway-tool-naming.md). A per-target glob (…___*) is robust if a target
# ever exposes more than one tool.
#
# NOTE: this is model-visibility scoping, not authorization — the Cedar
# policies on the Gateway (Stage 3) are the actual security boundary.
ALLOWED_TOOLS = [
    "@gateway/member-lookup___*",
    "@gateway/rate-engine___*",
    "@gateway/advisor-library___*",
]
