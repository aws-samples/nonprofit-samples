# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
import json
import threading
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError
from pydantic import ValidationError

from evidence_synthesis.factory import build_orchestrator
from evidence_synthesis.models import (
    EvidenceExtractorResult,
    ResearchState,
    RunRequest,
    RunSummary,
    TerminalStatus,
)
from evidence_synthesis.ui.artifact_progress import (
    FileArtifactReader,
    S3ArtifactReader,
    saved_stage_statuses,
    start_run,
)

RUN_ID = "1234567890abcdef1234567890abcdef"
OTHER_RUN_ID = "abcdef1234567890abcdef1234567890"


def _request() -> RunRequest:
    return RunRequest(
        question="What is supported?", run_id=RUN_ID,
        sources=[{"source_id": "one", "content": "Supported evidence."}],
    )


@pytest.mark.parametrize("run_id", ["../elsewhere", "not-a-uuid", "a" * 33, "A" * 32])
def test_request_rejects_unsafe_or_invalid_run_identifiers(run_id: str) -> None:
    with pytest.raises(ValidationError):
        RunRequest.model_validate({**_request().model_dump(), "run_id": run_id})


@pytest.mark.asyncio
async def test_requested_id_correlates_response_and_artifacts(tmp_path: Path) -> None:
    runner = build_orchestrator(runtime_name="deterministic", output_dir=tmp_path)

    state = await runner.run(_request())

    assert state.run_id == RUN_ID
    reader = FileArtifactReader(tmp_path, RUN_ID)
    reader.poll()
    assert reader.outputs["state"] == RunSummary.from_state(state)
    expected = {"evidence_extractor", "analysis", "verification-1", "synthesis"}
    assert expected.issubset(reader.outputs)


def test_local_reader_waits_for_valid_json_and_ignores_other_runs(tmp_path: Path) -> None:
    reader = FileArtifactReader(tmp_path, RUN_ID)
    reader.poll()
    assert not reader.outputs
    folder = tmp_path / RUN_ID
    folder.mkdir()
    extraction = folder / "evidence_extractor.json"
    extraction.write_text('{"records":')
    (tmp_path / OTHER_RUN_ID).mkdir()
    (tmp_path / OTHER_RUN_ID / "analysis.json").write_text('{"claims": []}')

    reader.poll()
    assert not reader.outputs
    extraction.write_text(EvidenceExtractorResult(records=[]).model_dump_json())
    reader.poll()
    assert set(reader.outputs) == {"evidence_extractor"}


def test_s3_reader_paginates_and_downloads_only_new_versions() -> None:
    extraction = BytesIO(b'{"records": []}')
    analysis = BytesIO(b'{"claims": []}')
    changed_extraction = BytesIO(b'{"records": [], "gaps": ["New gap"]}')
    first = {
        "Contents": [{"Key": f"runs/{RUN_ID}/evidence_extractor.json", "ETag": "extraction-v1"}],
        "IsTruncated": True, "NextContinuationToken": "page-two",
    }
    second = {"Contents": [
        {"Key": f"runs/{RUN_ID}/analysis.json", "ETag": "analysis-v1"},
        {"Key": f"runs/{RUN_ID}/unrelated.json", "ETag": "ignored"},
        {"Key": f"runs/{OTHER_RUN_ID}/evidence_extractor.json", "ETag": "other-run"},
    ]}
    last = {"Contents": [
        {"Key": f"runs/{RUN_ID}/evidence_extractor.json", "ETag": "extraction-v2"},
    ]}
    client = Mock()
    client.list_objects_v2.side_effect = [first, second, first, second, last]
    client.get_object.side_effect = [
        {"Body": extraction}, {"Body": analysis}, {"Body": changed_extraction},
    ]
    reader = S3ArtifactReader(client, "results", RUN_ID)

    reader.poll()
    reader.poll()
    assert client.get_object.call_count == 2
    assert extraction.closed and analysis.closed
    assert client.list_objects_v2.call_args_list[1].kwargs == {
        "Bucket": "results", "Prefix": f"runs/{RUN_ID}/", "ContinuationToken": "page-two",
    }
    reader.poll()
    assert client.get_object.call_count == 3
    assert changed_extraction.closed
    assert reader.outputs["evidence_extractor"].gaps == ["New gap"]


def test_s3_reader_retries_incomplete_artifacts_without_caching_them() -> None:
    client = Mock()
    client.list_objects_v2.return_value = {
        "Contents": [{"Key": f"runs/{RUN_ID}/evidence_extractor.json", "ETag": "v1"}],
    }
    invalid = BytesIO(b'{"records":')
    valid = BytesIO(b'{"records": []}')
    client.get_object.side_effect = [
        ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject"),
        {"Body": invalid},
        {"Body": valid},
    ]
    reader = S3ArtifactReader(client, "results", RUN_ID)
    reader.poll()
    reader.poll()
    assert not reader.outputs
    reader.poll()
    assert isinstance(reader.outputs["evidence_extractor"], EvidenceExtractorResult)
    assert invalid.closed and valid.closed


def test_reader_rejects_state_from_another_run(tmp_path: Path) -> None:
    folder = tmp_path / RUN_ID
    folder.mkdir()
    (folder / "state.json").write_text(
        ResearchState(run_id=OTHER_RUN_ID, request=_request()).model_dump_json(),
    )
    with pytest.raises(ValueError, match="different run"):
        FileArtifactReader(tmp_path, RUN_ID).poll()


def test_review_artifacts_use_numeric_order_without_guessing_running_stages(tmp_path: Path) -> None:
    folder = tmp_path / RUN_ID
    folder.mkdir()
    for name in ["analysis", "analysis-retry-2", "analysis-retry-10"]:
        (folder / f"{name}.json").write_text('{"claims": []}')
    for cycle in [1, 3, 11]:
        (folder / f"verification-{cycle}.json").write_text(json.dumps({
            "claims": [], "citation_closure_complete": cycle == 11,
        }))
    reader = FileArtifactReader(tmp_path, RUN_ID)
    reader.poll()
    statuses = saved_stage_statuses(reader.outputs)
    assert statuses["analyst"] == "Revision 10 saved"
    assert statuses["verifier"] == "Review 11 complete"
    assert statuses["synthesizer"] == "Waiting for result"
    assert all("Running" not in status for status in statuses.values())


def test_polling_failure_does_not_cancel_or_repeat_the_invocation(tmp_path: Path) -> None:
    release = threading.Event()
    state = ResearchState(run_id=RUN_ID, request=_request(), status=TerminalStatus.COMPLETED)
    invoke = Mock(side_effect=lambda: (release.wait(timeout=5), state)[1])
    reader = Mock()
    reader.poll.side_effect = ClientError({"Error": {"Code": "AccessDenied"}}, "ListObjectsV2")
    run = start_run(_request(), reader, invoke)
    try:
        run.poll()
        run.poll()
        assert run.warning and "temporarily unavailable" in run.warning
        assert not run.future.done()
        assert invoke.call_count == 1
    finally:
        release.set()
    assert run.future.result(timeout=5) == state
    assert run.result() == state
    assert invoke.call_count == 1


def test_saved_final_state_can_recover_a_lost_response(tmp_path: Path) -> None:
    state = ResearchState(run_id=RUN_ID, request=_request(), status=TerminalStatus.COMPLETED)
    folder = tmp_path / RUN_ID
    folder.mkdir()
    (folder / "state.json").write_text(state.model_dump_json())
    reader = FileArtifactReader(tmp_path, RUN_ID)
    operation = Mock(side_effect=TimeoutError("Response lost"))
    run = start_run(_request(), reader, operation)
    run.future.exception(timeout=5)
    run.poll()

    assert run.result() == state
    operation.assert_called_once()


def test_summary_recovers_only_the_matching_request(tmp_path: Path) -> None:
    state = ResearchState(run_id=RUN_ID, request=_request(), status=TerminalStatus.COMPLETED)
    folder = tmp_path / RUN_ID
    folder.mkdir()
    (folder / "state.json").write_text(RunSummary.from_state(state).model_dump_json())
    reader = FileArtifactReader(tmp_path, RUN_ID)
    operation = Mock(side_effect=TimeoutError("Response lost"))
    run = start_run(_request(), reader, operation)
    run.future.exception(timeout=5)
    run.poll()
    recovered = run.result()
    assert recovered.summary_only
    assert recovered.request == _request()
    wrong_request = _request().model_copy(update={"question": "A different question?"})
    with pytest.raises(ValueError, match="different request"):
        reader.outputs["state"].restore(wrong_request)
