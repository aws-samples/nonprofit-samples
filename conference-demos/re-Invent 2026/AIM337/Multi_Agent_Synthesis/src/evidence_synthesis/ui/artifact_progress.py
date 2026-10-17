# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError
from pydantic import BaseModel, ValidationError

from evidence_synthesis.models import (
    AnalysisResult,
    EvidenceExtractorResult,
    ResearchState,
    RunRequest,
    RunSummary,
    StageSummary,
    SynthesisResult,
    VerificationResult,
)
from evidence_synthesis.ui.log_progress import RunLogReader

_ARTIFACT_TYPES: dict[str, type[BaseModel]] = {
    "evidence_extractor": EvidenceExtractorResult,
    "analysis": AnalysisResult,
    "synthesis": SynthesisResult,
    "partial-synthesis": SynthesisResult,
    "state": ResearchState,
}
_REVIEW = re.compile(r"^(verification|analysis-retry)-([1-9][0-9]*)$")
_RUN_ID = re.compile(r"^[a-f0-9]{32}$")


def artifact_type(name: str) -> type[BaseModel] | None:
    if name in _ARTIFACT_TYPES:
        return _ARTIFACT_TYPES[name]
    review = _REVIEW.fullmatch(name)
    if review:
        return VerificationResult if review[1] == "verification" else AnalysisResult
    return None


def artifact_order(name: str) -> tuple[int, int]:
    review = _REVIEW.fullmatch(name)
    if review:
        cycle = int(review[2])
        return (2, cycle * 2 - (1 if review[1] == "verification" else 0))
    return {
        "evidence_extractor": (0, 0), "analysis": (1, 0), "synthesis": (3, 0),
        "partial-synthesis": (3, 1), "state": (4, 0),
    }.get(name, (5, 0))


class ArtifactReader:
    """Cache validated artifacts for one run; polling belongs entirely to the UI."""

    def __init__(self, run_id: str) -> None:
        if not _RUN_ID.fullmatch(run_id):
            raise ValueError("Artifact polling requires a 32-character hexadecimal run ID.")
        self.run_id = run_id
        self.outputs: dict[str, BaseModel] = {}
        self._versions: dict[str, str] = {}

    def poll(self) -> None:
        versions = self._list()
        for name in sorted(versions, key=artifact_order):
            model = artifact_type(name)
            if model is None or self._versions.get(name) == versions[name]:
                continue
            value: BaseModel
            try:
                data = json.loads(self._read(name))
                if not isinstance(data, dict):
                    continue
                if data.get("artifact_kind") == "stage_summary":
                    value = StageSummary.model_validate(data)
                elif data.get("artifact_kind") == "run_summary":
                    value = RunSummary.model_validate(data)
                else:
                    value = model.model_validate(data)
            except (FileNotFoundError, ValidationError, json.JSONDecodeError):
                # A local write may still be in progress. Retry it on the next poll.
                continue
            if isinstance(value, (ResearchState, RunSummary)) and value.run_id != self.run_id:
                raise ValueError("The saved state belongs to a different run.")
            self.outputs[name] = value
            self._versions[name] = versions[name]

    def _list(self) -> dict[str, str]:
        raise NotImplementedError

    def _read(self, name: str) -> bytes:
        raise NotImplementedError


class FileArtifactReader(ArtifactReader):
    def __init__(self, root: Path, run_id: str) -> None:
        super().__init__(run_id)
        self._directory = root / run_id

    def _list(self) -> dict[str, str]:
        versions = {}
        for path in self._directory.glob("*.json"):
            if path.is_file():
                stat = path.stat()
                versions[path.stem] = f"{stat.st_mtime_ns}:{stat.st_size}"
        return versions

    def _read(self, name: str) -> bytes:
        return (self._directory / f"{name}.json").read_bytes()


class S3ArtifactReader(ArtifactReader):
    def __init__(self, client: Any, bucket: str, run_id: str, *, prefix: str = "runs") -> None:
        super().__init__(run_id)
        if not bucket:
            raise ValueError("A results bucket is required for artifact polling.")
        self._client = client
        self._bucket = bucket
        self._prefix = f"{prefix.strip('/')}/{run_id}/".lstrip("/")

    def _list(self) -> dict[str, str]:
        versions = {}
        request = {"Bucket": self._bucket, "Prefix": self._prefix}
        while True:
            response = self._client.list_objects_v2(**request)
            for item in response.get("Contents", []):
                key = str(item["Key"])
                if key.startswith(self._prefix) and key.endswith(".json"):
                    name = key[len(self._prefix):-5]
                    versions[name] = str(item["ETag"])
            if not response.get("IsTruncated"):
                return versions
            request["ContinuationToken"] = response["NextContinuationToken"]

    def _read(self, name: str) -> bytes:
        try:
            response = self._client.get_object(
                Bucket=self._bucket, Key=f"{self._prefix}{name}.json",
            )
        except ClientError as error:
            if error.response["Error"]["Code"] in {"NoSuchKey", "404", "NotFound"}:
                raise FileNotFoundError(name) from error
            raise
        body = response["Body"]
        try:
            return bytes(body.read())
        finally:
            body.close()


@dataclass
class ActiveRun:
    request: RunRequest
    reader: ArtifactReader
    future: Future[ResearchState]
    started_at: float
    warning: str | None = None
    logs: RunLogReader | None = None
    log_warning: str | None = None

    def poll(self) -> None:
        try:
            self.reader.poll()
            self.warning = None
        except Exception:
            self.warning = (
                "Progress updates are temporarily unavailable. "
                "The request is still running; its final response will be shown when available."
            )
        if self.logs is not None:
            try:
                self.logs.poll()
                self.log_warning = None
            except Exception:
                self.log_warning = (
                    "Live status updates are temporarily unavailable. "
                    "Showing the last known status while the request continues."
                )

    def statuses(self) -> dict[str, str]:
        return self.logs.statuses() if self.logs is not None else saved_stage_statuses(
            self.reader.outputs,
        )

    def result(self) -> ResearchState:
        try:
            state = self.future.result()
        except Exception:
            # A saved final state can recover a lost response.
            saved = self.reader.outputs.get("state")
            if isinstance(saved, RunSummary):
                return saved.restore(self.request)
            if isinstance(saved, ResearchState) and saved.request == self.request:
                return saved
            raise
        if state.run_id != self.request.run_id:
            raise ValueError(
                "The runtime returned a different run ID. Update the deployed backend."
            )
        return state


def start_run(
    request: RunRequest,
    reader: ArtifactReader,
    invoke: Callable[[], ResearchState],
    *,
    logs: RunLogReader | None = None,
) -> ActiveRun:
    future: Future[ResearchState] = Future()

    def execute() -> None:
        try:
            result = invoke()
        except Exception as error:
            future.set_exception(error)
        else:
            future.set_result(result)
        finally:
            if logs is not None:
                # Observer cleanup must not hide the invocation's result.
                with suppress(Exception):
                    logs.close()

    run = ActiveRun(request, reader, future, time.monotonic(), logs=logs)
    threading.Thread(
        target=execute, daemon=True, name=f"evidence-ui-{request.run_id}",
    ).start()
    return run


def saved_stage_statuses(
    outputs: dict[str, BaseModel], *, finished: bool = False,
) -> dict[str, str]:
    statuses = dict.fromkeys(
        ("evidence_extractor", "analyst", "verifier", "synthesizer"),
        "Not reached" if finished else "Waiting for result",
    )
    for name in sorted(outputs, key=artifact_order):
        value = outputs[name]
        if isinstance(value, StageSummary):
            if name == "evidence_extractor":
                statuses["evidence_extractor"] = f"{value.item_count} excerpts extracted"
            elif name == "analysis" or name.startswith("analysis-retry-"):
                statuses["analyst"] = f"{value.item_count} claims drafted"
            elif name.startswith("verification-"):
                statuses["verifier"] = (
                    "Review complete" if value.citation_closure_complete else "Revision requested"
                )
            elif name in {"synthesis", "partial-synthesis"}:
                statuses["synthesizer"] = "Answer drafted · awaiting safety checks"
            continue
        if name == "evidence_extractor":
            statuses["evidence_extractor"] = "Evidence saved"
        elif name == "analysis":
            statuses["analyst"] = "Claims saved"
        elif name.startswith("analysis-retry-"):
            statuses["analyst"] = f"Revision {name.rsplit('-', 1)[1]} saved"
        elif isinstance(value, VerificationResult):
            cycle = name.rsplit("-", 1)[1]
            statuses["verifier"] = (
                f"Review {cycle} complete" if value.citation_closure_complete
                else f"Review {cycle} saved · revision requested"
            )
        elif name in {"synthesis", "partial-synthesis"}:
            statuses["synthesizer"] = (
                "Answer saved · awaiting final checks" if name == "synthesis"
                else "Partial answer saved"
            )
    return statuses


def state_stage_statuses(state: ResearchState) -> dict[str, str]:
    outputs: dict[str, BaseModel] = {}
    for name, value in [
        ("evidence_extractor", state.evidence_extractor), ("analysis", state.analysis),
        (f"verification-{state.verification_cycles}", state.verification),
        ("synthesis", state.synthesis),
    ]:
        if value is not None:
            outputs[name] = value
    statuses = saved_stage_statuses(outputs, finished=True)
    if state.synthesis is not None:
        statuses["synthesizer"] = {
            "completed": "Complete", "partial": "Partial result",
            "blocked": "Blocked", "failed": "Failed",
        }[state.status.value]
    return statuses
