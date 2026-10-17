# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
import json
from pathlib import Path

import aws_cdk as cdk
from aws_cdk import assertions
from aws_cdk import aws_bedrockagentcore as agentcore
from aws_cdk import aws_s3 as s3
from evidence_synthesis_infra import FoundationStack, RuntimeStack

ACCOUNT = "111122223333"
REGION = "us-east-1"
AGENTCORE_AZ_IDS = ["use1-az4", "use1-az1"]
AGENTCORE_AZ_NAMES = ["us-east-1a", "us-east-1c"]
MODELS = {
    "evidence_extractor": "us.amazon.nova-lite-v1:0",
    "analyst": "us.anthropic.claude-sonnet-5",
    "verifier": "us.anthropic.claude-sonnet-5",
    "synthesizer": "us.anthropic.claude-sonnet-5",
}
MAX_TOKENS = {
    "evidence_extractor": 4096,
    "analyst": 6144,
    "verifier": 8192,
    "synthesizer": 4096,
}


def _stacks() -> tuple[FoundationStack, RuntimeStack]:
    app = cdk.App(context={"@aws-cdk/aws-s3:serverAccessLogsUseBucketPolicy": True})
    environment = cdk.Environment(account=ACCOUNT, region=REGION)
    foundation = FoundationStack(
        app,
        "Foundation",
        env=environment,
        availability_zones=AGENTCORE_AZ_NAMES,
        replacement_subnet_cidr="10.42.2.0/24",
    )
    artifact = agentcore.AgentRuntimeArtifact.from_s3(
        s3.Location(bucket_name="code-assets", object_key="agent.zip"),
        agentcore.AgentCoreRuntime.PYTHON_3_12,
        ["python", "main.py"],
    )
    runtime = RuntimeStack(
        app,
        "Runtime",
        env=environment,
        foundation=foundation,
        model_ids=MODELS,
        max_tokens=MAX_TOKENS,
        source_path=Path("."),
        agent_artifact=artifact,
    )
    return foundation, runtime


def test_foundation_has_isolated_vpc_data_buckets_endpoints_and_guardrail() -> None:
    foundation, _ = _stacks()
    template = assertions.Template.from_stack(foundation)

    template.resource_count_is("AWS::EC2::NatGateway", 0)
    template.resource_count_is("AWS::EC2::InternetGateway", 0)
    template.resource_count_is("AWS::EC2::FlowLog", 0)
    template.resource_count_is("AWS::EC2::Subnet", 2)
    template.resource_count_is("AWS::S3::Bucket", 3)
    template.resource_count_is("AWS::EC2::VPCEndpoint", 5)
    template.resource_count_is("AWS::Bedrock::Guardrail", 1)
    template.resource_count_is("AWS::Bedrock::GuardrailVersion", 1)
    retained_resources = {
        **template.find_resources("AWS::S3::Bucket"),
        **template.find_resources("AWS::Bedrock::Guardrail"),
        **template.find_resources("AWS::Bedrock::GuardrailVersion"),
    }
    assert retained_resources
    assert all(
        resource["DeletionPolicy"] == "RetainExceptOnCreate"
        and resource["UpdateReplacePolicy"] == "Retain"
        for resource in retained_resources.values()
    )
    subnets = template.find_resources("AWS::EC2::Subnet")
    subnets_by_cidr = {
        resource["Properties"]["CidrBlock"]: resource["Properties"]
        for resource in subnets.values()
    }
    assert subnets_by_cidr["10.42.0.0/24"]["AvailabilityZone"] == "us-east-1a"
    assert subnets_by_cidr["10.42.2.0/24"]["AvailabilityZone"] == "us-east-1c"
    assert "10.42.1.0/24" not in subnets_by_cidr
    endpoints = template.find_resources("AWS::EC2::VPCEndpoint")
    gateway_endpoint = next(
        resource
        for resource in endpoints.values()
        if resource["Properties"]["VpcEndpointType"] == "Gateway"
    )
    gateway_policy = json.dumps(gateway_endpoint["Properties"]["PolicyDocument"])
    assert "cdk-hnb659fds-assets-" in gateway_policy
    assert "AWS::AccountId" in gateway_policy
    assert "AWS::Region" in gateway_policy
    assert ACCOUNT not in gateway_policy
    assert "acr-code-*" in gateway_policy
    template.has_resource_properties(
        "AWS::S3::Bucket",
        assertions.Match.object_like(
            {
                "BucketEncryption": {
                    "ServerSideEncryptionConfiguration": [
                        {
                            "ServerSideEncryptionByDefault": {
                                "SSEAlgorithm": "AES256"
                            }
                        }
                    ]
                },
                "PublicAccessBlockConfiguration": {
                    "BlockPublicAcls": True,
                    "BlockPublicPolicy": True,
                    "IgnorePublicAcls": True,
                    "RestrictPublicBuckets": True,
                },
                "VersioningConfiguration": {"Status": "Enabled"},
            }
        ),
    )

    guardrails = template.find_resources("AWS::Bedrock::Guardrail")
    guardrail = next(iter(guardrails.values()))
    filters = guardrail["Properties"]["ContentPolicyConfig"]["FiltersConfig"]
    prompt_attack = next(item for item in filters if item["Type"] == "PROMPT_ATTACK")
    assert prompt_attack == {
        "InputAction": "BLOCK",
        "InputEnabled": True,
        "InputStrength": "HIGH",
        "OutputStrength": "NONE",
        "Type": "PROMPT_ATTACK",
    }


def test_data_bucket_access_logs_have_scoped_delivery_and_bounded_retention() -> None:
    foundation, _ = _stacks()
    template = assertions.Template.from_stack(foundation)
    buckets = template.find_resources("AWS::S3::Bucket")
    sources = {
        key: value["Properties"]
        for key, value in buckets.items()
        if "LoggingConfiguration" in value["Properties"]
    }
    assert len(sources) == 2
    destination_ids = {
        value["LoggingConfiguration"]["DestinationBucketName"]["Ref"]
        for value in sources.values()
    }
    assert len(destination_ids) == 1
    destination_id = destination_ids.pop()
    assert destination_id not in sources
    destination = buckets[destination_id]["Properties"]
    assert "LoggingConfiguration" not in destination
    assert destination["OwnershipControls"]["Rules"] == [
        {"ObjectOwnership": "BucketOwnerEnforced"}
    ]
    assert destination["LifecycleConfiguration"]["Rules"][0]["ExpirationInDays"] == 90
    assert destination["LifecycleConfiguration"]["Rules"][0][
        "NoncurrentVersionExpiration"
    ]["NoncurrentDays"] == 90
    assert {
        value["LoggingConfiguration"]["LogFilePrefix"] for value in sources.values()
    } == {"InputBucket/", "ResultsBucket/"}

    policies = template.find_resources("AWS::S3::BucketPolicy")
    delivery = [
        statement
        for policy in policies.values()
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]
        if statement.get("Principal") == {"Service": "logging.s3.amazonaws.com"}
    ]
    assert len(delivery) == 2
    for statement in delivery:
        assert statement["Action"] == "s3:PutObject"
        assert statement["Condition"]["StringEquals"]["aws:SourceAccount"] == ACCOUNT
        assert statement["Condition"]["ArnLike"]["aws:SourceArn"]["Fn::GetAtt"][0] in sources
        assert destination_id in json.dumps(statement["Resource"])


def test_runtime_has_agentcore_iam_logs_dashboard_and_no_alerting() -> None:
    _, runtime = _stacks()
    template = assertions.Template.from_stack(runtime)

    template.resource_count_is("AWS::BedrockAgentCore::Runtime", 1)
    template.resource_count_is("AWS::BedrockAgentCore::RuntimeEndpoint", 1)
    template.resource_count_is("AWS::CloudWatch::Dashboard", 1)
    template.resource_count_is("AWS::CloudWatch::Alarm", 0)
    template.resource_count_is("AWS::SNS::Topic", 0)
    template.resource_count_is("AWS::ECR::Repository", 0)
    template.resource_count_is("AWS::ApiGateway::RestApi", 0)
    template.has_resource_properties(
        "AWS::BedrockAgentCore::Runtime",
        assertions.Match.object_like(
            {
                "AgentRuntimeName": "evidence_synthesis",
                "ProtocolConfiguration": "HTTP",
                "EnvironmentVariables": assertions.Match.object_like(
                    {
                        "ANALYST_MODEL_ID": "us.anthropic.claude-sonnet-5",
                        "VERIFIER_MODEL_ID": "us.anthropic.claude-sonnet-5",
                        "SYNTHESIZER_MODEL_ID": "us.anthropic.claude-sonnet-5",
                        "EVIDENCE_EXTRACTOR_MAX_TOKENS": "4096",
                        "ANALYST_MAX_TOKENS": "6144",
                        "VERIFIER_MAX_TOKENS": "8192",
                        "SYNTHESIZER_MAX_TOKENS": "4096",
                        "PIPELINE_DEADLINE_SECONDS": "300",
                    }
                ),
                "NetworkConfiguration": assertions.Match.object_like(
                    {"NetworkMode": "VPC"}
                ),
                "LifecycleConfiguration": {
                    "IdleRuntimeSessionTimeout": 300,
                    "MaxLifetime": 900,
                },
            }
        ),
    )
    template.resource_count_is("AWS::SSM::Parameter", 7)
    policies = json.dumps(template.find_resources("AWS::IAM::Policy"))
    assert "s3:PutObject" in policies
    assert "sources/ui/*" in policies
    statements = [
        statement
        for resource in template.find_resources("AWS::IAM::Policy").values()
        for statement in resource["Properties"]["PolicyDocument"]["Statement"]
    ]
    listing = next(item for item in statements if item.get("Sid") == "ListRunArtifacts")
    assert listing["Action"] == "s3:ListBucket"
    assert listing["Condition"] == {"StringLike": {"s3:prefix": ["runs/*"]}}
    reading = next(item for item in statements if item.get("Sid") == "ReadRunArtifacts")
    assert reading["Action"] == "s3:GetObject"
    assert "/runs/*" in json.dumps(reading["Resource"])
    log_reading = next(item for item in statements if item.get("Sid") == "ReadRuntimeStageLogs")
    assert log_reading["Action"] == "logs:FilterLogEvents"
    assert "/aws/bedrock-agentcore/runtimes/" in json.dumps(log_reading["Resource"])
    assert "AgentRuntimeId" in json.dumps(log_reading["Resource"])


def test_observability_permissions_are_scoped_without_breaking_trace_ingestion() -> None:
    _, runtime = _stacks()
    template = assertions.Template.from_stack(runtime)
    statements = [
        statement
        for policy in template.find_resources("AWS::IAM::Policy").values()
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]
    ]
    by_sid = {statement.get("Sid"): statement for statement in statements}
    log_groups = json.dumps(by_sid["RuntimeLogGroups"]["Resource"])
    assert "/aws/bedrock-agentcore/runtimes/evidence_synthesis-*" in log_groups
    assert "/aws/vendedlogs/bedrock-agentcore/evidence_synthesis" in log_groups
    assert ACCOUNT in log_groups and REGION in log_groups
    log_streams = json.dumps(by_sid["RuntimeLogStreams"]["Resource"])
    assert ":log-stream:*" in log_streams
    assert by_sid["RuntimeLogGroups"]["Resource"] != "*"
    assert by_sid["DiscoverRuntimeLogGroups"]["Resource"] != "*"
    traces = by_sid["RuntimeTraces"]
    assert traces["Resource"] == "*"
    assert traces["Condition"] == {"StringEquals": {"aws:RequestedRegion": REGION}}
    assert "xray:PutTraceSegments" in traces["Action"]
    metrics = [
        statement for statement in statements
        if "cloudwatch:PutMetricData" in statement["Action"]
    ]
    assert len(metrics) == 1
    assert metrics[0]["Condition"] == {
        "StringEquals": {
            "cloudwatch:namespace": "bedrock-agentcore",
            "aws:RequestedRegion": REGION,
        }
    }
    assert "GetAgentAccessToken" not in by_sid
    assert "XRayAccess" not in by_sid
    assert "LogGroupAccess" not in by_sid


def test_runtime_asset_hash_tracks_locked_dependencies_and_ignores_bytecode(tmp_path: Path) -> None:
    package = tmp_path / "src" / "evidence_synthesis"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    constraints = tmp_path / "requirements-runtime.txt"
    constraints.write_text("example==1.0\n")
    original = RuntimeStack._source_hash(tmp_path)

    bytecode = package / "__pycache__"
    bytecode.mkdir()
    (bytecode / "__init__.cpython-312.pyc").write_bytes(b"generated")
    assert RuntimeStack._source_hash(tmp_path) == original

    constraints.write_text("example==1.1\n")
    assert RuntimeStack._source_hash(tmp_path) != original


def test_invocation_trust_reservations_and_retention_match_the_demo_boundary() -> None:
    foundation, runtime = _stacks()
    runtime_template = assertions.Template.from_stack(runtime)
    roles = runtime_template.find_resources("AWS::IAM::Role")
    operator_role = next(value for key, value in roles.items() if key.startswith("InvokerRole"))
    trust = operator_role["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]
    assert trust["Principal"]["AWS"] == {"Ref": "OperatorPrincipalArn"}
    assert trust["Condition"]["StringEquals"]["aws:PrincipalAccount"] == ACCOUNT
    policies = json.dumps(runtime_template.find_resources("AWS::IAM::Policy"))
    assert "reservations/*" in policies
    assert "InvokeAgentRuntimeCommand" not in policies
    runtime_template.has_resource_properties(
        "AWS::BedrockAgentCore::Runtime",
        assertions.Match.object_like({
            "EnvironmentVariables": assertions.Match.object_like({"OTEL_SDK_DISABLED": "true"}),
        }),
    )
    foundation_template = assertions.Template.from_stack(foundation)
    statements = [
        item for policy in foundation_template.find_resources("AWS::S3::BucketPolicy").values()
        for item in policy["Properties"]["PolicyDocument"]["Statement"]
    ]
    reservation = next(
        item for item in statements if item.get("Sid") == "RequireAtomicRunReservations"
    )
    assert reservation["Effect"] == "Deny"
    assert reservation["Condition"] == {"StringNotEquals": {"s3:if-none-match": "*"}}
    lifecycle_rules = [
        rule for bucket in foundation_template.find_resources("AWS::S3::Bucket").values()
        for rule in bucket["Properties"].get("LifecycleConfiguration", {}).get("Rules", [])
    ]
    retained = {rule.get("Prefix"): rule for rule in lifecycle_rules}
    assert retained["runs/"]["ExpirationInDays"] == 7
    assert retained["sources/ui/"]["ExpirationInDays"] == 7
    assert "reservations/" not in retained
