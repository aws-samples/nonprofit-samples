# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""
Rollback path for demo/benefits_navigator/deploy.py — removes ONLY this
domain's additions and deliberately leaves the shared platform intact.

Removes:
    - the 3 benefits-navigator Gateway targets (from the shared Gateway),
    - the 3 benefits-navigator Cedar policies (from the shared Policy Engine),
    - the 3 benefits-navigator Lambdas and their execution role,
    - this domain's skill objects under its S3 prefix (in the shared bucket),
    - the additive inline policies this domain added to the shared Gateway and
      Harness roles.

Does NOT remove (shared with financial-advisor — removing them would break it):
    - the Gateway, Harness, Policy Engine, or Guardrail themselves,
    - the shared skill bucket (only this domain's prefix is emptied),
    - the shared Gateway/Harness roles (only this domain's inline policies on
      them are removed),
    - anything financial-advisor created.

Use demo/financial_advisor/cleanup.py to tear down the shared platform after
both domains are done.

Usage:
    python cleanup.py
"""

import time

import boto3

import config


def delete_gateway_targets() -> None:
    print("=" * 60)
    print("Delete benefits-navigator Gateway targets (shared Gateway kept)")
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
        print("  Shared Gateway not found, skipping target deletion.")
        return

    target_names = set(config.TARGETS.keys())
    for page in gw_control.get_paginator("list_gateway_targets").paginate(gatewayIdentifier=gateway_id):
        for tgt in page.get("items", []):
            if tgt.get("name") in target_names:
                try:
                    gw_control.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=tgt["targetId"])
                    print(f"  Deleted target: {tgt['name']} ({tgt['targetId']})")
                    time.sleep(5)
                except Exception as error:  # noqa: BLE001 - cleanup must continue regardless
                    print(f"  Warning: {error}")
    print("  Shared Gateway itself left intact.")


def delete_cedar_policies() -> None:
    print("\n" + "=" * 60)
    print("Delete benefits-navigator Cedar policies (shared Policy Engine kept)")
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
        print("  Shared Policy Engine not found, skipping policy deletion.")
        return

    # Only this domain's policies, identified by the BenefitsNavigator* name
    # prefix used in deploy.py's _cedar_policy_statements.
    benefits_prefix = "BenefitsNavigator"
    for page in control.get_paginator("list_policies").paginate(policyEngineId=policy_engine_id):
        for pol in page.get("policies", []):
            if pol.get("name", "").startswith(benefits_prefix):
                try:
                    control.delete_policy(policyEngineId=policy_engine_id, policyId=pol["policyId"])
                    print(f"  Deleted policy: {pol['name']} ({pol['policyId']})")
                except Exception as error:  # noqa: BLE001 - cleanup must continue regardless
                    print(f"  Warning: {error}")
    print("  Shared Policy Engine and financial-advisor policies left intact.")


def delete_lambdas() -> None:
    print("\n" + "=" * 60)
    print("Delete benefits-navigator Lambdas")
    print("=" * 60)
    lam = boto3.client("lambda", region_name=config.AWS_REGION)
    for target_name, target_config in config.TARGETS.items():
        fn_name = target_config["lambda_function_name"]
        try:
            lam.delete_function(FunctionName=fn_name)
            print(f"  Deleted Lambda: {fn_name} ({target_name})")
        except lam.exceptions.ResourceNotFoundException:
            print(f"  Lambda not found, skipping: {fn_name}")


def delete_lambda_role() -> None:
    print("\n" + "=" * 60)
    print("Delete benefits-navigator Lambda role")
    print("=" * 60)
    iam = boto3.client("iam")
    role_name = config.LAMBDA_ROLE_NAME
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


def remove_additive_inline_policies() -> None:
    """Remove ONLY the inline policies this domain added to the SHARED Gateway
    and Harness roles, leaving those roles (and financial-advisor's inline
    policies on them) intact."""
    print("\n" + "=" * 60)
    print("Remove benefits-navigator inline policies from shared roles")
    print("=" * 60)
    iam = boto3.client("iam")

    additive = [
        (config.GATEWAY_ROLE_NAME, "BenefitsNavigatorGatewayLambdaInvoke"),
        (config.GATEWAY_ROLE_NAME, "BenefitsNavigatorGatewayPolicyEngineRead"),
        (config.HARNESS_ROLE_NAME, "BenefitsNavigatorSkillBucketRead"),
    ]
    for role_name, policy_name in additive:
        try:
            iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
            print(f"  Removed inline policy {policy_name!r} from {role_name}")
        except iam.exceptions.NoSuchEntityException:
            print(f"  Inline policy {policy_name!r} not found on {role_name}, skipping.")
    print("  Shared roles and their financial-advisor policies left intact.")


def delete_skill_objects() -> None:
    print("\n" + "=" * 60)
    print("Delete benefits-navigator skill objects (shared bucket kept)")
    print("=" * 60)
    account_id = boto3.client("sts").get_caller_identity()["Account"]
    bucket_name = config.skill_bucket_name(account_id)
    s3 = boto3.client("s3", region_name=config.AWS_REGION)

    try:
        s3.head_bucket(Bucket=bucket_name)
    except s3.exceptions.ClientError:
        print(f"  Shared skill bucket not found, skipping: {bucket_name}")
        return

    paginator = s3.get_paginator("list_objects_v2")
    deleted_any = False
    for page in paginator.paginate(Bucket=bucket_name, Prefix=config.SKILL_S3_PREFIX):
        objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
        if objects:
            s3.delete_objects(Bucket=bucket_name, Delete={"Objects": objects})
            deleted_any = True
    if deleted_any:
        print(f"  Deleted objects under s3://{bucket_name}/{config.SKILL_S3_PREFIX}")
    else:
        print(f"  No objects found under s3://{bucket_name}/{config.SKILL_S3_PREFIX}")
    print("  Shared skill bucket (and financial-advisor's prefix) left intact.")


def main() -> None:
    delete_gateway_targets()
    delete_cedar_policies()
    delete_lambdas()
    delete_lambda_role()
    remove_additive_inline_policies()
    delete_skill_objects()
    print("\nBenefits-navigator cleanup complete. Shared platform left intact for financial-advisor.")


if __name__ == "__main__":
    main()
