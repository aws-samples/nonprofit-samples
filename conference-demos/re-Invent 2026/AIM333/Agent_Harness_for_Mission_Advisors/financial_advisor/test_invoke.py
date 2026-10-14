# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""
Manual test script for the Stage 1 financial-advisor Harness + Gateway.

Invokes the already-deployed Harness with the Gateway explicitly attached via
the `tools` parameter, so the model can actually call the member-lookup tool
— unlike some console test surfaces, which may invoke the Harness without a
Gateway bound and therefore cannot call any tool at all.

Usage:
    python test_invoke.py "Look up member-101 and summarize their goal."
    python test_invoke.py --member-id member-102
    python test_invoke.py --raw-events "Look up member-999."
    python test_invoke.py "..." --actor-id advisor-a --session-id ...

If no prompt is given, a default prompt asking about member-101 is used.

Actor scoping: `--actor-id` defaults to "default" (matching invoke_harness's
own default when the parameter is omitted). AgentCore Memory's SEMANTIC
strategy extracts long-term facts per *actor*, not per session
(/actors/{actorId}/facts/) — every test run that omits --actor-id shares one
growing fact store, so session-isolation testing (Stage 4) is only valid
when different scenarios use different --actor-id values.
"""

import argparse
import sys
import uuid

import boto3

import config


def build_message(args: argparse.Namespace) -> str:
    if args.prompt:
        return args.prompt
    if args.goal:
        member_id = args.member_id or ("member-101" if args.goal == "retirement" else "member-102")
        return (
            f"Look up synthetic member {member_id}, find the relevant rate for their goal, "
            "and use the plan library to build a full action plan with milestones."
        )
    member_id = args.member_id or "member-101"
    return (
        f"Look up synthetic member {member_id} and summarize their stated "
        "financial goal and the other details returned by the tool."
    )


def resolve_arns() -> tuple[str, str]:
    """Look up the current Harness ARN and Gateway ARN by the names in config.py.

    Avoids hardcoding the random suffix AWS appended at create time
    (e.g. financial_advisor_harness-BxvjnRqVVj), so this script keeps working
    if the resources are ever redeployed under the same names.
    """
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
        sys.exit(f"No harness found named {config.HARNESS_NAME!r}. Run deploy.py first.")

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
        sys.exit(f"No gateway found named {config.GATEWAY_NAME!r}. Run deploy.py first.")

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
        # Scope this invocation to only the financial-advisor tools. Stage 5
        # registers the benefits-navigator targets on this same shared Gateway,
        # so without this the model would see all six tools; this keeps a
        # financial-advisor call scoped to its own three (and trims token
        # overhead of the unused benefits-navigator tool definitions).
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
        description="Test the deployed financial-advisor Harness with the Gateway attached."
    )
    parser.add_argument("prompt", nargs="?", default=None, help="Prompt to send. Overrides --member-id.")
    parser.add_argument("--member-id", default=None, help="Shortcut: build a member-lookup prompt for this ID.")
    parser.add_argument("--goal", choices=["retirement", "home-buying"], default=None,
                         help="Shortcut: build a full multi-tool plan prompt for this goal.")
    parser.add_argument("--session-id", default=None, help="Reuse a session ID to continue a conversation.")
    parser.add_argument(
        "--actor-id",
        default="default",
        help=(
            "Actor ID to scope this invocation's long-term memory under. AgentCore Memory's "
            "SEMANTIC strategy extracts durable facts per actor (namespace "
            "/actors/{actorId}/facts/), not per session — every invocation defaults to the "
            "same 'default' actor unless this is set, so distinct advisors/scenarios sharing "
            "the default actor will see each other's extracted facts even across unrelated "
            "sessions. Set a distinct value per simulated advisor/member to test real isolation."
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
    print(f"Prompt:  {message}\n")

    client = boto3.client("bedrock-agentcore", region_name=config.AWS_REGION)
    stream_response(client, harness_arn, gateway_arn, skill_uri, message, session_id, args.actor_id, args.raw_events)


if __name__ == "__main__":
    main()
