# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""
Stage 5: benefits-navigator domain swap.

Adds the benefits-navigator domain ON TOP OF the already-deployed financial-
advisor platform, WITHOUT touching anything financial-advisor created. Run
demo/financial_advisor/deploy.py first — this script looks up the shared
Gateway, Harness, Policy Engine, and skill bucket by name and fails clearly if
they don't exist yet.

What this script does (all additive):
    1. Deploy the 3 benefits-navigator Lambdas (own execution role).
    2. Grant the EXISTING Gateway role invoke access to those Lambdas.
    3. Register the 3 Lambdas as targets on the EXISTING Gateway, alongside the
       financial-advisor targets (nothing deleted or renamed).
    4. Add 3 Cedar policies (scoped to this domain's tool arguments) to the
       EXISTING Policy Engine.
    5. Upload the benefits-navigator skill to its own S3 prefix in the EXISTING
       skill bucket, and grant the EXISTING Harness role read access to it.

What this script does NOT do:
    - It does not create, modify, or delete the Gateway, Harness, Policy
      Engine, Guardrail, or skill bucket.
    - It does not mutate the shared Harness's persisted tools/skills/model. The
      DOMAIN SWAP is performed at invoke time (see test_invoke.py) by passing
      this domain's skill URI and an allowedTools allowlist scoped to this
      domain's targets — so financial-advisor keeps working unchanged and the
      swap is reversible per-invocation.
    - It makes NO invocation against the shared Harness at all. Deploy is pure
      provisioning; run test_invoke.py separately to exercise the swap. This
      keeps deploy from writing anything to any actor's memory or otherwise
      exercising the shared Harness that financial-advisor also uses.

Usage:
    python deploy.py

Uses only synthetic data. No real household data, credentials, or account-
specific configuration is stored anywhere in this file or its dependencies.
"""

import io
import json
import time
import zipfile

import boto3

import config
from aws_helpers import (
    create_lambda_role,
    get_account_id,
    grant_gateway_invoke_lambdas,
    grant_gateway_policy_engine_permissions,
    grant_harness_skill_read,
    poll_gateway_status,
    poll_policy_status,
    poll_target_status,
)


def _lookup_gateway(gw_control):
    """Return (gateway_id, gateway_arn, gateway_role_arn) for the shared
    Gateway, or exit with a clear message if the financial-advisor platform
    hasn't been deployed yet."""
    for page in gw_control.get_paginator("list_gateways").paginate():
        for gw in page.get("items", []):
            if gw.get("name") == config.GATEWAY_NAME:
                detail = gw_control.get_gateway(gatewayIdentifier=gw["gatewayId"])
                return detail["gatewayId"], detail["gatewayArn"], detail.get("roleArn")
    raise SystemExit(
        f"Shared Gateway {config.GATEWAY_NAME!r} not found. Run "
        f"demo/financial_advisor/deploy.py first — Stage 5 reuses its Gateway."
    )


def _lookup_policy_engine(control):
    for page in control.get_paginator("list_policy_engines").paginate():
        for pe in page.get("policyEngines", []):
            if pe.get("name") == config.POLICY_ENGINE_NAME:
                return pe["policyEngineId"], pe["policyEngineArn"]
    raise SystemExit(
        f"Shared Policy Engine {config.POLICY_ENGINE_NAME!r} not found. Run "
        f"demo/financial_advisor/deploy.py first — Stage 5 reuses its Policy Engine."
    )


def _lookup_harness_arn(control):
    for page in control.get_paginator("list_harnesses").paginate():
        for h in page.get("harnesses", []):  # ListHarnesses uses "harnesses", not "items"
            if h.get("harnessName") == config.HARNESS_NAME:
                return h["arn"]
    raise SystemExit(
        f"Shared Harness {config.HARNESS_NAME!r} not found. Run "
        f"demo/financial_advisor/deploy.py first — Stage 5 reuses its Harness."
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
            Description=f"Synthetic {target_name} tool for the benefits-navigator AgentCore Gateway demo.",
            Timeout=30,
        )["FunctionArn"]
        print(f"  Created Lambda: {fn_name}")

    lam.get_waiter("function_active_v2").wait(FunctionName=fn_name)
    print(f"  Lambda ARN: {fn_arn}")
    print("  Lambda is Active")
    return fn_arn


def create_gateway_target(gw_control, gateway_id: str, target_name: str, target_config: dict,
                           lambda_arn: str, wait_for_propagation: bool) -> str:
    print("\n" + "=" * 60)
    print(f"Step 3: Register {target_name} as a Gateway target (on the shared Gateway)")
    print("=" * 60)

    if wait_for_propagation:
        # The shared Gateway role's Lambda-invoke policy for THIS domain was
        # just added moments ago in grant_gateway_invoke_lambdas(). IAM
        # propagation can lag, and CreateGatewayTarget is the first place the
        # Gateway service tries to use it — a ValidationException here is
        # usually this timing gap, not a policy bug.
        print("  Waiting for Gateway role Lambda-invoke propagation...")
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
            description=f"Synthetic {target_name} tool (benefits-navigator).",
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
    """Cedar policy statements enforcing this domain's scope on each Gateway
    target's arguments (context.input), matching the financial-advisor pattern
    (see demo/financial_advisor/deploy.py's _cedar_policy_statements).

    These are ADDED to the shared Policy Engine alongside the financial-advisor
    policies. Cedar is default-deny with no wildcard resources, so each permit
    only widens access for exactly its one tool + argument set; the financial-
    advisor policies are unaffected, and any call not matching some permit
    (either domain's) is still denied by default.

    Policy names are prefixed "BenefitsNavigator*" so they never collide with
    the financial-advisor policy names in the same engine and cleanup can
    remove exactly this domain's policies.
    """
    return {
        "BenefitsNavigatorHouseholdLookupScope": (
            "permit(\n"
            "    principal,\n"
            '    action == AgentCore::Action::"household-lookup___household_lookup",\n'
            f'    resource == AgentCore::Gateway::"{gateway_arn}"\n'
            ")\n"
            "when {\n"
            '    context.input.household_id == "household-201" ||\n'
            '    context.input.household_id == "household-202"\n'
            "};"
        ),
        "BenefitsNavigatorEligibilityEngineScope": (
            "permit(\n"
            "    principal,\n"
            '    action == AgentCore::Action::"eligibility-engine___eligibility_engine",\n'
            f'    resource == AgentCore::Gateway::"{gateway_arn}"\n'
            ")\n"
            "when {\n"
            '    context.input.program == "food-assistance" ||\n'
            '    context.input.program == "health-coverage"\n'
            "};"
        ),
        "BenefitsNavigatorProgramLibraryScope": (
            "permit(\n"
            "    principal,\n"
            '    action == AgentCore::Action::"program-library___program_library",\n'
            f'    resource == AgentCore::Gateway::"{gateway_arn}"\n'
            ")\n"
            "when {\n"
            '    context.input.template_id == "food-assistance-enrollment" ||\n'
            '    context.input.template_id == "health-coverage-enrollment"\n'
            "};"
        ),
    }


def add_cedar_policies(control, policy_engine_id: str, gateway_arn: str) -> None:
    print("\n" + "=" * 60)
    print("Step 4: Add benefits-navigator Cedar policies to the shared Policy Engine")
    print("=" * 60)

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


def deploy_skill(account_id: str) -> str:
    """Upload the reviewed skills/benefits-navigator/ directory to its own
    versioned S3 prefix in the SHARED skill bucket, and grant the shared
    Harness role read access to that prefix. Returns the s3:// URI.
    """
    print("\n" + "=" * 60)
    print("Step 5: Deploy benefits-navigator skill to S3 (shared bucket, own prefix)")
    print("=" * 60)

    import os

    bucket_name = config.skill_bucket_name(account_id)
    s3 = boto3.client("s3", region_name=config.AWS_REGION)

    # The bucket is shared and created by financial-advisor's deploy; require
    # it rather than create it, to keep this script strictly additive.
    try:
        s3.head_bucket(Bucket=bucket_name)
    except s3.exceptions.ClientError as error:
        raise SystemExit(
            f"Shared skill bucket {bucket_name!r} not found. Run "
            f"demo/financial_advisor/deploy.py first — Stage 5 reuses its skill bucket."
        ) from error

    uploaded = 0
    for root, _dirs, files in os.walk(config.SKILL_LOCAL_DIR):
        for filename in files:
            local_path = os.path.join(root, filename)
            relative_path = os.path.relpath(local_path, config.SKILL_LOCAL_DIR)
            key = f"{config.SKILL_S3_PREFIX}{relative_path}"
            s3.upload_file(local_path, bucket_name, key)
            uploaded += 1
            print(f"  Uploaded s3://{bucket_name}/{key}")
    if uploaded == 0:
        raise RuntimeError(f"No files found under {config.SKILL_LOCAL_DIR!r} to upload as a skill.")

    grant_harness_skill_read(config.HARNESS_ROLE_NAME, bucket_name, config.SKILL_S3_PREFIX)

    skill_uri = config.skill_s3_uri(account_id)
    print(f"  Skill URI: {skill_uri}")
    return skill_uri


def print_next_steps(harness_arn: str, gateway_arn: str, skill_uri: str) -> None:
    """Print how to test the swap. Deliberately does NOT invoke the Harness —
    deploy is pure provisioning, so it makes zero invocations against the
    SHARED Harness and writes nothing to any actor's memory. Run test_invoke.py
    (a separate step) to exercise the domain swap when you're ready.
    """
    print("\n" + "=" * 60)
    print("Stage 5 deploy complete (benefits-navigator targets + policies + skill added).")
    print("=" * 60)
    print("Shared Gateway/Harness/Policy Engine/Guardrail were reused, not modified.")
    print("This deploy made NO invocation against the shared Harness — nothing")
    print("about financial-advisor's behavior or memory was touched.")
    print()
    print("To test the domain swap (separate, opt-in step):")
    print("    python test_invoke.py --program food-assistance --household-id household-201")
    print("    python test_invoke.py --program health-coverage --household-id household-202")
    print()
    print(f"  Harness: {harness_arn}")
    print(f"  Gateway: {gateway_arn}")
    print(f"  Skill:   {skill_uri}")
    print("  (test_invoke.py resolves these itself; shown here for reference.)")


def main() -> None:
    account_id = get_account_id()
    print(f"Account: {account_id}")
    print(f"Region:  {config.AWS_REGION}\n")

    control = boto3.client("bedrock-agentcore-control", region_name=config.AWS_REGION)

    # Look up the shared platform resources (fail clearly if not deployed).
    gateway_id, gateway_arn, gateway_role_arn = _lookup_gateway(control)
    print(f"  Reusing shared Gateway: {gateway_id}")
    policy_engine_id, policy_engine_arn = _lookup_policy_engine(control)
    print(f"  Reusing shared Policy Engine: {policy_engine_id}")
    harness_arn = _lookup_harness_arn(control)
    print(f"  Reusing shared Harness: {harness_arn}")

    # 1. This domain's Lambdas.
    lambda_role_arn = create_lambda_role(config.LAMBDA_ROLE_NAME)
    lambda_arns = {}
    for target_name, target_config in config.TARGETS.items():
        lambda_arns[target_name] = deploy_lambda(lambda_role_arn, target_name, target_config)

    # 2. Additively grant the shared Gateway role invoke access to them.
    grant_gateway_invoke_lambdas(config.GATEWAY_ROLE_NAME, list(lambda_arns.values()))

    # 3. Register them as targets on the shared Gateway.
    for i, (target_name, target_config) in enumerate(config.TARGETS.items()):
        create_gateway_target(
            control, gateway_id, target_name, target_config,
            lambda_arns[target_name],
            wait_for_propagation=(i == 0),
        )

    # 4. Add this domain's Cedar policies to the shared Policy Engine, and
    #    re-assert the shared Gateway role's policy-engine permissions (already
    #    granted by financial-advisor; redundant if present, required if this
    #    directory is run standalone).
    grant_gateway_policy_engine_permissions(config.GATEWAY_ROLE_NAME, policy_engine_arn, gateway_arn)
    add_cedar_policies(control, policy_engine_id, gateway_arn)

    # 5. This domain's skill, in the shared bucket under its own prefix.
    skill_uri = deploy_skill(account_id)

    # Pure provisioning — no invocation against the shared Harness. Test the
    # swap separately via test_invoke.py so deploy never changes financial-
    # advisor's behavior or writes to any actor's memory.
    print_next_steps(harness_arn, gateway_arn, skill_uri)


if __name__ == "__main__":
    main()
