# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""
Stage 1: connect the first AgentCore Gateway tool target to a Harness.

Deploys a synthetic member-lookup Lambda, registers it as an AgentCore
Gateway MCP target, creates a Harness, and invokes it once to confirm the
agent can call the tool and return a synthetic member record.

Uses only synthetic data (see lambda/member_lookup.py). No real member data,
credentials, or account-specific configuration is stored anywhere in this
file or its dependencies.

Usage:
    python deploy.py

Resources created (see cleanup.py to remove them):
    - IAM role for the Lambda's own execution
    - IAM role for the Gateway (scoped to invoke only the demo's Lambdas)
    - IAM role for the Harness execution
    - Lambda functions: member-lookup, rate-engine, advisor-library
    - AgentCore Gateway (protocolType=MCP, authorizerType=AWS_IAM)
    - AgentCore Gateway targets (Lambda MCP targets, credential type GATEWAY_IAM_ROLE)
    - AgentCore Harness
    - S3 bucket holding the versioned financial-advisor skill artifact

Prerequisites:
    - AWS CLI configured with credentials
    - AWS_DEFAULT_REGION set (or pass via config.py's default)
    - Model access enabled for the model in config.MODEL_ID
"""

import io
import json
import time
import uuid
import zipfile

import boto3

import config
from aws_helpers import (
    create_gateway_role,
    create_guardrail,
    create_harness_role,
    create_lambda_role,
    create_skill_bucket,
    get_account_id,
    grant_gateway_policy_engine_permissions,
    grant_harness_guardrail_permission,
    grant_skill_bucket_read,
    poll_gateway_status,
    poll_harness_status,
    poll_policy_engine_status,
    poll_policy_status,
    poll_target_status,
    upload_skill_directory,
)


def deploy_lambda(lambda_role_arn: str, target_name: str, target_config: dict) -> str:
    fn_name = target_config["lambda_function_name"]
    handler_module = target_config["handler_module"]
    handler_function = target_config["handler_function"]

    print("=" * 60)
    print(f"Step 1: Deploy {target_name} Lambda ({fn_name})")
    print("=" * 60)

    lam = boto3.client("lambda", region_name=config.AWS_REGION)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(f"{config.LAMBDA_DIR}/{handler_module}.py", f"{handler_module}.py")
    buf.seek(0)

    try:
        fn_arn = lam.get_function(FunctionName=fn_name)["Configuration"]["FunctionArn"]
        buf.seek(0)
        lam.update_function_code(FunctionName=fn_name, ZipFile=buf.read())
        print(f"  Updated existing Lambda: {fn_name}")
    except lam.exceptions.ResourceNotFoundException:
        print("  Waiting for IAM propagation...")
        time.sleep(10)
        fn_arn = lam.create_function(
            FunctionName=fn_name,
            Runtime="python3.12",
            Role=lambda_role_arn,
            Handler=f"{handler_module}.{handler_function}",
            Code={"ZipFile": buf.read()},
            Description=f"Synthetic {target_name} tool for the financial-advisor AgentCore Gateway demo.",
            Timeout=30,
        )["FunctionArn"]
        print(f"  Created Lambda: {fn_name}")

    lam.get_waiter("function_active_v2").wait(FunctionName=fn_name)
    print(f"  Lambda ARN: {fn_arn}")
    print("  Lambda is Active")
    return fn_arn


def create_gateway(gateway_role_arn: str):
    print("\n" + "=" * 60)
    print("Step 2: Create Gateway (authorizerType=AWS_IAM)")
    print("=" * 60)

    gw_control = boto3.client("bedrock-agentcore-control", region_name=config.AWS_REGION)

    try:
        resp = gw_control.create_gateway(
            name=config.GATEWAY_NAME,
            roleArn=gateway_role_arn,
            protocolType="MCP",
            # Inbound: IAM-authenticated callers only (Stage 3). The gateway
            # validates the caller's SigV4 signature and requires
            # bedrock-agentcore:InvokeGateway scoped to this gateway's ARN.
            # In this architecture the Harness is the only caller of
            # InvokeGateway, so every request presents the same
            # AgentCore::IamEntity principal (the harness execution role) —
            # there is no per-end-user identity to distinguish advisor vs.
            # viewer here. Cedar policy conditions in Stage 3 are written
            # against context.input (tool call arguments) instead.
            authorizerType="AWS_IAM",
            description="Financial-advisor demo Gateway (Stage 1: member-lookup target).",
        )
        gateway_id = resp["gatewayId"]
        gateway_arn = resp["gatewayArn"]
        print(f"  Created gateway: {gateway_id}")
    except gw_control.exceptions.ConflictException:
        gateway_id = gateway_arn = None
        for page in gw_control.get_paginator("list_gateways").paginate():
            for gw in page.get("items", []):
                if gw.get("name") == config.GATEWAY_NAME:
                    detail = gw_control.get_gateway(gatewayIdentifier=gw["gatewayId"])
                    gateway_id = detail["gatewayId"]
                    gateway_arn = detail["gatewayArn"]
                    break
            if gateway_id:
                break
        if not gateway_id:
            raise RuntimeError(f"Gateway {config.GATEWAY_NAME} conflict but not found")
        print(f"  Gateway already exists: {gateway_id}")

        if detail.get("authorizerType") != "AWS_IAM":
            # authorizerType is immutable on an existing gateway
            # (UpdateGateway rejects it with "Authorizer type cannot be
            # updated for an existing gateway" — confirmed against the live
            # API, not just docs). Migrating an existing NONE-auth gateway
            # to AWS_IAM for Stage 3 requires deleting and recreating the
            # gateway. Its targets must be deleted first (a gateway with
            # targets cannot be deleted), then recreated by the caller after
            # this function returns — main() already does this for every
            # target on every run, so nothing else changes.
            print(f"  authorizerType is {detail.get('authorizerType')!r}, need 'AWS_IAM'.")
            print("  authorizerType is immutable — deleting and recreating the gateway...")
            target_ids = []
            for page in gw_control.get_paginator("list_gateway_targets").paginate(gatewayIdentifier=gateway_id):
                for tgt in page.get("items", []):
                    gw_control.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=tgt["targetId"])
                    target_ids.append(tgt["targetId"])
                    print(f"    Deleted target: {tgt['name']} ({tgt['targetId']})")

            # Target deletion is async — DeleteGateway rejects with
            # ValidationException (not ConflictException) if any target is
            # still listed as associated, so wait until list_gateway_targets
            # reports empty before trying to delete the gateway itself.
            deadline = time.monotonic() + 300
            while target_ids:
                time.sleep(5)
                remaining = set()
                for page in gw_control.get_paginator("list_gateway_targets").paginate(gatewayIdentifier=gateway_id):
                    remaining.update(t["targetId"] for t in page.get("items", []))
                if not remaining:
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError(f"Targets still listed after 300s: {remaining}")

            deadline = time.monotonic() + 300
            while True:
                try:
                    gw_control.delete_gateway(gatewayIdentifier=gateway_id)
                    print(f"  Deleted gateway: {gateway_id}")
                    break
                except (gw_control.exceptions.ConflictException, gw_control.exceptions.ValidationException):
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(5)

            resp = gw_control.create_gateway(
                name=config.GATEWAY_NAME,
                roleArn=gateway_role_arn,
                protocolType="MCP",
                authorizerType="AWS_IAM",
                description="Financial-advisor demo Gateway (Stage 1: member-lookup target).",
            )
            gateway_id = resp["gatewayId"]
            gateway_arn = resp["gatewayArn"]
            print(f"  Recreated gateway with authorizerType=AWS_IAM: {gateway_id}")

    print(f"  Gateway ARN: {gateway_arn}")
    poll_gateway_status(gw_control, gateway_id)
    return gw_control, gateway_id, gateway_arn


def create_gateway_target(gw_control, gateway_id: str, target_name: str, target_config: dict,
                           lambda_arn: str, wait_for_propagation: bool) -> str:
    print("\n" + "=" * 60)
    print(f"Step 3: Register {target_name} as a Gateway target")
    print("=" * 60)

    if wait_for_propagation:
        # The Gateway role's trust policy was just created/updated moments ago
        # in create_gateway_role(). IAM propagation can lag a few seconds
        # behind the role-creation API call returning, and CreateGatewayTarget
        # is the first place the Gateway service actually tries to assume
        # that role — so a ValidationException ("not authorized to perform
        # AssumeRole") here is usually a timing issue, not a policy bug.
        print("  Waiting for Gateway role trust-policy propagation...")
        time.sleep(15)

    with open(f"{config.LAMBDA_DIR}/{target_config['tool_schemas_file']}") as f:
        tool_schemas = json.load(f)

    try:
        resp = gw_control.create_gateway_target(
            gatewayIdentifier=gateway_id,
            name=target_name,
            targetConfiguration={
                "mcp": {
                    "lambda": {
                        "lambdaArn": lambda_arn,
                        "toolSchema": {"inlinePayload": tool_schemas},
                    }
                }
            },
            credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
            description=f"Synthetic {target_name} tool.",
        )
        target_id = resp["targetId"]
        print(f"  Created target: {target_id}")
    except gw_control.exceptions.ConflictException:
        target_id = None
        for page in gw_control.get_paginator("list_gateway_targets").paginate(gatewayIdentifier=gateway_id):
            for tgt in page.get("items", []):
                if tgt.get("name") == target_name:
                    target_id = tgt["targetId"]
                    break
            if target_id:
                break
        if not target_id:
            raise RuntimeError(f"Target {target_name} conflict but not found")
        print(f"  Target already exists: {target_id}")

    poll_target_status(gw_control, gateway_id, target_id)
    return target_id


def _cedar_policy_statements(gateway_arn: str) -> dict:
    """Cedar policy statements enforcing domain scope on each Gateway
    target's arguments (context.input), not on caller identity
    (principal) — see deploy.py's create_gateway() docstring for why:
    the Harness is this architecture's only caller of InvokeGateway, so
    every request presents the same AgentCore::IamEntity principal.

    Cedar is default-deny with no wildcard resources, so these three
    `permit` statements (one per tool) are both necessary and sufficient:
    any tool call not matching one of them — including any future fourth
    tool — is denied automatically, with no `forbid` statements needed.
    """
    return {
        "MemberLookupScope": (
            "permit(\n"
            "    principal,\n"
            '    action == AgentCore::Action::"member-lookup___member_lookup",\n'
            f'    resource == AgentCore::Gateway::"{gateway_arn}"\n'
            ")\n"
            "when {\n"
            '    context.input.member_id == "member-101" ||\n'
            '    context.input.member_id == "member-102"\n'
            "};"
        ),
        "RateEngineScope": (
            "permit(\n"
            "    principal,\n"
            '    action == AgentCore::Action::"rate-engine___rate_engine",\n'
            f'    resource == AgentCore::Gateway::"{gateway_arn}"\n'
            ")\n"
            "when {\n"
            '    context.input.purpose == "retirement-savings" ||\n'
            '    context.input.purpose == "home-purchase"\n'
            "};"
        ),
        "AdvisorLibraryScope": (
            "permit(\n"
            "    principal,\n"
            '    action == AgentCore::Action::"advisor-library___advisor_library",\n'
            f'    resource == AgentCore::Gateway::"{gateway_arn}"\n'
            ")\n"
            "when {\n"
            '    context.input.template_id == "retirement-readiness" ||\n'
            '    context.input.template_id == "home-buying-readiness"\n'
            "};"
        ),
    }


def create_policy_engine_and_policies(gateway_role_name: str, gateway_arn: str):
    """Create the Policy Engine and its 3 Cedar policies (idempotent —
    reuses existing resources by name/statement on rerun), grant the
    Gateway's own role the 3 required permissions, then attach the engine
    to the Gateway. Starts in LOG_ONLY at the engine level; promote to
    ENFORCE manually once you've confirmed the allow/deny paths (see
    content/AIM333-content-plan.html for the tested procedure).
    """
    print("\n" + "=" * 60)
    print("Step 6: Create AgentCore Policy Engine + Cedar policies (Stage 3)")
    print("=" * 60)

    control = boto3.client("bedrock-agentcore-control", region_name=config.AWS_REGION)

    try:
        resp = control.create_policy_engine(
            name=config.POLICY_ENGINE_NAME,
            description="Cedar-based tool-call authorization for the financial-advisor demo Gateway (Stage 3).",
        )
        policy_engine_id = resp["policyEngineId"]
        policy_engine_arn = resp["policyEngineArn"]
        print(f"  Created policy engine: {policy_engine_id}")
    except control.exceptions.ConflictException:
        policy_engine_id = policy_engine_arn = None
        for page in control.get_paginator("list_policy_engines").paginate():
            for pe in page.get("policyEngines", []):
                if pe.get("name") == config.POLICY_ENGINE_NAME:
                    policy_engine_id = pe["policyEngineId"]
                    policy_engine_arn = pe["policyEngineArn"]
                    break
            if policy_engine_id:
                break
        if not policy_engine_id:
            raise RuntimeError(f"Policy engine {config.POLICY_ENGINE_NAME} conflict but not found")
        print(f"  Policy engine already exists: {policy_engine_id}")

    poll_policy_engine_status(control, policy_engine_id)

    for name, statement in _cedar_policy_statements(gateway_arn).items():
        try:
            resp = control.create_policy(
                policyEngineId=policy_engine_id,
                name=name,
                definition={"cedar": {"statement": statement}},
            )
            policy_id = resp["policyId"]
            print(f"  Created policy: {name} ({policy_id})")
        except control.exceptions.ConflictException:
            policy_id = None
            for page in control.get_paginator("list_policies").paginate(policyEngineId=policy_engine_id):
                for pol in page.get("policies", []):
                    if pol.get("name") == name:
                        policy_id = pol["policyId"]
                        break
                if policy_id:
                    break
            if not policy_id:
                raise RuntimeError(f"Policy {name} conflict but not found")
            print(f"  Policy already exists: {name} ({policy_id})")
        poll_policy_status(control, policy_engine_id, policy_id)

    grant_gateway_policy_engine_permissions(gateway_role_name, policy_engine_arn, gateway_arn)
    print("  Waiting for Gateway role policy-engine-permission propagation...")
    time.sleep(15)

    return control, policy_engine_id, policy_engine_arn


def attach_policy_engine_to_gateway(gw_control, gateway_id: str, gateway_arn: str, gateway_role_arn: str,
                                     policy_engine_arn: str, mode: str) -> None:
    """Attach the Policy Engine to the Gateway. UpdateGateway requires
    re-sending every field that was set at creation, not just the one
    changing (see the gateway guide's ValidationException note).

    mode: "LOG_ONLY" (evaluate and log, never block — safe default) or
    "ENFORCE" (actively allow/deny). Always verify LOG_ONLY first.
    """
    print("\n" + "=" * 60)
    print(f"Step 6b: Attach Policy Engine to Gateway (mode={mode})")
    print("=" * 60)

    current = gw_control.get_gateway(gatewayIdentifier=gateway_id)
    current_config = current.get("policyEngineConfiguration")
    if current_config and current_config.get("arn") == policy_engine_arn:
        # Never downgrade an already-attached engine's mode automatically —
        # if ENFORCE was manually verified and promoted (see the tested
        # allow/deny procedure), a rerun of this script must not silently
        # revert it to LOG_ONLY just because that's this call's default.
        if current_config.get("mode") == mode or current_config.get("mode") == "ENFORCE":
            print(f"  Policy engine already attached (mode={current_config.get('mode')}) — no change made.")
            return

    gw_control.update_gateway(
        gatewayIdentifier=gateway_id,
        name=config.GATEWAY_NAME,
        roleArn=gateway_role_arn,
        protocolType="MCP",
        authorizerType="AWS_IAM",
        description="Financial-advisor demo Gateway (Stage 1: member-lookup target).",
        policyEngineConfiguration={"arn": policy_engine_arn, "mode": mode},
    )
    poll_gateway_status(gw_control, gateway_id)
    print(f"  Policy engine attached in mode={mode}.")


def create_harness(harness_role_arn: str):
    print("\n" + "=" * 60)
    print("Step 4: Create Harness")
    print("=" * 60)

    # Same IAM propagation timing issue as the Gateway role in
    # create_gateway_target: the harness role's trust policy was just
    # created/refreshed, and CreateHarness is the first place AgentCore
    # actually tries to assume it. A ValidationException here
    # ("Role validation failed... verify the role exists and its trust
    # policy allows assumption") is usually this timing gap, not a bad policy.
    print("  Waiting for Harness role trust-policy propagation...")
    time.sleep(15)

    control = boto3.client("bedrock-agentcore-control", region_name=config.AWS_REGION)
    try:
        resp = control.create_harness(harnessName=config.HARNESS_NAME, executionRoleArn=harness_role_arn)
        harness_id = resp["harness"]["harnessId"]
        harness_arn = resp["harness"]["arn"]
        print(f"  Created harness: {harness_id}")
    except control.exceptions.ConflictException:
        # ListHarnesses returns items under "harnesses", not "items" — the
        # other AgentCore list operations used here (gateways, targets) use
        # "items"; harnesses does not, so this key must not be shared with them.
        harness_id = harness_arn = None
        for page in control.get_paginator("list_harnesses").paginate():
            for h in page.get("harnesses", []):
                if h.get("harnessName") == config.HARNESS_NAME:
                    harness_id = h["harnessId"]
                    harness_arn = h["arn"]
                    break
            if harness_id:
                break
        if not harness_id:
            raise RuntimeError(f"Harness {config.HARNESS_NAME} conflict but not found")
        print(f"  Harness already exists: {harness_id}")

    print(f"  Harness ARN: {harness_arn}")
    poll_harness_status(control, harness_id)
    return control, harness_id, harness_arn


def attach_gateway_to_harness(control, harness_id: str, gateway_arn: str) -> None:
    """Persist the Gateway on the Harness via update_harness.

    invoke_harness's own `tools` parameter only attaches a Gateway for that
    one call. Any other invocation of this Harness that does not pass
    `tools` explicitly — including the AWS Console's built-in test/chat
    surface for an existing Harness — sees no tools at all and cannot call
    member-lookup. Attaching the Gateway here makes it available by default
    on every future invocation of this Harness, not just calls that build
    their own `tools` list.

    update_harness REPLACES the entire `tools` list rather than merging into
    it — passing only this one entry would silently delete any other tool
    (e.g. a future Stage 5 benefits-navigator gateway) already attached to
    this Harness. So this reads the Harness's current `tools` first and
    replaces only the entry named "gateway", keeping every other entry
    untouched.
    """
    print("\n" + "=" * 60)
    print("Step 4b: Persist Gateway on Harness (update_harness)")
    print("=" * 60)

    current_tools = control.get_harness(harnessId=harness_id)["harness"].get("tools", []) or []
    this_tool = {
        "type": "agentcore_gateway",
        "name": "gateway",
        "config": {"agentCoreGateway": {"gatewayArn": gateway_arn}},
    }
    merged_tools = [t for t in current_tools if t.get("name") != "gateway"] + [this_tool]

    if merged_tools == current_tools:
        print("  Gateway already attached with the current ARN — no update needed.")
        return

    control.update_harness(harnessId=harness_id, tools=merged_tools)
    print("  Waiting for harness update...")
    poll_harness_status(control, harness_id)
    print(f"  Gateway is now attached by default ({len(merged_tools)} tool(s) total on this Harness).")


def attach_guardrail_to_harness(control, harness_id: str, guardrail_arn: str, guardrail_version: str) -> None:
    """Persist the Bedrock Guardrail on the Harness via update_harness's
    model.bedrockModelConfig.additionalParams.guardrailConfig, per
    harness-models.html. Sets config.MODEL_ID explicitly at the same time —
    found during Stage 3 that this Harness had a stale model persisted
    from an earlier console session (a different model than config.MODEL_ID),
    left over because nothing had called update_harness with a model
    config before; every invoke_harness call's own model= override had
    been masking the mismatch.

    Idempotent: skips the update if the current persisted model config
    already matches exactly.
    """
    print("\n" + "=" * 60)
    print("Step 6c: Attach Bedrock Guardrail to Harness (update_harness)")
    print("=" * 60)

    desired_model = {
        "bedrockModelConfig": {
            "modelId": config.MODEL_ID,
            "apiFormat": "converse_stream",
            "additionalParams": {
                "guardrailConfig": {
                    "guardrailIdentifier": guardrail_arn,
                    "guardrailVersion": guardrail_version,
                    "trace": "enabled_full",
                }
            },
        }
    }

    current_model = control.get_harness(harnessId=harness_id)["harness"].get("model")
    if current_model == desired_model:
        print("  Guardrail already attached with the current model config — no update needed.")
        return

    control.update_harness(harnessId=harness_id, model=desired_model)
    print("  Waiting for harness update...")
    poll_harness_status(control, harness_id)
    print("  Guardrail is now attached by default for every invocation of this Harness.")


def deploy_skill(account_id: str, harness_role_name: str) -> str:
    """Upload the reviewed skills/financial-advisor/ directory to a
    versioned S3 prefix and grant the Harness execution role read access to
    it, per the steering doc's S3-based Harness domain skills pattern.
    Returns the s3:// URI to pass in the harness `skills` config.
    """
    print("\n" + "=" * 60)
    print("Step 4c: Deploy skill to S3 and grant Harness read access")
    print("=" * 60)

    bucket_name = config.skill_bucket_name(account_id)
    create_skill_bucket(config.AWS_REGION, bucket_name)
    upload_skill_directory(config.AWS_REGION, bucket_name, config.SKILL_S3_PREFIX, config.SKILL_LOCAL_DIR)
    grant_skill_bucket_read(harness_role_name, bucket_name, config.SKILL_S3_PREFIX)

    skill_uri = config.skill_s3_uri(account_id)
    print(f"  Skill URI: {skill_uri}")
    return skill_uri


def attach_skill_to_harness(control, harness_id: str, skill_uri: str) -> None:
    """Persist the S3 skill on the Harness via update_harness, same pattern
    as attach_gateway_to_harness: without this, only invocations that pass
    their own `skills` list (like test_invoke.py) would load it, and the
    AWS Console's test/chat surface would not.

    Same replace-not-merge risk as tools: update_harness REPLACES the whole
    `skills` list. This reads the current list first and only replaces the
    entry whose S3 URI PREFIX matches this skill's (skills don't carry a
    `name` field the way tools do), so a future Stage 5 benefits-navigator
    S3 skill under a different prefix is preserved rather than wiped out.
    """
    print("\n" + "=" * 60)
    print("Step 4d: Persist skill on Harness (update_harness)")
    print("=" * 60)

    current_skills = control.get_harness(harnessId=harness_id)["harness"].get("skills", []) or []
    this_skill = {"s3": {"uri": skill_uri}}

    def _is_this_skill(skill: dict) -> bool:
        return "s3" in skill and skill["s3"].get("uri") == skill_uri

    merged_skills = [s for s in current_skills if not _is_this_skill(s)] + [this_skill]

    if merged_skills == current_skills:
        print("  Skill already attached with the current URI — no update needed.")
        return

    control.update_harness(harnessId=harness_id, skills=merged_skills)
    print("  Waiting for harness update...")
    poll_harness_status(control, harness_id)
    print(f"  Skill is now attached by default ({len(merged_skills)} skill(s) total on this Harness).")


def invoke_and_confirm(harness_arn: str, gateway_arn: str, skill_uri: str) -> None:
    print("\n" + "=" * 60)
    print("Step 5: Invoke Harness (member-lookup via Gateway, skill attached)")
    print("=" * 60)

    client = boto3.client("bedrock-agentcore", region_name=config.AWS_REGION)
    session_id = str(uuid.uuid4()).upper()
    message = (
        "Look up synthetic member member-101, find the relevant rate for their goal, "
        "and use the plan library to build a full action plan with milestones."
    )
    print(f"  Session ID: {session_id}")
    print(f"  Prompt: {message}\n")

    response = client.invoke_harness(
        harnessArn=harness_arn,
        runtimeSessionId=session_id,
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
    )

    for event in response["stream"]:
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

    print("\n" + "=" * 60)
    print("Stage 2 deploy complete (tools + skill). Resources were NOT deleted — use cleanup.py when done reviewing.")
    print("=" * 60)


def main() -> None:
    account_id = get_account_id()
    print(f"Account: {account_id}")
    print(f"Region:  {config.AWS_REGION}")

    lambda_role_arn = create_lambda_role(config.LAMBDA_ROLE_NAME)

    lambda_arns = {}
    for target_name, target_config in config.TARGETS.items():
        lambda_arns[target_name] = deploy_lambda(lambda_role_arn, target_name, target_config)

    gateway_role_arn = create_gateway_role(config.AWS_REGION, config.GATEWAY_ROLE_NAME, list(lambda_arns.values()))
    gw_control, gateway_id, gateway_arn = create_gateway(gateway_role_arn)

    for i, (target_name, target_config) in enumerate(config.TARGETS.items()):
        create_gateway_target(
            gw_control, gateway_id, target_name, target_config,
            lambda_arns[target_name],
            wait_for_propagation=(i == 0),  # only need to wait once, before the first target call
        )

    # Stage 3: Cedar-based tool-call authorization. Attaches in LOG_ONLY —
    # the safe default for a fresh deploy. Promote to ENFORCE manually only
    # after confirming both the allow and deny paths (see the tested
    # procedure and CloudWatch verification steps in
    # content/AIM333-content-plan.html) via:
    #   gw_control.update_gateway(..., policyEngineConfiguration={"arn": policy_engine_arn, "mode": "ENFORCE"})
    _, policy_engine_id, policy_engine_arn = create_policy_engine_and_policies(config.GATEWAY_ROLE_NAME, gateway_arn)
    attach_policy_engine_to_gateway(
        gw_control, gateway_id, gateway_arn, gateway_role_arn, policy_engine_arn, mode="LOG_ONLY"
    )

    # gateway_arn is already known here (create_gateway ran above), so the
    # harness role's InvokeGateway permission is scoped to this specific
    # gateway from the start, per the AWS_IAM security best practice.
    harness_role_arn = create_harness_role(config.AWS_REGION, config.HARNESS_ROLE_NAME, gateway_arn)
    control, harness_id, harness_arn = create_harness(harness_role_arn)
    attach_gateway_to_harness(control, harness_id, gateway_arn)

    skill_uri = deploy_skill(account_id, config.HARNESS_ROLE_NAME)
    attach_skill_to_harness(control, harness_id, skill_uri)

    # Stage 3: Bedrock Guardrail (content safety).
    #
    # RE-ENABLED after root-causing a false-positive regression (see
    # content/AIM333-content-plan.html): the original SKILL.md safety-
    # boundaries paragraph said "Do not answer requests outside this
    # skill's scope (for example, tax, legal, or medical questions)" —
    # phrasing that itself reads as being ABOUT those topics, which
    # tripped the Guardrail's own LegalAdvice topic filter on every turn
    # (the skill is fetched into context on the first tool call and
    # persists for the session). Confirmed via bedrock-runtime.apply_guardrail
    # called directly on that exact paragraph: action="GUARDRAIL_INTERVENED".
    # Reworded to describe the skill's in-scope coverage positively instead
    # of naming the excluded topics in a denial-shaped sentence; re-verified
    # action="NONE" on the new paragraph and the full updated SKILL.md file.
    guardrail_id, guardrail_arn = create_guardrail(config.AWS_REGION, config.GUARDRAIL_NAME, config.GUARDRAIL_VERSION)
    grant_harness_guardrail_permission(config.HARNESS_ROLE_NAME, guardrail_arn)
    print("  Waiting for Harness role guardrail-permission propagation...")
    time.sleep(15)
    attach_guardrail_to_harness(control, harness_id, guardrail_arn, config.GUARDRAIL_VERSION)

    invoke_and_confirm(harness_arn, gateway_arn, skill_uri)


if __name__ == "__main__":
    main()
