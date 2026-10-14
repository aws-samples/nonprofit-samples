# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Self-contained AWS helpers for the financial-advisor Stage 1 demo.

Deliberately standalone (no import from outside demo/financial_advisor/) so
this directory can be cloned and run independently, matching the pattern used
by 03-integrations/gateway/agentcore-tool-search-plugin in
awslabs/agentcore-samples.

IAM policies below are enumerated by action and scoped by resource ARN where
possible, following the pattern in 01-features/01-harness/utils/iam.py in the
same upstream repo, rather than using wildcard actions or resources.
"""

import json
import time

import boto3

HARNESS_POLL_INTERVAL = 5
HARNESS_POLL_TIMEOUT = 600
HARNESS_FAILURE_STATUSES = ("CREATE_FAILED", "UPDATE_FAILED", "DELETE_FAILED")

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


def create_harness_role(region: str, harness_role_name: str, gateway_arn: str | None = None) -> str:
    """Create (or refresh) the Harness execution role with least-privilege permissions.

    Enumerated actions, not wildcards, following utils/iam.py's pattern in
    awslabs/agentcore-samples: broad Action: "*" grants would let this role do
    far more than a demo Harness needs.

    gateway_arn: if provided, scopes InvokeGateway to this specific gateway
    (per the AWS_IAM inbound-auth security best practice in
    gateway-inbound-auth.md: "you should modify this policy such that the
    Resource field is scoped to the gateway that you create"). Falls back to
    "*" only when the gateway doesn't exist yet (first-ever deploy, before
    create_gateway has run) — call this function again once the gateway ARN
    is known to tighten the policy.
    """
    iam = boto3.client("iam")
    account_id = get_account_id()

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {"StringEquals": {"aws:SourceAccount": account_id}},
            }
        ],
    }
    role_arn = ensure_role(
        iam, harness_role_name, trust_policy, "Execution role for the financial-advisor demo Harness."
    )

    permissions_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "BedrockInvokeModel",
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                "Resource": [
                    "arn:aws:bedrock:*::foundation-model/*",
                    f"arn:aws:bedrock:*:{account_id}:inference-profile/*",
                ],
            },
            {
                "Sid": "AgentCoreGatewayInvoke",
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:InvokeGateway"],
                "Resource": gateway_arn if gateway_arn else "*",
            },
            {
                # The Harness auto-provisions a short-term memory resource per
                # session (arn:...:memory/<harness-name>-<suffix>) and reads
                # from it via ListEvents/GetEvent during InvokeHarness, even
                # when the demo does not explicitly create or attach memory.
                # DeleteEvent was added after a live Guardrail intervention
                # (Stage 3) failed with AccessDeniedException: the Harness
                # tries to delete/replace the in-progress event when a
                # guardrail blocks a response mid-turn, which needs
                # DeleteEvent even though normal turns never call it.
                "Sid": "AgentCoreSessionMemory",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:CreateEvent",
                    "bedrock-agentcore:ListEvents",
                    "bedrock-agentcore:GetEvent",
                    "bedrock-agentcore:DeleteEvent",
                    "bedrock-agentcore:GetMemory",
                    "bedrock-agentcore:ListMemories",
                    "bedrock-agentcore:RetrieveMemoryRecords",
                ],
                "Resource": f"arn:aws:bedrock-agentcore:*:{account_id}:memory/*",
            },
            {
                "Sid": "CloudWatchLogs",
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                "Resource": [
                    f"arn:aws:logs:*:{account_id}:log-group:/aws/bedrock-agentcore/runtimes/*",
                    f"arn:aws:logs:*:{account_id}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*",
                ],
            },
            {
                "Sid": "XRay",
                "Effect": "Allow",
                "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords"],
                "Resource": "*",
            },
        ],
    }
    put_scoped_policy(iam, harness_role_name, "FinancialAdvisorHarnessPolicy", permissions_policy)
    return role_arn


def create_gateway_role(region: str, gateway_role_name: str, lambda_arns) -> str:
    """Create (or refresh) the Gateway's execution role, scoped to invoke only the given Lambda(s).

    Accepts a single ARN string or a list of ARNs — the Gateway serves
    multiple targets (member-lookup, rate-engine, advisor-library), so its
    role needs invoke permission on each backing Lambda, and only those.
    """
    iam = boto3.client("iam")

    if isinstance(lambda_arns, str):
        lambda_arns = [lambda_arns]

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    role_arn = ensure_role(
        iam, gateway_role_name, trust_policy, "Role for the financial-advisor demo Gateway to invoke its Lambda targets."
    )

    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "InvokeFinancialAdvisorLambdasOnly",
                "Effect": "Allow",
                "Action": "lambda:InvokeFunction",
                "Resource": list(lambda_arns),
            }
        ],
    }
    put_scoped_policy(iam, gateway_role_name, "FinancialAdvisorGatewayLambdaInvoke", policy)
    return role_arn


def create_lambda_role(lambda_role_name: str) -> str:
    """Create (or refresh) the Lambda's own execution role (basic logging only)."""
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
        iam, lambda_role_name, trust_policy, "Execution role for the financial-advisor member-lookup Lambda."
    )
    try:
        iam.attach_role_policy(
            RoleName=lambda_role_name,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        )
    except Exception as error:  # noqa: BLE001 - already-attached is fine; surface anything else
        print(f"  Note: could not attach AWSLambdaBasicExecutionRole ({error})")
    return role_arn


def create_skill_bucket(region: str, bucket_name: str) -> None:
    """Create the S3 bucket that holds the versioned skill artifact, if it
    doesn't already exist. Idempotent: an existing bucket owned by this
    account is left alone.
    """
    s3 = boto3.client("s3", region_name=region)
    try:
        s3.head_bucket(Bucket=bucket_name)
        print(f"  Bucket already exists: {bucket_name}")
        return
    except s3.exceptions.ClientError as error:
        status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status != 404:
            raise

    create_kwargs = {"Bucket": bucket_name}
    if region != "us-east-1":
        create_kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
    s3.create_bucket(**create_kwargs)
    s3.put_public_access_block(
        Bucket=bucket_name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    print(f"  Created bucket (public access blocked): {bucket_name}")


def upload_skill_directory(region: str, bucket_name: str, s3_prefix: str, local_dir: str) -> None:
    """Upload every file under local_dir to s3://bucket_name/s3_prefix,
    preserving the relative path (SKILL.md, and any scripts/, references/,
    assets/ subdirectories the AgentSkills.io spec allows).
    """
    import os

    s3 = boto3.client("s3", region_name=region)
    uploaded = 0
    for root, _dirs, files in os.walk(local_dir):
        for filename in files:
            local_path = os.path.join(root, filename)
            relative_path = os.path.relpath(local_path, local_dir)
            key = f"{s3_prefix}{relative_path}"
            s3.upload_file(local_path, bucket_name, key)
            uploaded += 1
            print(f"  Uploaded s3://{bucket_name}/{key}")
    if uploaded == 0:
        raise RuntimeError(f"No files found under {local_dir!r} to upload as a skill.")


def grant_skill_bucket_read(harness_role_name: str, bucket_name: str, s3_prefix: str) -> None:
    """Grant the Harness execution role s3:GetObject and s3:ListBucket,
    scoped to only this skill's bucket/prefix, per the harness-skills docs
    ("The execution role needs s3:GetObject and s3:ListBucket on the bucket").
    ListBucket is scoped further with an s3:prefix condition so the role
    cannot list unrelated keys in the same bucket.
    """
    iam = boto3.client("iam")
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "SkillBucketListScoped",
                "Effect": "Allow",
                "Action": "s3:ListBucket",
                "Resource": f"arn:aws:s3:::{bucket_name}",
                "Condition": {"StringLike": {"s3:prefix": f"{s3_prefix}*"}},
            },
            {
                "Sid": "SkillObjectRead",
                "Effect": "Allow",
                "Action": "s3:GetObject",
                "Resource": f"arn:aws:s3:::{bucket_name}/{s3_prefix}*",
            },
        ],
    }
    put_scoped_policy(iam, harness_role_name, "FinancialAdvisorSkillBucketRead", policy)


def grant_gateway_policy_engine_permissions(gateway_role_name: str, policy_engine_arn: str, gateway_arn: str) -> None:
    """Grant the Gateway's own execution role the three permissions AWS
    documents as required for any Gateway with a Policy Engine attached
    (see the "AgentCore Gateway and Policy in AgentCore IAM Permissions"
    guide): GetPolicyEngine, AuthorizeAction, and PartiallyAuthorizeActions.
    AuthorizeAction and PartiallyAuthorizeActions must be scoped to BOTH the
    policy-engine ARN and the gateway ARN — confirmed against a live
    AccessDeniedException, not just the docs (the error names only one
    resource at a time; both are required together).

    Without these, UpdateGateway with a policyEngineConfiguration returns
    AccessDeniedException, and if the role is ever missing them after
    attachment, every tool invocation is denied by default even with
    permit policies configured.
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
    put_scoped_policy(iam, gateway_role_name, "FinancialAdvisorGatewayPolicyEngineRead", policy)


def poll_policy_engine_status(control, policy_engine_id: str, target_status: str = "ACTIVE",
                               timeout: int = GATEWAY_POLL_TIMEOUT) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        resp = control.get_policy_engine(policyEngineId=policy_engine_id)
        status = resp["status"]
        print(f"  Policy engine status: {status}")
        if status == target_status:
            return resp
        if "FAILED" in status:
            raise RuntimeError(f"Policy engine {status}: {resp.get('statusReasons', [])}")
        if time.monotonic() > deadline:
            raise TimeoutError(f"Policy engine not {target_status} after {timeout}s")
        time.sleep(GATEWAY_POLL_INTERVAL)


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


GUARDRAIL_TOPICS = [
    {
        "name": "TaxAdvice",
        "definition": (
            "Offering guidance, calculations, or recommendations about tax filing, tax liability, "
            "tax deductions, or tax strategy."
        ),
        "examples": [
            "How much will I owe in taxes on this withdrawal?",
            "What tax deductions should I claim this year?",
            "Should I file jointly or separately?",
        ],
        "type": "DENY",
    },
    {
        "name": "LegalAdvice",
        "definition": (
            "Offering guidance or recommendations on legal matters, contracts, disputes, or "
            "interpretation of laws and regulations."
        ),
        "examples": [
            "Can I sue my employer over this?",
            "Is this contract clause legally enforceable?",
            "What are my legal rights in a divorce settlement?",
        ],
        "type": "DENY",
    },
    {
        "name": "MedicalAdvice",
        "definition": "Offering medical diagnoses, treatment recommendations, or health advice.",
        "examples": [
            "What medication should I take for this condition?",
            "Is this symptom something to worry about?",
            "How should I treat my diagnosis?",
        ],
        "type": "DENY",
    },
]

GUARDRAIL_BLOCKED_INPUT_MESSAGE = (
    "This request is outside the financial-advisor skill's scope (tax, legal, or medical topics). "
    "Please consult a qualified advisor or professional for this question."
)
GUARDRAIL_BLOCKED_OUTPUT_MESSAGE = (
    "This response was blocked because it touched on tax, legal, or medical advice, which is outside "
    "this skill's scope. Please consult a qualified advisor or professional for this question."
)


def create_guardrail(region: str, guardrail_name: str, guardrail_version: str) -> tuple[str, str]:
    """Create (or reuse) the Bedrock Guardrail and its numbered version.

    Guardrails need a numbered version (not DRAFT) to attach to a Harness
    invocation (see harness-models.html's guardrailConfig example) — DRAFT
    is a working copy, not something invoke_harness accepts. Idempotent:
    if a guardrail with this name already exists, reuses it; if the
    requested version already exists, reuses that too (create_guardrail_version
    always creates a new numbered version, so this must check first).
    """
    bedrock = boto3.client("bedrock", region_name=region)

    guardrail_id = None
    for page in bedrock.get_paginator("list_guardrails").paginate():
        for gr in page.get("guardrails", []):
            if gr.get("name") == guardrail_name:
                guardrail_id = gr["id"]
                break
        if guardrail_id:
            break

    if guardrail_id is None:
        resp = bedrock.create_guardrail(
            name=guardrail_name,
            description=(
                "Financial-advisor demo Guardrail (Stage 3). Blocks tax, legal, and medical advice "
                "requests, matching SKILL.md's existing out-of-scope rule."
            ),
            topicPolicyConfig={"topicsConfig": GUARDRAIL_TOPICS},
            blockedInputMessaging=GUARDRAIL_BLOCKED_INPUT_MESSAGE,
            blockedOutputsMessaging=GUARDRAIL_BLOCKED_OUTPUT_MESSAGE,
        )
        guardrail_id = resp["guardrailId"]
        print(f"  Created guardrail: {guardrail_id}")
    else:
        print(f"  Guardrail already exists: {guardrail_id}")

    try:
        bedrock.get_guardrail(guardrailIdentifier=guardrail_id, guardrailVersion=guardrail_version)
        print(f"  Guardrail version already exists: {guardrail_version}")
    except bedrock.exceptions.ResourceNotFoundException:
        resp = bedrock.create_guardrail_version(
            guardrailIdentifier=guardrail_id,
            description=f"Stage 3 version {guardrail_version}: tax/legal/medical denied topics.",
        )
        print(f"  Created guardrail version: {resp['version']}")

    account_id = get_account_id()
    guardrail_arn = f"arn:aws:bedrock:{region}:{account_id}:guardrail/{guardrail_id}"
    return guardrail_id, guardrail_arn


def grant_harness_guardrail_permission(harness_role_name: str, guardrail_arn: str) -> None:
    """Grant the Harness execution role bedrock:ApplyGuardrail, scoped to
    this specific guardrail ARN, per harness-models.html's instructions."""
    iam = boto3.client("iam")
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "FinancialAdvisorGuardrailApply",
                "Effect": "Allow",
                "Action": ["bedrock:ApplyGuardrail"],
                "Resource": [guardrail_arn],
            }
        ],
    }
    put_scoped_policy(iam, harness_role_name, "FinancialAdvisorGuardrailApply", policy)


def poll_harness_status(control, harness_id: str, target_status: str = "READY",
                         timeout: int = HARNESS_POLL_TIMEOUT) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        resp = control.get_harness(harnessId=harness_id)
        status = resp["harness"]["status"]
        print(f"  Harness status: {status}")
        if status == target_status:
            return resp
        if status in HARNESS_FAILURE_STATUSES:
            reason = resp["harness"].get("failureReason", "")
            raise RuntimeError(f"Harness entered {status}. {reason}".strip())
        if time.monotonic() > deadline:
            raise TimeoutError(f"Harness not {target_status} after {timeout}s (current: {status})")
        time.sleep(HARNESS_POLL_INTERVAL)


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
