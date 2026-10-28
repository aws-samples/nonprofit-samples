# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
import json
import logging
from pathlib import Path
from unittest.mock import Mock

import pytest

from evidence_synthesis.models import ResearchState, RunRequest, TerminalStatus
from evidence_synthesis.ui.artifact_progress import FileArtifactReader, start_run
from evidence_synthesis.ui.log_progress import CloudWatchLogReader, LocalLogReader, RunLogReader
from evidence_synthesis.ui.render import stage_status_class

RUN_ID = "1234567890abcdef1234567890abcdef"
OTHER_RUN_ID = "abcdef1234567890abcdef1234567890"
LOGGER = "evidence_synthesis.orchestrator"


def _payload(stage: str, status: str, *, run_id: str = RUN_ID, attempt: int = 1) -> dict:
    return {
        "event": "evidence_synthesis", "run_id": run_id,
        "stage": stage, "status": status, "attempt": attempt,
    }


def _otel(payload: dict, logger: str = LOGGER) -> str:
    return json.dumps({"scope": {"name": logger}, "body": json.dumps(payload)})


def test_log_transitions_ignore_older_events_and_handle_revisions_and_retries() -> None:
    reader = RunLogReader(RUN_ID)
    reader.record(_payload("evidence_extractor", "started"), 100)
    assert reader.statuses()["evidence_extractor"] == "Running"
    assert stage_status_class(reader.statuses()["evidence_extractor"]) == "running"

    reader.record(_payload("evidence_extractor", "completed"), 200)
    reader.record(_payload("evidence_extractor", "started"), 100)
    reader.record(_payload("evidence_extractor", "completed"), 200)
    assert reader.statuses()["evidence_extractor"] == "Complete"
    assert stage_status_class(reader.statuses()["evidence_extractor"]) == "complete"

    reader.record(_payload("analyst", "completed"), 300)
    reader.record(_payload("analyst-retry", "started"), 400)
    assert reader.statuses()["analyst"] == "Revising claims"
    assert stage_status_class(reader.statuses()["analyst"]) == "running"
    reader.record(_payload("analyst-retry", "failed"), 500)
    assert stage_status_class(reader.statuses()["analyst"]) == "failed"
    reader.record(_payload("analyst-retry", "started", attempt=2), 600)
    assert reader.statuses()["analyst"] == "Revising claims · attempt 2"
    reader.record(_payload("analyst-retry", "completed", attempt=2), 700)
    assert reader.statuses()["analyst"] == "Revision complete"
    assert reader.statuses()["verifier"] == "Waiting for logs"


@pytest.mark.parametrize("style", ["otel-string", "otel-object", "formatted", "basic"])
def test_cloudwatch_reads_the_supported_orchestrator_log_formats(style: str) -> None:
    payload = _payload("verifier", "started")
    messages = {
        "otel-string": _otel(payload),
        "otel-object": json.dumps({"scope": {"name": LOGGER}, "body": payload}),
        "formatted": (
            f"2026-09-27 16:15:14,338 INFO [{LOGGER}] [orchestrator.py:411] "
            f"[trace_id=example] - {json.dumps(payload)}"
        ),
        "basic": f"INFO:{LOGGER}:{json.dumps(payload)}",
    }
    client = Mock()
    client.filter_log_events.return_value = {
        "events": [{"timestamp": 100, "message": messages[style]}],
    }
    reader = CloudWatchLogReader(client, "runtime-group", RUN_ID)
    reader.poll()
    assert reader.statuses()["verifier"] == "Running"


def test_cloudwatch_paginates_and_revisits_window_for_delayed_delivery() -> None:
    completed = {"timestamp": 200, "message": _otel(_payload("analyst", "completed"))}
    started = {"timestamp": 100, "message": _otel(_payload("analyst", "started"))}
    unrelated = {
        "timestamp": 999, "message": _otel(_payload("analyst", "failed", run_id=OTHER_RUN_ID)),
    }
    client = Mock()
    client.filter_log_events.side_effect = [
        {"events": [completed], "nextToken": "next"},
        {"events": [started, unrelated]},
        {"events": [completed, started]},
    ]
    reader = CloudWatchLogReader(client, "runtime-group", RUN_ID)
    reader.poll()
    reader.poll()
    assert reader.statuses()["analyst"] == "Complete"
    calls = client.filter_log_events.call_args_list
    assert calls[0].kwargs["startTime"] == calls[2].kwargs["startTime"]
    assert calls[0].kwargs["filterPattern"] == f'"{RUN_ID}" "{LOGGER}"'
    assert calls[1].kwargs["nextToken"] == "next"
    assert "nextToken" not in calls[2].kwargs


def test_cloudwatch_ignores_non_orchestrator_payloads_and_invalid_statuses() -> None:
    client = Mock()
    client.filter_log_events.return_value = {"events": [
        {"timestamp": 100, "message": _otel(_payload("analyst", "started"), "strands.agent")},
        {"timestamp": 200, "message": json.dumps(_payload("analyst", "started"))},
        {"timestamp": 300, "message": "{malformed"},
        {"timestamp": 400, "message": _otel(_payload("unknown", "started"))},
        {"timestamp": 500, "message": _otel(_payload("analyst", "invented"))},
    ]}
    reader = CloudWatchLogReader(client, "runtime-group", RUN_ID)
    reader.poll()
    assert set(reader.statuses().values()) == {"Waiting for logs"}


def test_local_reader_filters_run_ids_and_detaches_handler() -> None:
    logger = logging.getLogger(LOGGER)
    reader = LocalLogReader(RUN_ID)
    other = LocalLogReader(OTHER_RUN_ID)
    try:
        logger.info(json.dumps(_payload("evidence_extractor", "started")))
        assert reader.statuses()["evidence_extractor"] == "Running"
        assert other.statuses()["evidence_extractor"] == "Waiting for logs"
        reader.close()
        logger.info(json.dumps(_payload("evidence_extractor", "completed")))
        assert reader.statuses()["evidence_extractor"] == "Running"
    finally:
        reader.close()
        other.close()


def test_log_poll_and_cleanup_failures_do_not_hide_the_final_response(tmp_path: Path) -> None:
    request = RunRequest(
        question="What is supported?", run_id=RUN_ID,
        sources=[{"source_id": "test", "content": "Evidence."}],
    )
    state = ResearchState(run_id=RUN_ID, request=request, status=TerminalStatus.COMPLETED)
    operation = Mock(return_value=state)
    logs = Mock()
    logs.poll.side_effect = RuntimeError("Logs unavailable")
    logs.close.side_effect = RuntimeError("Cleanup unavailable")
    run = start_run(request, FileArtifactReader(tmp_path, RUN_ID), operation, logs=logs)
    run.future.result(timeout=5)
    run.poll()
    assert run.warning is None
    assert run.log_warning and "status updates" in run.log_warning
    assert run.result() == state
    operation.assert_called_once()


@pytest.mark.parametrize(("status", "style"), [
    ("Waiting for logs", "waiting"), ("Running", "running"),
    ("Running · attempt 2", "running"), ("Revising claims", "running"),
    ("Complete", "complete"), ("Failed", "failed"), ("Timed out", "failed"),
    ("Blocked", "failed"), ("Partial result", "warning"),
])
def test_stage_color_styles(status: str, style: str) -> None:
    assert stage_status_class(status) == style
