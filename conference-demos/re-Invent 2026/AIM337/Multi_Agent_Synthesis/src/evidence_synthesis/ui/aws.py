# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config

from evidence_synthesis.agentcore_client import AgentCoreRuntimeClient
from evidence_synthesis.ui.artifact_progress import S3ArtifactReader
from evidence_synthesis.ui.log_progress import CloudWatchLogReader

PARAMETERS = {
    "runtime_arn": "/evidence-synthesis/runtime-arn",
    "endpoint_name": "/evidence-synthesis/endpoint-name",
    "input_bucket": "/evidence-synthesis/input-bucket",
    "invoker_role_arn": "/evidence-synthesis/invoker-role-arn",
    "results_bucket": "/evidence-synthesis/results-bucket",
}


@dataclass(frozen=True)
class AwsSettings:
    region: str
    runtime_arn: str
    qualifier: str
    input_bucket: str
    invoker_role_arn: str
    results_bucket: str = ""
    results_prefix: str = "runs"

    @property
    def runtime_log_group(self) -> str:
        runtime_id = self.runtime_arn.rsplit("/", 1)[-1]
        return f"/aws/bedrock-agentcore/runtimes/{runtime_id}-{self.qualifier}"


@dataclass(frozen=True)
class DemoAwsClients:
    runtime: AgentCoreRuntimeClient
    s3: PutObjectS3Adapter
    artifact_client: Any
    log_client: Any

    def artifact_reader(self, settings: AwsSettings, run_id: str) -> S3ArtifactReader:
        return S3ArtifactReader(
            self.artifact_client, settings.results_bucket, run_id, prefix=settings.results_prefix,
        )

    def log_reader(self, settings: AwsSettings, run_id: str) -> CloudWatchLogReader:
        return CloudWatchLogReader(self.log_client, settings.runtime_log_group, run_id)


class PutObjectS3Adapter:
    def __init__(self, client: Any) -> None:
        self._client = client

    def upload_file(
        self,
        filename: str,
        bucket: str,
        key: str,
        *,
        ExtraArgs: dict[str, Any],
    ) -> None:
        content_type = str(
            ExtraArgs.get("ContentType", "application/octet-stream")
        )
        self._client.put_object(
            Bucket=bucket,
            Key=key,
            Body=Path(filename).read_bytes(),
            ContentType=content_type,
        )


def discover_settings(
    *,
    region: str,
    runtime_arn: str = "",
    qualifier: str = "",
    input_bucket: str = "",
    invoker_role_arn: str = "",
    results_bucket: str = "",
    session: Any | None = None,
) -> AwsSettings:
    values = {
        "runtime_arn": runtime_arn
        or os.getenv("EVIDENCE_SYNTHESIS_RUNTIME_ARN", ""),
        "endpoint_name": qualifier
        or os.getenv("EVIDENCE_SYNTHESIS_ENDPOINT_NAME", ""),
        "input_bucket": input_bucket
        or os.getenv("EVIDENCE_SYNTHESIS_INPUT_BUCKET", ""),
        "invoker_role_arn": invoker_role_arn
        or os.getenv("EVIDENCE_SYNTHESIS_INVOKER_ROLE_ARN", ""),
        "results_bucket": results_bucket or os.getenv("EVIDENCE_SYNTHESIS_RESULTS_BUCKET", ""),
    }
    missing_keys = [key for key, value in values.items() if not value]
    invalid_parameters: list[str] = []
    if missing_keys:
        base_session = session or boto3.Session(region_name=region)
        ssm = base_session.client("ssm", config=_client_config(read_timeout=30))
        response = ssm.get_parameters(
            Names=[PARAMETERS[key] for key in missing_keys],
            WithDecryption=False,
        )
        invalid_parameters = [
            str(name) for name in response.get("InvalidParameters", [])
        ]
        by_name = {
            str(parameter["Name"]): str(parameter["Value"])
            for parameter in response.get("Parameters", [])
        }
        for key in missing_keys:
            values[key] = by_name.get(PARAMETERS[key], "")

    unresolved = [key for key, value in values.items() if not value]
    if unresolved:
        rendered = ", ".join(unresolved)
        missing_names = ", ".join(invalid_parameters)
        suffix = (
            f" SSM did not find: {missing_names}."
            if missing_names
            else ""
        )
        raise ValueError(
            f"Unable to discover AWS settings in {region}: {rendered}.{suffix}"
        )
    return AwsSettings(
        region=region,
        runtime_arn=values["runtime_arn"],
        qualifier=values["endpoint_name"],
        input_bucket=values["input_bucket"],
        invoker_role_arn=values["invoker_role_arn"],
        results_bucket=values["results_bucket"],
        results_prefix=os.getenv("EVIDENCE_SYNTHESIS_RESULTS_PREFIX", "runs").strip("/"),
    )


def create_demo_clients(
    settings: AwsSettings,
    *,
    session: Any | None = None,
) -> DemoAwsClients:
    base_session = session or boto3.Session(region_name=settings.region)
    active_session = base_session
    if settings.invoker_role_arn:
        sts = base_session.client("sts", config=_client_config(read_timeout=30))
        response = sts.assume_role(
            RoleArn=settings.invoker_role_arn,
            RoleSessionName=f"evidence-ui-{uuid.uuid4().hex[:12]}",
            DurationSeconds=3600,
        )
        credentials = response["Credentials"]
        active_session = boto3.Session(
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
            region_name=settings.region,
        )

    s3_client = active_session.client(
        "s3",
        config=_client_config(read_timeout=60),
    )
    # A transport retry could start another copy of the same run.
    runtime_client = active_session.client(
        "bedrock-agentcore",
        config=_client_config(read_timeout=600, total_max_attempts=1),
    )
    return DemoAwsClients(
        runtime=AgentCoreRuntimeClient(runtime_client),
        s3=PutObjectS3Adapter(s3_client),
        artifact_client=active_session.client(
            "s3", config=_client_config(read_timeout=5, total_max_attempts=1),
        ),
        log_client=active_session.client(
            "logs", config=_client_config(read_timeout=5, total_max_attempts=1),
        ),
    )


def _client_config(*, read_timeout: int, total_max_attempts: int = 3) -> Config:
    return Config(
        retries={"total_max_attempts": total_max_attempts, "mode": "adaptive"},
        connect_timeout=5,
        read_timeout=read_timeout,
        user_agent_appid="evidence-synthesis-ui/0.1.0",
    )
