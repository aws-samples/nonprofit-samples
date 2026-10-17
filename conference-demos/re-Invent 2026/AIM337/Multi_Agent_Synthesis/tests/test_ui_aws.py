# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

from pathlib import Path
from typing import Any

from evidence_synthesis.ui.aws import (
    AwsSettings,
    PutObjectS3Adapter,
    discover_settings,
)


def test_runtime_log_group_is_derived_from_the_runtime_and_endpoint() -> None:
    settings = AwsSettings(
        region="us-east-1",
        runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/example-123",
        qualifier="live", input_bucket="input", invoker_role_arn="",
    )
    assert settings.runtime_log_group == "/aws/bedrock-agentcore/runtimes/example-123-live"


class FakeSsmClient:
    def __init__(self, *, missing: bool = False) -> None:
        self.requests: list[dict[str, Any]] = []
        self.missing = missing

    def get_parameters(self, **request: Any) -> dict[str, Any]:
        self.requests.append(request)
        if self.missing:
            return {
                "Parameters": [],
                "InvalidParameters": request["Names"],
            }
        return {
            "Parameters": [
                {
                    "Name": "/evidence-synthesis/runtime-arn",
                    "Value": "arn:runtime",
                },
                {
                    "Name": "/evidence-synthesis/endpoint-name",
                    "Value": "live",
                },
                {
                    "Name": "/evidence-synthesis/input-bucket",
                    "Value": "input-bucket",
                },
                {
                    "Name": "/evidence-synthesis/invoker-role-arn",
                    "Value": "arn:role",
                },
                {
                    "Name": "/evidence-synthesis/results-bucket",
                    "Value": "results-bucket",
                },
            ]
        }


class FakeSession:
    def __init__(self, ssm: FakeSsmClient) -> None:
        self.ssm = ssm

    def client(self, service_name: str, **_: Any) -> FakeSsmClient:
        assert service_name == "ssm"
        return self.ssm


class FakeS3Client:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def put_object(self, **request: Any) -> None:
        self.requests.append(request)


def test_discovers_missing_aws_settings_from_ssm() -> None:
    ssm = FakeSsmClient()

    settings = discover_settings(
        region="us-east-1",
        session=FakeSession(ssm),
    )

    assert settings.runtime_arn == "arn:runtime"
    assert settings.qualifier == "live"
    assert settings.input_bucket == "input-bucket"
    assert settings.invoker_role_arn == "arn:role"
    assert settings.results_bucket == "results-bucket"
    assert len(ssm.requests) == 1
    assert ssm.requests[0]["WithDecryption"] is False


def test_invocations_have_no_automatic_transport_retries() -> None:
    from evidence_synthesis.ui.aws import _client_config

    config = _client_config(read_timeout=600, total_max_attempts=1)
    assert config.read_timeout == 600
    assert config.retries["total_max_attempts"] == 1


def test_put_object_adapter_uploads_without_acl_or_listing(
    tmp_path: Path,
) -> None:
    source = tmp_path / "notes.md"
    source.write_bytes(b"Evidence.")
    client = FakeS3Client()

    PutObjectS3Adapter(client).upload_file(
        str(source),
        "input-bucket",
        "sources/ui/run/notes.md",
        ExtraArgs={"ContentType": "text/markdown"},
    )

    assert client.requests == [
        {
            "Bucket": "input-bucket",
            "Key": "sources/ui/run/notes.md",
            "Body": b"Evidence.",
            "ContentType": "text/markdown",
        }
    ]


def test_discovery_error_identifies_the_region() -> None:
    ssm = FakeSsmClient(missing=True)

    try:
        discover_settings(
            region="us-east-2",
            session=FakeSession(ssm),
        )
    except ValueError as error:
        message = str(error)
    else:
        raise AssertionError("Expected discovery to fail")

    assert "in us-east-2" in message
    assert "/evidence-synthesis/runtime-arn" in message
