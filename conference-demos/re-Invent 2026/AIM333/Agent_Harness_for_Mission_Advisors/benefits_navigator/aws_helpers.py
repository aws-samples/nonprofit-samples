# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""AWS helper functions for the benefits-navigator (Stage 5 domain-swap) demo.

Deliberately standalone (no import from outside demo/benefits_navigator/) so
this directory can be cloned and run independently, matching the pattern used
by demo/financial_advisor/aws_helpers.py.

STAGE 5 DESIGN — additive only. The Gateway, Harness, Policy Engine, Guardrail,
and skill S3 bucket are SHARED platform resources created by the financial-
advisor deploy. This module never creates or deletes them. It only:
  - creates this domain's own Lambda execution role and Lambdas,
  - grants the EXISTING Gateway role invoke access to those Lambdas (additively),
  - grants the EXISTING Harness role read access to this domain's skill prefix,
  - builds this domain's Cedar policy statements (added to the shared engine
    by deploy.py),
  - grants the EXISTING Gateway role authorization permissions on the shared
    Policy Engine (idempotent; financial-advisor already did this, re-asserted
    here so this directory is runnable standalone).

IAM policies are enumerated by action and scoped by resource ARN, following
demo/financial_advisor/aws_helpers.py.
"""

import json
import time

import boto3

GATEWAY_POLL_INTERVAL = 5
GATEWAY_POLL_TIMEOUT = 300
GATEWAY_FAILURE_STATUSES = ("FAILED", "UPDATE_UNSUCCESSFUL")
TARGET_FAILURE_STATUSES = (*GATEWAY_FAILURE_STATUSES, "SYNCHRONIZE_UNSUCCESSFUL")
TARGET_PENDING_AUTH_STATUSES = (
    "CREATE_PENDING_AUTH",
    "UPDATE_PENDING_AUTH",
    "SYNCHRONIZE_PENDING_AUTH",
)


def get_account_id() -> str:
    return boto3.client("sts").get_caller_identity()["Account"]


def ensure_role(iam_client, role_name: str, trust_policy: dict, description: str) -> str:
    """Create the IAM role if it doesn't exist; return its ARN either way."""
    trust_json = json.dumps(trust_policy)
    try:
        existing = iam_client.get_role(RoleName=role_name)
        arn = existing["Role"]["Arn"]
        iam_client.update_assume_role_policy(RoleName=role_name, PolicyDocument=trust_json)
        print(f"  Role already exists, trust policy refreshed: {role_name}")
        return arn
    except iam_client.exceptions.NoSuchEntityException:
        resp = iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=trust_json,
            Description=description,
        )
        print(f"  Created role: {role_name}")
        return resp["Role"]["Arn"]


def put_scoped_policy(iam_client, role_name: str, policy_name: str, policy_document: dict) -> None:
    iam_client.put_role_policy(
        RoleName=role_name,
        PolicyName=policy_name,
        PolicyDocument=json.dumps(policy_document),
    )
    print(f"  Attached inline policy {policy_name!r} to {role_name}")


def create_lambda_role(lambda_role_name: str) -> str:
    """Create (or refresh) this domain's Lambda execution role (logging only).

    Separate from financial-advisor's Lambda role so the two domains' Lambdas
    stay independently cleanable.
    """
    iam = boto3.client("iam")
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    role_arn = ensure_role(
        iam, lambda_role_name, trust_policy, "Execution role for the benefits-navigator demo Lambdas."
    )
    try:
        iam.attach_role_policy(
            RoleName=lambda_role_name,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        )
    except Exception as error:  # noqa: BLE001 - already-attached is fine; surface anything else
        print(f"  Note: could not attach AWSLambdaBasicExecutionRole ({error})")
    return role_arn


def grant_gateway_invoke_lambdas(gateway_role_name: str, lambda_arns) -> None:
    """ADDITIVELY grant the EXISTING (shared) Gateway role permission to invoke
    this domain's Lambdas.

    The Gateway role already has a financial-advisor inline policy
    (FinancialAdvisorGatewayLambdaInvoke) scoped to the financial-advisor
    Lambdas. Rather than touch that policy, this adds a SEPARATE inline policy
    (BenefitsNavigatorGatewayLambdaInvoke) scoped to only this domain's
    Lambdas. Both inline policies coexist on the same role, so the shared
    Gateway can invoke both domains' targets and cleanup can remove just this
    one without affecting financial-advisor.
    """
    if isinstance(lambda_arns, str):
        lambda_arns = [lambda_arns]
    iam = boto3.client("iam")
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "InvokeBenefitsNavigatorLambdasOnly",
                "Effect": "Allow",
                "Action": "lambda:InvokeFunction",
                "Resource": list(lambda_arns),
            }
        ],
    }
    put_scoped_policy(iam, gateway_role_name, "BenefitsNavigatorGatewayLambdaInvoke", policy)


def grant_harness_skill_read(harness_role_name: str, bucket_name: str, s3_prefix: str) -> None:
    """ADDITIVELY grant the EXISTING (shared) Harness role read access to this
    domain's skill prefix in the shared skill bucket.

    Same additive pattern as grant_gateway_invoke_lambdas: financial-advisor's
    FinancialAdvisorSkillBucketRead policy covers its own prefix; this adds a
    separate BenefitsNavigatorSkillBucketRead policy scoped to only this
    domain's prefix, so both coexist and cleanup removes just this one.
    """
    iam = boto3.client("iam")
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "BenefitsNavigatorSkillListScoped",
                "Effect": "Allow",
                "Action": "s3:ListBucket",
                "Resource": f"arn:aws:s3:::{bucket_name}",
                "Condition": {"StringLike": {"s3:prefix": f"{s3_prefix}*"}},
            },
            {
                "Sid": "BenefitsNavigatorSkillObjectRead",
                "Effect": "Allow",
                "Action": "s3:GetObject",
                "Resource": f"arn:aws:s3:::{bucket_name}/{s3_prefix}*",
            },
        ],
    }
    put_scoped_policy(iam, harness_role_name, "BenefitsNavigatorSkillBucketRead", policy)


def grant_gateway_policy_engine_permissions(gateway_role_name: str, policy_engine_arn: str, gateway_arn: str) -> None:
    """Re-assert the three Policy-Engine permissions the shared Gateway role
    needs (GetPolicyEngine, AuthorizeAction, PartiallyAuthorizeActions), scoped
    to both the policy-engine ARN and the gateway ARN.

    financial-advisor's deploy already granted these (as
    FinancialAdvisorGatewayPolicyEngineRead) — they're not domain-specific, and
    the engine and gateway are shared. This re-asserts them under a benefits-
    navigator-named policy so this directory is runnable standalone even if
    financial-advisor's policy were ever removed; if both are present they are
    simply redundant, not conflicting.
    """
    iam = boto3.client("iam")
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "PolicyEngineConfiguration",
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:GetPolicyEngine"],
                "Resource": [policy_engine_arn],
            },
            {
                "Sid": "PolicyEngineAuthorization",
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:AuthorizeAction", "bedrock-agentcore:PartiallyAuthorizeActions"],
                "Resource": [policy_engine_arn, gateway_arn],
            },
        ],
    }
    put_scoped_policy(iam, gateway_role_name, "BenefitsNavigatorGatewayPolicyEngineRead", policy)


def poll_policy_status(control, policy_engine_id: str, policy_id: str, target_status: str = "ACTIVE",
                        timeout: int = GATEWAY_POLL_TIMEOUT) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        resp = control.get_policy(policyEngineId=policy_engine_id, policyId=policy_id)
        status = resp["status"]
        print(f"  Policy status: {status}")
        if status == target_status:
            return resp
        if "FAILED" in status:
            raise RuntimeError(f"Policy {status}: {resp.get('statusReasons', [])}")
        if time.monotonic() > deadline:
            raise TimeoutError(f"Policy not {target_status} after {timeout}s")
        time.sleep(GATEWAY_POLL_INTERVAL)


def poll_gateway_status(control, gateway_id: str, target_status: str = "READY",
                         timeout: int = GATEWAY_POLL_TIMEOUT) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        resp = control.get_gateway(gatewayIdentifier=gateway_id)
        status = resp["status"]
        print(f"  Gateway status: {status}")
        if status == target_status:
            return resp
        if status in GATEWAY_FAILURE_STATUSES:
            raise RuntimeError(f"Gateway {status}: {resp.get('statusReasons', [])}")
        if time.monotonic() > deadline:
            raise TimeoutError(f"Gateway not {target_status} after {timeout}s")
        time.sleep(GATEWAY_POLL_INTERVAL)


def poll_target_status(control, gateway_id: str, target_id: str, target_status: str = "READY",
                        timeout: int = GATEWAY_POLL_TIMEOUT) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        resp = control.get_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id)
        status = resp["status"]
        print(f"  Target status: {status}")
        if status == target_status:
            return resp
        if status in TARGET_FAILURE_STATUSES:
            raise RuntimeError(f"Target {status}: {resp.get('statusReasons', [])}")
        if status in TARGET_PENDING_AUTH_STATUSES:
            raise RuntimeError(
                f"Target {status}: waiting on an outbound authorization that will not "
                f"complete on its own. {resp.get('statusReasons', [])}"
            )
        if time.monotonic() > deadline:
            raise TimeoutError(f"Target not {target_status} after {timeout}s")
        time.sleep(GATEWAY_POLL_INTERVAL)
