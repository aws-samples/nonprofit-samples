# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""
Manual test script for the Stage 5 benefits-navigator domain swap.

Invokes the SHARED Harness with the SHARED Gateway attached, plus:
  - this domain's S3 skill URI (skills=[...]), and
  - an allowedTools allowlist scoped to only the benefits-navigator targets.

Together those two are the domain swap: the financial-advisor targets are still
registered on the same Gateway, but allowedTools hides them from this
invocation, and the benefits-navigator skill drives the workflow. Swapping back
to financial-advisor is just a different skill URI + allowedTools on the same
plumbing (see demo/financial_advisor/test_invoke.py).

Usage:
    python test_invoke.py "Screen household-201 for their program and build a plan."
    python test_invoke.py --household-id household-202
    python test_invoke.py --program food-assistance --household-id household-201
    python test_invoke.py "..." --actor-id navigator-a --session-id ...
    python test_invoke.py --raw-events "Look up household-201."

If no prompt is given, a default prompt asking about household-201 is used.

Actor scoping: --actor-id defaults to "default" (matching invoke_harness's own
default). AgentCore Memory's SEMANTIC strategy extracts long-term facts per
actor (/actors/{actorId}/facts/), not per session, so use a distinct
--actor-id per scenario when isolation matters (see the Stage 4 notes in
content/AIM333-content-plan.html).
"""

import argparse
import sys
import uuid

import boto3

import config


def build_message(args: argparse.Namespace) -> str:
    if args.prompt:
        return args.prompt
    if args.program:
        household_id = args.household_id or (
            "household-201" if args.program == "food-assistance" else "household-202"
        )
        return (
            f"Look up synthetic household {household_id}, screen their likely eligibility "
            f"for {args.program}, and use the program library to build a full enrollment "
            "action plan with milestones."
        )
    household_id = args.household_id or "household-201"
    return (
        f"Look up synthetic household {household_id}, screen their likely eligibility for "
        "their program of interest, and use the program library to build a full enrollment "
        "action plan with milestones."
    )


def resolve_arns() -> tuple[str, str]:
    """Look up the shared Harness ARN and Gateway ARN by the names in config.py."""
    control = boto3.client("bedrock-agentcore-control", region_name=config.AWS_REGION)

    harness_arn = None
    for page in control.get_paginator("list_harnesses").paginate():
        for h in page.get("harnesses", []):
            if h.get("harnessName") == config.HARNESS_NAME:
                harness_arn = h["arn"]
                break
        if harness_arn:
            break
    if not harness_arn:
        sys.exit(
            f"No harness found named {config.HARNESS_NAME!r}. Run "
            f"demo/financial_advisor/deploy.py then demo/benefits_navigator/deploy.py first."
        )

    gateway_arn = None
    for page in control.get_paginator("list_gateways").paginate():
        for gw in page.get("items", []):
            if gw.get("name") == config.GATEWAY_NAME:
                detail = control.get_gateway(gatewayIdentifier=gw["gatewayId"])
                gateway_arn = detail["gatewayArn"]
                break
        if gateway_arn:
            break
    if not gateway_arn:
        sys.exit(
            f"No gateway found named {config.GATEWAY_NAME!r}. Run "
            f"demo/financial_advisor/deploy.py then demo/benefits_navigator/deploy.py first."
        )

    return harness_arn, gateway_arn


def stream_response(client, harness_arn: str, gateway_arn: str, skill_uri: str, message: str,
                     session_id: str, actor_id: str, raw: bool) -> None:
    response = client.invoke_harness(
        harnessArn=harness_arn,
        runtimeSessionId=session_id,
        actorId=actor_id,
        messages=[{"role": "user", "content": [{"text": message}]}],
        model={"bedrockModelConfig": {"modelId": config.MODEL_ID}},
        tools=[
            {
                "type": "agentcore_gateway",
                "name": "gateway",
                "config": {"agentCoreGateway": {"gatewayArn": gateway_arn}},
            }
        ],
        skills=[{"s3": {"uri": skill_uri}}],
        # Domain swap: scope this invocation to only the benefits-navigator
        # tools, even though the financial-advisor targets are also on this
        # Gateway.
        allowedTools=config.ALLOWED_TOOLS,
    )

    for event in response["stream"]:
        if raw:
            import json

            print(json.dumps(event, default=str))
            continue
        if "contentBlockStart" in event:
            start = event["contentBlockStart"].get("start", {})
            if "toolUse" in start:
                print(f"\n  [Tool: {start['toolUse'].get('name', '?')}]", flush=True)
        elif "contentBlockDelta" in event:
            delta = event["contentBlockDelta"].get("delta", {})
            if "text" in delta:
                print(delta["text"], end="", flush=True)
        elif "messageStop" in event:
            print()
        elif "internalServerException" in event:
            print(f"\n  Error: {event['internalServerException']}")
        elif "validationException" in event:
            print(f"\n  Validation error: {event['validationException']}")
        elif "runtimeClientError" in event:
            print(f"\n  Runtime error: {event['runtimeClientError']}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test the benefits-navigator domain swap on the shared Harness + Gateway."
    )
    parser.add_argument("prompt", nargs="?", default=None, help="Prompt to send. Overrides --household-id.")
    parser.add_argument("--household-id", default=None, help="Shortcut: build a lookup prompt for this household ID.")
    parser.add_argument("--program", choices=["food-assistance", "health-coverage"], default=None,
                         help="Shortcut: build a full screening+plan prompt for this program.")
    parser.add_argument("--session-id", default=None, help="Reuse a session ID to continue a conversation.")
    parser.add_argument(
        "--actor-id",
        default="default",
        help=(
            "Actor ID to scope this invocation's long-term memory under. Defaults to "
            "'default'. Set a distinct value per simulated navigator/scenario to keep "
            "extracted facts isolated (see Stage 4 notes)."
        ),
    )
    parser.add_argument("--raw-events", action="store_true", help="Print raw stream events instead of just text.")
    args = parser.parse_args()

    harness_arn, gateway_arn = resolve_arns()
    account_id = harness_arn.split(":")[4]
    skill_uri = config.skill_s3_uri(account_id)
    message = build_message(args)
    session_id = args.session_id or str(uuid.uuid4()).upper()

    print(f"Harness:      {harness_arn}")
    print(f"Gateway:      {gateway_arn}")
    print(f"Skill:        {skill_uri}")
    print(f"allowedTools: {config.ALLOWED_TOOLS}")
    print(f"Session:      {session_id}")
    print(f"Actor:        {args.actor_id}")
    print(f"Prompt:       {message}\n")

    client = boto3.client("bedrock-agentcore", region_name=config.AWS_REGION)
    stream_response(client, harness_arn, gateway_arn, skill_uri, message, session_id, args.actor_id, args.raw_events)


if __name__ == "__main__":
    main()
