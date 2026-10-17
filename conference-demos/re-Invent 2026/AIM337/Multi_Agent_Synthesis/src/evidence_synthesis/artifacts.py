# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from evidence_synthesis.models import (
    AnalysisResult,
    EvidenceExtractorResult,
    ResearchState,
    RunSummary,
    StageSummary,
    VerificationResult,
)


class DuplicateRunError(ValueError):
    pass


def _stage_summary(stage: str, value: BaseModel) -> StageSummary:
    count = 0
    closure = None
    if isinstance(value, EvidenceExtractorResult):
        count = len(value.records)
    elif isinstance(value, (AnalysisResult, VerificationResult)):
        count = len(value.claims)
    if isinstance(value, VerificationResult):
        closure = value.citation_closure_complete
    return StageSummary(stage=stage, item_count=count, citation_closure_complete=closure)


class ArtifactStore(Protocol):
    def reserve_run(self, run_id: str) -> None: ...

    def uri_for_run(self, run_id: str) -> str | None: ...

    def save_stage(self, run_id: str, stage: str, value: BaseModel) -> None: ...

    def save_state(self, state: ResearchState) -> None: ...


class NullArtifactStore:
    def reserve_run(self, run_id: str) -> None:
        del run_id

    def uri_for_run(self, run_id: str) -> str | None:
        del run_id
        return None

    def save_stage(self, run_id: str, stage: str, value: BaseModel) -> None:
        del run_id, stage, value

    def save_state(self, state: ResearchState) -> None:
        del state


class FileArtifactStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def reserve_run(self, run_id: str) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        try:
            (self._root / run_id).mkdir(mode=0o700)
        except FileExistsError:
            raise DuplicateRunError("Run ID is already in use") from None

    def uri_for_run(self, run_id: str) -> str:
        return str((self._root / run_id).resolve())

    def save_stage(self, run_id: str, stage: str, value: BaseModel) -> None:
        run_dir = self._root / run_id
        run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._write(run_dir / f"{stage}.json", _stage_summary(stage, value).model_dump())

    def save_state(self, state: ResearchState) -> None:
        run_dir = self._root / state.run_id
        run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._write(run_dir / "state.json", RunSummary.from_state(state).model_dump(mode="json"))
        if state.synthesis is not None:
            (run_dir / "answer.md").write_text(state.synthesis.answer + "\n", encoding="utf-8")

    @staticmethod
    def _write(path: Path, value: object) -> None:
        path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class S3ArtifactStore:
    def __init__(
        self,
        bucket: str,
        *,
        prefix: str = "runs",
        region: str | None = None,
        client: Any | None = None,
    ) -> None:
        if not bucket.strip():
            raise ValueError("S3 artifact bucket must not be empty")
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._region = region
        self._client = client

    def reserve_run(self, run_id: str) -> None:
        from botocore.exceptions import ClientError

        try:
            self._get_client().put_object(
                Bucket=self._bucket,
                Key=f"reservations/{run_id}.json",
                Body=b"{}",
                ContentType="application/json",
                IfNoneMatch="*",
            )
        except ClientError as error:
            if error.response["Error"]["Code"] in {"PreconditionFailed", "412"}:
                raise DuplicateRunError("Run ID is already in use") from None
            raise

    def uri_for_run(self, run_id: str) -> str:
        return f"s3://{self._bucket}/{self._key(run_id)}"

    def save_stage(self, run_id: str, stage: str, value: BaseModel) -> None:
        self._write_json(
            f"{self._key(run_id)}/{stage}.json", _stage_summary(stage, value).model_dump(),
        )

    def save_state(self, state: ResearchState) -> None:
        run_key = self._key(state.run_id)
        self._write_json(
            f"{run_key}/state.json", RunSummary.from_state(state).model_dump(mode="json"),
        )
        if state.synthesis is not None:
            self._put(
                f"{run_key}/answer.md",
                (state.synthesis.answer + "\n").encode(),
                content_type="text/markdown; charset=utf-8",
            )

    def _write_json(self, key: str, value: object) -> None:
        body = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
        self._put(key, body, content_type="application/json")

    def _put(self, key: str, body: bytes, *, content_type: str) -> None:
        self._get_client().put_object(
            Bucket=self._bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
        )

    def _get_client(self) -> Any:
        if self._client is None:
            import boto3
            from botocore.config import Config

            config = Config(
                retries={"total_max_attempts": 3, "mode": "adaptive"},
                connect_timeout=5,
                read_timeout=30,
                user_agent_appid="evidence-synthesis/0.1.0",
            )
            self._client = boto3.Session(region_name=self._region).client(
                "s3", config=config
            )
        return self._client

    def _key(self, run_id: str) -> str:
        return f"{self._prefix}/{run_id}" if self._prefix else run_id
