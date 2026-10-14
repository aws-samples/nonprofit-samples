# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""
Rollback path for deploy.py — deletes every resource it created, in the
order that avoids ConflictException (target before gateway; harness can be
deleted independently since it has no static binding to the gateway).

Usage:
    python cleanup.py

Not run automatically by deploy.py. Run this explicitly when you are done
reviewing and testing the Stage 1 resources.
"""

import time

import boto3

import config


def delete_harness() -> None:
    print("=" * 60)
    print("Delete Harness")
    print("=" * 60)
    control = boto3.client("bedrock-agentcore-control", region_name=config.AWS_REGION)
    harness_id = None
    for page in control.get_paginator("list_harnesses").paginate():
        for h in page.get("harnesses", []):  # ListHarnesses uses "harnesses", not "items"
            if h.get("harnessName") == config.HARNESS_NAME:
                harness_id = h["harnessId"]
                break
        if harness_id:
            break
    if not harness_id:
        print("  Harness not found, skipping.")
        return

    deadline = time.monotonic() + 600
    while True:
        try:
            control.delete_harness(harnessId=harness_id)
            print(f"  Deleted harness: {harness_id}")
            return
        except control.exceptions.ConflictException:
            if time.monotonic() > deadline:
                print(f"  Warning: harness {harness_id} still not deletable after 600s.")
                return
            print("  Harness still provisioning, retrying delete in 5s...")
            time.sleep(5)


def delete_gateway_and_target() -> None:
    print("\n" + "=" * 60)
    print("Delete Gateway target, then Gateway")
    print("=" * 60)
    gw_control = boto3.client("bedrock-agentcore-control", region_name=config.AWS_REGION)

    gateway_id = None
    for page in gw_control.get_paginator("list_gateways").paginate():
        for gw in page.get("items", []):
            if gw.get("name") == config.GATEWAY_NAME:
                gateway_id = gw["gatewayId"]
                break
        if gateway_id:
            break
    if not gateway_id:
        print("  Gateway not found, skipping.")
        return

    target_names = set(config.TARGETS.keys())
    for page in gw_control.get_paginator("list_gateway_targets").paginate(gatewayIdentifier=gateway_id):
        for tgt in page.get("items", []):
            if tgt.get("name") in target_names:
                try:
                    gw_control.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=tgt["targetId"])
                    print(f"  Deleted target: {tgt['name']} ({tgt['targetId']})")
                    time.sleep(10)
                except Exception as error:  # noqa: BLE001 - cleanup must continue regardless
                    print(f"  Warning: {error}")

    try:
        gw_control.delete_gateway(gatewayIdentifier=gateway_id)
        print(f"  Deleted gateway: {gateway_id}")
    except Exception as error:  # noqa: BLE001 - cleanup must continue regardless
        print(f"  Warning: {error}")


def delete_lambdas() -> None:
    print("\n" + "=" * 60)
    print("Delete Lambdas")
    print("=" * 60)
    lam = boto3.client("lambda", region_name=config.AWS_REGION)
    for target_name, target_config in config.TARGETS.items():
        fn_name = target_config["lambda_function_name"]
        try:
            lam.delete_function(FunctionName=fn_name)
            print(f"  Deleted Lambda: {fn_name} ({target_name})")
        except lam.exceptions.ResourceNotFoundException:
            print(f"  Lambda not found, skipping: {fn_name}")


def delete_role(role_name: str) -> None:
    iam = boto3.client("iam")
    try:
        for page in iam.get_paginator("list_role_policies").paginate(RoleName=role_name):
            for name in page["PolicyNames"]:
                iam.delete_role_policy(RoleName=role_name, PolicyName=name)
        for page in iam.get_paginator("list_attached_role_policies").paginate(RoleName=role_name):
            for policy in page["AttachedPolicies"]:
                iam.detach_role_policy(RoleName=role_name, PolicyArn=policy["PolicyArn"])
        iam.delete_role(RoleName=role_name)
        print(f"  Deleted role: {role_name}")
    except iam.exceptions.NoSuchEntityException:
        print(f"  Role not found, skipping: {role_name}")


def delete_roles() -> None:
    print("\n" + "=" * 60)
    print("Delete IAM roles")
    print("=" * 60)
    delete_role(config.HARNESS_ROLE_NAME)
    delete_role(config.GATEWAY_ROLE_NAME)
    delete_role(config.LAMBDA_ROLE_NAME)


def delete_policy_engine_and_policies() -> None:
    """Delete the Cedar policies, then the policy engine.

    A policy engine's association with a gateway lives entirely on the
    gateway side (policyEngineConfiguration) — the engine itself has no
    record of which gateway(s) reference it, and deleting a gateway does
    NOT delete or detach its policy engine. So this must run as its own
    explicit step regardless of what delete_gateway_and_target() already
    did. Policies must be deleted before the engine (engines cannot be
    deleted while they hold policies).
    """
    print("\n" + "=" * 60)
    print("Delete Policy Engine and its Cedar policies")
    print("=" * 60)
    control = boto3.client("bedrock-agentcore-control", region_name=config.AWS_REGION)

    policy_engine_id = None
    for page in control.get_paginator("list_policy_engines").paginate():
        for pe in page.get("policyEngines", []):
            if pe.get("name") == config.POLICY_ENGINE_NAME:
                policy_engine_id = pe["policyEngineId"]
                break
        if policy_engine_id:
            break
    if not policy_engine_id:
        print("  Policy engine not found, skipping.")
        return

    for page in control.get_paginator("list_policies").paginate(policyEngineId=policy_engine_id):
        for pol in page.get("policies", []):
            try:
                control.delete_policy(policyEngineId=policy_engine_id, policyId=pol["policyId"])
                print(f"  Deleted policy: {pol['name']} ({pol['policyId']})")
            except Exception as error:  # noqa: BLE001 - cleanup must continue regardless
                print(f"  Warning: {error}")

    deadline = time.monotonic() + 300
    while True:
        try:
            control.delete_policy_engine(policyEngineId=policy_engine_id)
            print(f"  Deleted policy engine: {policy_engine_id}")
            return
        except control.exceptions.ConflictException:
            if time.monotonic() > deadline:
                print(f"  Warning: policy engine {policy_engine_id} still not deletable after 300s.")
                return
            print("  Policy(ies) still deleting, retrying in 5s...")
            time.sleep(5)


def delete_guardrail() -> None:
    print("\n" + "=" * 60)
    print("Delete Bedrock Guardrail")
    print("=" * 60)
    bedrock = boto3.client("bedrock", region_name=config.AWS_REGION)

    guardrail_id = None
    for page in bedrock.get_paginator("list_guardrails").paginate():
        for gr in page.get("guardrails", []):
            if gr.get("name") == config.GUARDRAIL_NAME:
                guardrail_id = gr["id"]
                break
        if guardrail_id:
            break
    if not guardrail_id:
        print("  Guardrail not found, skipping.")
        return

    try:
        bedrock.delete_guardrail(guardrailIdentifier=guardrail_id)
        print(f"  Deleted guardrail: {guardrail_id}")
    except Exception as error:  # noqa: BLE001 - cleanup must continue regardless
        print(f"  Warning: {error}")


def delete_skill_bucket() -> None:
    print("\n" + "=" * 60)
    print("Delete skill S3 bucket")
    print("=" * 60)
    account_id = boto3.client("sts").get_caller_identity()["Account"]
    bucket_name = config.skill_bucket_name(account_id)
    s3 = boto3.client("s3", region_name=config.AWS_REGION)

    try:
        s3.head_bucket(Bucket=bucket_name)
    except s3.exceptions.ClientError:
        print(f"  Bucket not found, skipping: {bucket_name}")
        return

    paginator = s3.get_paginator("list_objects_v2")
    deleted_any = False
    for page in paginator.paginate(Bucket=bucket_name):
        objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
        if objects:
            s3.delete_objects(Bucket=bucket_name, Delete={"Objects": objects})
            deleted_any = True
    if deleted_any:
        print(f"  Deleted all objects under s3://{bucket_name}/")

    s3.delete_bucket(Bucket=bucket_name)
    print(f"  Deleted bucket: {bucket_name}")


def main() -> None:
    delete_harness()
    delete_gateway_and_target()
    delete_policy_engine_and_policies()
    delete_guardrail()
    delete_lambdas()
    delete_roles()
    delete_skill_bucket()
    print("\nCleanup complete.")


if __name__ == "__main__":
    main()
