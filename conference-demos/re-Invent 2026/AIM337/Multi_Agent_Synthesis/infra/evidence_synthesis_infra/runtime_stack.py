# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path

from aws_cdk import (
    Arn,
    ArnComponents,
    ArnFormat,
    AssetHashType,
    BundlingOptions,
    CfnOutput,
    CfnParameter,
    DockerImage,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import (
    aws_bedrockagentcore as agentcore,
)
from aws_cdk import (
    aws_cloudwatch as cloudwatch,
)
from aws_cdk import (
    aws_ec2 as ec2,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_logs as logs,
)
from aws_cdk import (
    aws_s3 as s3,
)
from aws_cdk import (
    aws_s3_assets as s3_assets,
)
from aws_cdk import (
    aws_ssm as ssm,
)
from cdk_nag import NagSuppressions
from constructs import Construct

from evidence_synthesis_infra.bundling import Arm64PythonBundler
from evidence_synthesis_infra.foundation_stack import FoundationStack


class RuntimeStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        foundation: FoundationStack,
        model_ids: Mapping[str, str],
        max_tokens: Mapping[str, int],
        source_path: Path,
        agent_artifact: agentcore.AgentRuntimeArtifact | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        runtime_name = "evidence_synthesis"
        runtime_log_group = logs.LogGroup(
            self,
            "RuntimeLogGroup",
            log_group_name=f"/aws/vendedlogs/bedrock-agentcore/{runtime_name}",
            retention=logs.RetentionDays.THREE_MONTHS,
            removal_policy=RemovalPolicy.RETAIN_ON_UPDATE_OR_DELETE,
        )

        execution_role = iam.Role(
            self,
            "ExecutionRole",
            assumed_by=iam.ServicePrincipal(
                "bedrock-agentcore.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": self.account},
                    "ArnLike": {
                        "aws:SourceArn": (
                            f"arn:{self.partition}:bedrock-agentcore:"
                            f"{self.region}:{self.account}:*"
                        )
                    },
                },
            ),
            description="Least-privilege execution role for evidence synthesis.",
        )
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="InvokeConfiguredModels",
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=self._model_resources(model_ids.values()),
            )
        )
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="ReadEvidence",
                actions=["s3:GetObject", "s3:GetObjectVersion"],
                resources=[foundation.input_bucket.arn_for_objects("sources/*")],
            )
        )
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="WriteArtifacts",
                actions=["s3:PutObject"],
                resources=[
                    foundation.results_bucket.arn_for_objects("runs/*"),
                    foundation.results_bucket.arn_for_objects("reservations/*"),
                ],
            )
        )
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="ApplyGuardrail",
                actions=["bedrock:ApplyGuardrail"],
                resources=[foundation.guardrail.attr_guardrail_arn],
            )
        )
        log_arn_prefix = (
            f"arn:{self.partition}:logs:{self.region}:{self.account}:log-group:"
        )
        log_group_arns = [
            f"{log_arn_prefix}/aws/bedrock-agentcore/runtimes/{runtime_name}-*",
            f"{log_arn_prefix}/aws/vendedlogs/bedrock-agentcore/{runtime_name}",
        ]
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="RuntimeLogGroups",
                actions=[
                    "logs:CreateLogGroup",
                    "logs:DescribeLogStreams",
                    "logs:PutResourcePolicy",
                ],
                resources=log_group_arns,
            )
        )
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="RuntimeLogStreams",
                actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                resources=[f"{arn}:log-stream:*" for arn in log_group_arns],
            )
        )
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="DiscoverRuntimeLogGroups",
                actions=["logs:DescribeLogGroups"],
                resources=[f"{log_arn_prefix}*"],
            )
        )
        # X-Ray trace APIs require "*"; limit requests to the deployment Region.
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="RuntimeTraces",
                actions=[
                    "xray:GetSamplingRules",
                    "xray:GetSamplingTargets",
                    "xray:PutTelemetryRecords",
                    "xray:PutTraceSegments",
                ],
                resources=["*"],
                conditions={"StringEquals": {"aws:RequestedRegion": self.region}},
            )
        )
        # AgentCore service metrics require "*" with namespace and Region conditions.
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="AgentCoreServiceMetrics",
                actions=["cloudwatch:PutMetricData"],
                resources=["*"],
                conditions={
                    "StringEquals": {
                        "cloudwatch:namespace": "bedrock-agentcore",
                        "aws:RequestedRegion": self.region,
                    }
                },
            )
        )

        artifact = agent_artifact or self._build_artifact(source_path, execution_role)
        runtime = agentcore.Runtime(
            self,
            "Runtime",
            runtime_name=runtime_name,
            description="Four-agent, contract-first evidence synthesis pipeline.",
            agent_runtime_artifact=artifact,
            authorizer_configuration=agentcore.RuntimeAuthorizerConfiguration.using_iam(),
            protocol_configuration=agentcore.ProtocolType.HTTP,
            # Prevent CDK's broad defaults from expanding this explicit role.
            execution_role=execution_role.without_policy_updates(),
            network_configuration=agentcore.RuntimeNetworkConfiguration.using_vpc(
                self,
                vpc=foundation.vpc,
                vpc_subnets=ec2.SubnetSelection(
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
                ),
                security_groups=[foundation.runtime_security_group],
            ),
            lifecycle_configuration=agentcore.LifecycleConfiguration(
                idle_runtime_session_timeout=Duration.seconds(300),
                max_lifetime=Duration.seconds(900),
            ),
            logging_configs=[
                agentcore.LoggingConfig(
                    log_type=agentcore.LogType.APPLICATION_LOGS,
                    destination=agentcore.LoggingDestination.cloud_watch_logs(
                        runtime_log_group
                    ),
                ),
                agentcore.LoggingConfig(
                    log_type=agentcore.LogType.USAGE_LOGS,
                    destination=agentcore.LoggingDestination.cloud_watch_logs(
                        runtime_log_group
                    ),
                ),
            ],
            manage_delivery_resource_policy=False,
            tracing_enabled=True,
            environment_variables={
                "AWS_REGION": self.region,
                # Stage logs and EMF metrics remain; model-content telemetry is disabled.
                "OTEL_SDK_DISABLED": "true",
                "EVIDENCE_EXTRACTOR_MODEL_ID": model_ids["evidence_extractor"],
                "ANALYST_MODEL_ID": model_ids["analyst"],
                "VERIFIER_MODEL_ID": model_ids["verifier"],
                "SYNTHESIZER_MODEL_ID": model_ids["synthesizer"],
                "EVIDENCE_EXTRACTOR_MAX_TOKENS": str(max_tokens["evidence_extractor"]),
                "ANALYST_MAX_TOKENS": str(max_tokens["analyst"]),
                "VERIFIER_MAX_TOKENS": str(max_tokens["verifier"]),
                "SYNTHESIZER_MAX_TOKENS": str(max_tokens["synthesizer"]),
                "PIPELINE_DEADLINE_SECONDS": "300",
                "PIPELINE_TOKEN_BUDGET": "200000",
                "PIPELINE_MAX_RETRIES": "1",
                "PIPELINE_MAX_VERIFICATION_CYCLES": "2",
                "SOURCE_BUCKET": foundation.input_bucket.bucket_name,
                "SOURCE_PREFIX": "sources",
                "ARTIFACT_BUCKET": foundation.results_bucket.bucket_name,
                "ARTIFACT_PREFIX": "runs",
                "BEDROCK_GUARDRAIL_ID": foundation.guardrail.attr_guardrail_id,
                "BEDROCK_GUARDRAIL_VERSION": foundation.guardrail_version.attr_version,
                "UNIFIED_TRACES_DESTINATION_ENABLED": "true",
                "LOG_LEVEL": "INFO",
            },
        )
        live_endpoint = runtime.add_endpoint(
            "live",
            description="Live evidence-synthesis runtime endpoint.",
            version=runtime.agent_runtime_version,
        )

        operator = CfnParameter(
            self, "OperatorPrincipalArn",
            type="String",
            description="Approved IAM user or role for this demo's single trusted data scope.",
            allowed_pattern=r"^arn:aws[a-z-]*:iam::[0-9]{12}:(role|user)/[A-Za-z0-9+=,.@_/-]+$",
        )
        invoker_role = iam.Role(
            self,
            "InvokerRole",
            assumed_by=iam.ArnPrincipal(operator.value_as_string).with_conditions({
                "StringEquals": {"aws:PrincipalAccount": self.account},
            }),
            description="Invocation and result access for the approved demo operator.",
        )
        runtime.grant_invoke(invoker_role)
        invoker_role.add_to_policy(
            iam.PolicyStatement(
                sid="UploadUiSources",
                actions=["s3:PutObject"],
                resources=[
                    foundation.input_bucket.arn_for_objects("sources/ui/*")
                ],
            )
        )
        invoker_role.add_to_policy(
            iam.PolicyStatement(
                sid="ListRunArtifacts",
                actions=["s3:ListBucket"],
                resources=[foundation.results_bucket.bucket_arn],
                conditions={"StringLike": {"s3:prefix": ["runs/*"]}},
            )
        )
        invoker_role.add_to_policy(
            iam.PolicyStatement(
                sid="ReadRunArtifacts",
                actions=["s3:GetObject"],
                resources=[foundation.results_bucket.arn_for_objects("runs/*")],
            )
        )
        invoker_role.add_to_policy(
            iam.PolicyStatement(
                sid="ReadRuntimeStageLogs",
                actions=["logs:FilterLogEvents"],
                resources=[
                    Arn.format(
                        ArnComponents(
                            service="logs",
                            resource="log-group",
                            resource_name=(
                                f"/aws/bedrock-agentcore/runtimes/{runtime.agent_runtime_id}-*:*"
                            ),
                            arn_format=ArnFormat.COLON_RESOURCE_NAME,
                        ),
                        self,
                    )
                ],
            )
        )
        NagSuppressions.add_resource_suppressions(
            execution_role,
            [
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": (
                        "Wildcards are limited to required cross-region foundation-model "
                        "ARNs, object prefixes, X-Ray and CloudWatch APIs that do not "
                        "support resource scoping, and this runtime's log group paths."
                    ),
                }
            ],
            apply_to_children=True,
        )
        NagSuppressions.add_resource_suppressions(
            invoker_role,
            [
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": (
                        "AgentCore invocation authorization requires the runtime endpoint "
                        "resource suffix beneath the generated runtime ARN, and UI uploads "
                        "are restricted to the sources/ui object prefix. Artifact reads "
                        "and listings are restricted to the results bucket's runs/ prefix. "
                        "Log reads are restricted to this runtime's endpoint log groups."
                    ),
                }
            ],
            apply_to_children=True,
        )

        dashboard = cloudwatch.Dashboard(
            self,
            "Dashboard",
            dashboard_name="evidence-synthesis",
        )
        dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="Invocations and errors",
                left=[
                    runtime.metric_invocations(statistic="Sum"),
                    runtime.metric_system_errors(statistic="Sum"),
                    runtime.metric_user_errors(statistic="Sum"),
                    runtime.metric_throttles(statistic="Sum"),
                ],
            ),
            cloudwatch.GraphWidget(
                title="End-to-end latency",
                left=[runtime.metric_latency(statistic="p95")],
            ),
            cloudwatch.GraphWidget(
                title="Sessions",
                left=[runtime.metric_session_count(statistic="Sum")],
            ),
            cloudwatch.GraphWidget(
                title="Pipeline token usage",
                left=[
                    cloudwatch.Metric(
                        namespace="EvidenceSynthesis",
                        metric_name="TotalTokens",
                        dimensions_map={"Runtime": "evidence-synthesis"},
                        statistic="Sum",
                        period=Duration.minutes(5),
                    ),
                    cloudwatch.Metric(
                        namespace="EvidenceSynthesis",
                        metric_name="Runs",
                        dimensions_map={"Runtime": "evidence-synthesis"},
                        statistic="Sum",
                        period=Duration.minutes(5),
                    ),
                ],
            ),
        )

        parameters = {
            "RuntimeArnParameter": (
                "/evidence-synthesis/runtime-arn",
                runtime.agent_runtime_arn,
            ),
            "EndpointArnParameter": (
                "/evidence-synthesis/endpoint-arn",
                live_endpoint.agent_runtime_endpoint_arn,
            ),
            "EndpointNameParameter": (
                "/evidence-synthesis/endpoint-name",
                live_endpoint.endpoint_name,
            ),
            "InvokerRoleArnParameter": (
                "/evidence-synthesis/invoker-role-arn",
                invoker_role.role_arn,
            ),
            "InputBucketParameter": (
                "/evidence-synthesis/input-bucket",
                foundation.input_bucket.bucket_name,
            ),
            "ResultsBucketParameter": (
                "/evidence-synthesis/results-bucket",
                foundation.results_bucket.bucket_name,
            ),
            "DashboardParameter": (
                "/evidence-synthesis/dashboard",
                dashboard.dashboard_name,
            ),
        }
        for parameter_id, (name, value) in parameters.items():
            ssm.StringParameter(
                self,
                parameter_id,
                parameter_name=name,
                string_value=value,
            )

        for output_id, value in (
            ("RuntimeArn", runtime.agent_runtime_arn),
            ("LiveEndpointArn", live_endpoint.agent_runtime_endpoint_arn),
            ("InvokerRoleArn", invoker_role.role_arn),
            ("InputBucketName", foundation.input_bucket.bucket_name),
            ("ResultsBucketName", foundation.results_bucket.bucket_name),
            ("DashboardName", dashboard.dashboard_name),
        ):
            CfnOutput(self, output_id, value=value)

    def _build_artifact(
        self, source_path: Path, execution_role: iam.Role
    ) -> agentcore.AgentRuntimeArtifact:
        code_asset = s3_assets.Asset(
            self,
            "RuntimeCode",
            path=str(source_path),
            asset_hash=self._source_hash(source_path),
            asset_hash_type=AssetHashType.CUSTOM,
            bundling=BundlingOptions(
                image=DockerImage.from_registry(
                    "public.ecr.aws/sam/build-python3.12@"
                    "sha256:1ccda0a9669eb11751f0b4d9fe9107a964d29a2379590aab958137c928d971f8"
                ),
                local=Arm64PythonBundler(source_path),
                platform="linux/arm64",
                command=[
                    "bash",
                    "-c",
                    " && ".join(
                        [
                            "cd /asset-input",
                            (
                                "python -I -m pip --isolated install --require-hashes "
                                "-r requirements-build.txt"
                            ),
                            (
                                "python -I -m pip --isolated install --target /asset-output "
                                "--require-hashes -r requirements-runtime.txt"
                            ),
                            (
                                "python -I -m pip --isolated install --target /asset-output "
                                "--no-deps --no-build-isolation '.[aws]'"
                            ),
                            (
                                "find /asset-output -type d "
                                "\\( -name __pycache__ -o -name tests \\) "
                                "-prune -exec rm -rf {} +"
                            ),
                            "find /asset-output -type f -name '*.pyc' -delete",
                            (
                                "cp /asset-input/deploy/agentcore_main.py "
                                "/asset-output/main.py"
                            ),
                            "chmod 755 /asset-output/main.py",
                            "chmod -R a+rX /asset-output",
                        ]
                    ),
                ],
            ),
        )
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="ReadRuntimeCode",
                actions=["s3:GetObject"],
                resources=[code_asset.bucket.arn_for_objects(code_asset.s3_object_key)],
            )
        )
        if code_asset.bucket.encryption_key is not None:
            code_asset.bucket.encryption_key.grant_decrypt(execution_role)
        return agentcore.AgentRuntimeArtifact.from_s3(
            s3.Location(
                bucket_name=code_asset.s3_bucket_name,
                object_key=code_asset.s3_object_key,
            ),
            agentcore.AgentCoreRuntime.PYTHON_3_12,
            ["opentelemetry-instrument", "main.py"],
        )

    def _model_resources(self, model_ids: object) -> list[str]:
        resources: set[str] = set()
        for model_id in model_ids:
            value = str(model_id)
            if value.startswith(("us.", "eu.", "apac.")):
                resources.add(
                    Arn.format(
                        ArnComponents(
                            service="bedrock",
                            resource="inference-profile",
                            resource_name=value,
                        ),
                        self,
                    )
                )
                value = value.split(".", maxsplit=1)[1]
            resources.add(
                f"arn:{self.partition}:bedrock:*::foundation-model/{value}"
            )
        return sorted(resources)

    @staticmethod
    def _source_hash(source_path: Path) -> str:
        digest = hashlib.sha256()
        files = [
            source_path / "pyproject.toml",
            source_path / "requirements-runtime.txt",
            source_path / "requirements-build.txt",
            source_path / "README.md",
            source_path / "LICENSE",
            source_path / "deploy" / "agentcore_main.py",
            *(source_path / "src" / "evidence_synthesis").rglob("*"),
        ]
        for path in sorted(
            item for item in files if item.is_file() and "__pycache__" not in item.parts
        ):
            digest.update(str(path.relative_to(source_path)).encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()
