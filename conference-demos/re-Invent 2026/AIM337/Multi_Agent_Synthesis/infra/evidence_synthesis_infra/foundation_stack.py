# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

from collections.abc import Sequence

from aws_cdk import Aws, Duration, RemovalPolicy, Stack
from aws_cdk import (
    aws_bedrock as bedrock,
)
from aws_cdk import (
    aws_ec2 as ec2,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_s3 as s3,
)
from cdk_nag import NagSuppressions
from constructs import Construct


class FoundationStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        availability_zones: Sequence[str],
        replacement_subnet_cidr: str,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        if len(availability_zones) != 2 or len(set(availability_zones)) != 2:
            raise ValueError("Provide exactly two unique AgentCore-supported AZ names")

        self.vpc = ec2.Vpc(
            self,
            "Vpc",
            ip_addresses=ec2.IpAddresses.cidr("10.42.0.0/16"),
            availability_zones=list(availability_zones),
            nat_gateways=0,
            enable_dns_hostnames=True,
            enable_dns_support=True,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="isolated",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24,
                )
            ],
        )
        replacement_subnet = self.vpc.isolated_subnets[1].node.default_child
        if not isinstance(replacement_subnet, ec2.CfnSubnet):
            raise TypeError("Expected the VPC subnet default child to be CfnSubnet")
        replacement_subnet.cidr_block = replacement_subnet_cidr

        self.runtime_security_group = ec2.SecurityGroup(
            self,
            "RuntimeSecurityGroup",
            vpc=self.vpc,
            allow_all_outbound=False,
            description="Outbound access for the AgentCore evidence-synthesis runtime.",
        )
        endpoint_security_group = ec2.SecurityGroup(
            self,
            "EndpointSecurityGroup",
            vpc=self.vpc,
            allow_all_outbound=False,
            description="Accept HTTPS from the AgentCore runtime to AWS service endpoints.",
        )
        endpoint_security_group.add_ingress_rule(
            self.runtime_security_group,
            ec2.Port.tcp(443),
            "HTTPS from the AgentCore runtime",
        )
        self.runtime_security_group.add_egress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(443),
            "HTTPS through VPC endpoints; isolated subnets have no internet route",
        )

        isolated_subnets = ec2.SubnetSelection(
            subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
        )
        for endpoint_id, service in (
            ("BedrockRuntimeEndpoint", ec2.InterfaceVpcEndpointAwsService.BEDROCK_RUNTIME),
            ("CloudWatchLogsEndpoint", ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS),
            (
                "CloudWatchMonitoringEndpoint",
                ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_MONITORING,
            ),
            ("XRayEndpoint", ec2.InterfaceVpcEndpointAwsService.XRAY),
        ):
            ec2.InterfaceVpcEndpoint(
                self,
                endpoint_id,
                vpc=self.vpc,
                service=service,
                private_dns_enabled=True,
                open=False,
                security_groups=[endpoint_security_group],
                subnets=isolated_subnets,
            )

        # Keep access logs separate from the source and result objects.
        self.access_logs_bucket = s3.Bucket(
            self,
            "AccessLogsBucket",
            encryption=s3.BucketEncryption.S3_MANAGED,
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            lifecycle_rules=[
                s3.LifecycleRule(
                    expiration=Duration.days(90),
                    noncurrent_version_expiration=Duration.days(90),
                )
            ],
            removal_policy=RemovalPolicy.RETAIN_ON_UPDATE_OR_DELETE,
        )
        # A log destination must not log to itself and create recursive logs.
        log_bucket = self.access_logs_bucket.node.default_child
        if not isinstance(log_bucket, s3.CfnBucket):
            raise TypeError("Expected the access log bucket's CloudFormation resource")
        log_bucket.add_metadata("checkov", {"skip": [{
            "id": "CKV_AWS_18",
            "comment": (
                "Dedicated access-log destination. AWS recommends disabling server "
                "access logging on the destination to prevent recursive log delivery."
            ),
        }]})
        NagSuppressions.add_resource_suppressions(
            self.access_logs_bucket,
            [{
                "id": "AwsSolutions-S1",
                "reason": "Dedicated access-log destination; self-logging creates recursive logs.",
            }],
        )

        self.input_bucket = self._bucket(
            "InputBucket",
            lifecycle_rules=[s3.LifecycleRule(
                prefix="sources/ui/",
                expiration=Duration.days(7),
                noncurrent_version_expiration=Duration.days(7),
            )],
        )
        self.results_bucket = self._bucket(
            "ResultsBucket",
            lifecycle_rules=[
                s3.LifecycleRule(
                    prefix="runs/",
                    expiration=Duration.days(7),
                    noncurrent_version_expiration=Duration.days(7),
                )
            ],
        )
        self.results_bucket.add_to_resource_policy(iam.PolicyStatement(
            sid="RequireAtomicRunReservations",
            effect=iam.Effect.DENY,
            principals=[iam.AnyPrincipal()],
            actions=["s3:PutObject"],
            resources=[self.results_bucket.arn_for_objects("reservations/*")],
            conditions={"StringNotEquals": {"s3:if-none-match": "*"}},
        ))

        s3_endpoint = self.vpc.add_gateway_endpoint(
            "S3Endpoint",
            service=ec2.GatewayVpcEndpointAwsService.S3,
            subnets=[isolated_subnets],
        )
        s3_endpoint.add_to_policy(
            iam.PolicyStatement(
                sid="ApplicationData",
                principals=[iam.AnyPrincipal()],
                actions=["s3:GetObject", "s3:GetObjectVersion", "s3:PutObject"],
                resources=[
                    self.input_bucket.arn_for_objects("sources/*"),
                    self.results_bucket.arn_for_objects("runs/*"),
                    self.results_bucket.arn_for_objects("reservations/*"),
                    (
                        f"arn:{Aws.PARTITION}:s3:::"
                        f"cdk-hnb659fds-assets-{Aws.ACCOUNT_ID}-{Aws.REGION}/*"
                    ),
                ],
                conditions={"StringEquals": {"aws:PrincipalAccount": Aws.ACCOUNT_ID}},
            )
        )
        s3_endpoint.add_to_policy(
            iam.PolicyStatement(
                sid="AgentCoreCodeBootstrap",
                principals=[iam.AnyPrincipal()],
                actions=["s3:GetObject"],
                resources=[
                    f"arn:{self.partition}:s3:::acr-code-*-{self.region}-an",
                    f"arn:{self.partition}:s3:::acr-code-*-{self.region}-an/*",
                ],
                conditions={
                    "StringEquals": {
                        "aws:PrincipalServiceName": "bedrock-agentcore.amazonaws.com"
                    }
                },
            )
        )

        self.guardrail = bedrock.CfnGuardrail(
            self,
            "Guardrail",
            name="evidence-synthesis",
            description="Safety boundary for accountable evidence synthesis.",
            blocked_input_messaging=(
                "This system supports evidence synthesis and planning. It does not make "
                "individual eligibility, insurance, legal, medical, or financial decisions."
            ),
            blocked_outputs_messaging=(
                "The response was blocked by the evidence-synthesis safety policy."
            ),
            content_policy_config=bedrock.CfnGuardrail.ContentPolicyConfigProperty(
                filters_config=[
                    self._content_filter("PROMPT_ATTACK", "HIGH"),
                    self._content_filter("HATE", "MEDIUM"),
                    self._content_filter("INSULTS", "MEDIUM"),
                    self._content_filter("SEXUAL", "MEDIUM"),
                    self._content_filter("VIOLENCE", "MEDIUM"),
                    self._content_filter("MISCONDUCT", "MEDIUM"),
                ]
            ),
            topic_policy_config=bedrock.CfnGuardrail.TopicPolicyConfigProperty(
                topics_config=[
                    bedrock.CfnGuardrail.TopicConfigProperty(
                        name="Individual high-impact decisions",
                        definition=(
                            "Requests or responses that make, recommend, or automate a "
                            "decision about an identifiable individual's insurance, "
                            "eligibility, legal, medical, or financial outcome."
                        ),
                        examples=[
                            "Deny flood insurance to this individual.",
                            "Determine whether this person is medically eligible.",
                        ],
                        type="DENY",
                        input_action="BLOCK",
                        output_action="BLOCK",
                        input_enabled=True,
                        output_enabled=True,
                    )
                ]
            ),
            sensitive_information_policy_config=(
                bedrock.CfnGuardrail.SensitiveInformationPolicyConfigProperty(
                    pii_entities_config=[
                        self._pii_filter("EMAIL", "ANONYMIZE"),
                        self._pii_filter("PHONE", "ANONYMIZE"),
                        self._pii_filter("AWS_ACCESS_KEY", "BLOCK"),
                        self._pii_filter("AWS_SECRET_KEY", "BLOCK"),
                        self._pii_filter("PASSWORD", "BLOCK"),
                        self._pii_filter("US_SOCIAL_SECURITY_NUMBER", "BLOCK"),
                        self._pii_filter("US_BANK_ACCOUNT_NUMBER", "BLOCK"),
                        self._pii_filter("CREDIT_DEBIT_CARD_NUMBER", "BLOCK"),
                    ]
                )
            ),
        )
        # PII filters do not establish compliance for a customer's workload.
        self.guardrail.add_metadata("DataHandling", {
            "Purpose": "Non-production sample. Use synthetic or approved data.",
            "CustomerResponsibility": (
                "Assess applicable HIPAA, PCI-DSS, GDPR, and other obligations for your "
                "data and use case. Configure access, retention, and required safeguards "
                "under the AWS shared responsibility model. Guardrail filters alone "
                "do not establish regulatory compliance."
            ),
            "References": [
                "https://aws.amazon.com/compliance/",
                "https://aws.amazon.com/compliance/shared-responsibility-model/",
            ],
        })
        self.guardrail.apply_removal_policy(RemovalPolicy.RETAIN_ON_UPDATE_OR_DELETE)
        self.guardrail_version = bedrock.CfnGuardrailVersion(
            self,
            "GuardrailVersion",
            guardrail_identifier=self.guardrail.attr_guardrail_id,
            description="Immutable evidence-synthesis safety policy.",
        )
        self.guardrail_version.apply_removal_policy(
            RemovalPolicy.RETAIN_ON_UPDATE_OR_DELETE
        )

        NagSuppressions.add_resource_suppressions(
            self.vpc,
            [
                {
                    "id": "AwsSolutions-VPC7",
                    "reason": (
                        "VPC Flow Logs were explicitly excluded from this deployment; "
                        "AgentCore, CloudTrail, and application logs provide the selected "
                        "audit surface."
                    ),
                }
            ],
            apply_to_children=True,
        )
    def _bucket(
        self,
        construct_id: str,
        *,
        lifecycle_rules: list[s3.LifecycleRule] | None = None,
    ) -> s3.Bucket:
        return s3.Bucket(
            self,
            construct_id,
            encryption=s3.BucketEncryption.S3_MANAGED,
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            server_access_logs_bucket=self.access_logs_bucket,
            server_access_logs_prefix=f"{construct_id}/",
            lifecycle_rules=lifecycle_rules,
            removal_policy=RemovalPolicy.RETAIN_ON_UPDATE_OR_DELETE,
        )

    @staticmethod
    def _content_filter(
        filter_type: str,
        strength: str,
    ) -> bedrock.CfnGuardrail.ContentFilterConfigProperty:
        if filter_type == "PROMPT_ATTACK":
            return bedrock.CfnGuardrail.ContentFilterConfigProperty(
                type=filter_type,
                input_strength=strength,
                output_strength="NONE",
                input_action="BLOCK",
                input_enabled=True,
            )
        return bedrock.CfnGuardrail.ContentFilterConfigProperty(
            type=filter_type,
            input_strength=strength,
            output_strength=strength,
            input_action="BLOCK",
            output_action="BLOCK",
            input_enabled=True,
            output_enabled=True,
        )

    @staticmethod
    def _pii_filter(
        pii_type: str,
        action: str,
    ) -> bedrock.CfnGuardrail.PiiEntityConfigProperty:
        return bedrock.CfnGuardrail.PiiEntityConfigProperty(
            type=pii_type,
            action=action,
            input_action=action,
            output_action=action,
            input_enabled=True,
            output_enabled=True,
        )
