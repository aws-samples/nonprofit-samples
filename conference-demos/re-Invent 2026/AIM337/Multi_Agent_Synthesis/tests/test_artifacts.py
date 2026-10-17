# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
import json
from typing import Any

from evidence_synthesis.artifacts import S3ArtifactStore
from evidence_synthesis.models import ResearchState, RunRequest, SynthesisResult


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, dict[str, Any]] = {}

    def put_object(self, **request: Any) -> None:
        self.objects[str(request["Key"])] = request


def test_s3_artifact_store_writes_run_outputs() -> None:
    client = FakeS3Client()
    store = S3ArtifactStore(
        "result-bucket",
        prefix="runs",
        client=client,
    )
    state = ResearchState(
        run_id="abc123",
        request=RunRequest(
            question="What is supported?",
            sources=[{"source_id": "inline", "content": "Evidence."}],
        ),
        artifact_uri="s3://result-bucket/runs/abc123",
        synthesis=SynthesisResult(answer="Supported answer."),
    )

    store.save_stage("abc123", "evidence_extractor", state.request)
    store.save_state(state)

    assert store.uri_for_run("abc123") == "s3://result-bucket/runs/abc123"
    assert set(client.objects) == {
        "runs/abc123/evidence_extractor.json",
        "runs/abc123/state.json",
        "runs/abc123/answer.md",
    }
    saved_state = json.loads(client.objects["runs/abc123/state.json"]["Body"])
    assert saved_state["artifact_uri"] == "s3://result-bucket/runs/abc123"
    assert client.objects["runs/abc123/answer.md"]["Body"] == b"Supported answer.\n"
