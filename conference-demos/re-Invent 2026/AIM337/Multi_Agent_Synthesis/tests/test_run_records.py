# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from evidence_synthesis.config import PipelinePolicy
from evidence_synthesis.factory import build_orchestrator
from evidence_synthesis.models import RunRequest, RunSummary, TerminalStatus


@pytest.mark.asyncio
async def test_completed_run_preserves_stage_logs_and_artifacts(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="evidence_synthesis.orchestrator")
    request = RunRequest(
        question="What is supported?",
        sources=[{"source_id": "one", "content": "Supported evidence."}],
    )
    runner = build_orchestrator(runtime_name="deterministic", output_dir=tmp_path)

    state = await runner.run(request)

    assert state.status is TerminalStatus.COMPLETED
    assert [
        event.stage for event in state.events
        if event.status == "completed" and event.stage != "pipeline"
    ] == ["evidence_extractor", "analyst", "verifier", "synthesizer"]
    run_dir = tmp_path / state.run_id
    assert {path.name for path in run_dir.iterdir()} == {
        "evidence_extractor.json", "analysis.json", "verification-1.json",
        "synthesis.json", "state.json", "answer.md",
    }
    assert RunSummary.model_validate_json(
        (run_dir / "state.json").read_text(),
    ) == RunSummary.from_state(state)
    logs = [
        json.loads(record.message) for record in caplog.records
        if record.name == "evidence_synthesis.orchestrator"
    ]
    assert all(record["run_id"] == state.run_id for record in logs)
    assert [(record["stage"], record["status"]) for record in logs] == [
        ("pipeline", "started"),
        ("evidence_extractor", "started"), ("evidence_extractor", "completed"),
        ("analyst", "started"), ("analyst", "completed"),
        ("verifier", "started"), ("verifier", "completed"),
        ("synthesizer", "started"), ("synthesizer", "completed"),
        ("pipeline", "completed"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(("question", "policy", "status", "reason"), [
    (
        "Should we deny insurance to this individual?", PipelinePolicy(),
        TerminalStatus.BLOCKED, "input_safety_policy",
    ),
    (
        "What is supported?", PipelinePolicy(deadline_seconds=0),
        TerminalStatus.PARTIAL, "deadline_exceeded",
    ),
    (
        "What is supported?", PipelinePolicy(token_budget=1),
        TerminalStatus.PARTIAL, "token_budget_exceeded",
    ),
])
async def test_early_exit_returns_and_persists_terminal_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    question: str, policy: PipelinePolicy, status: TerminalStatus, reason: str,
) -> None:
    monkeypatch.setattr("evidence_synthesis.orchestrator.asyncio.sleep", AsyncMock())
    runner = build_orchestrator(
        runtime_name="deterministic", output_dir=tmp_path, policy=policy,
    )
    request = RunRequest(
        question=question,
        sources=[{"source_id": "one", "content": "Supported evidence."}],
    )

    state = await runner.run(request)

    assert state.status is status
    assert state.terminal_reason is not None
    assert reason in state.terminal_reason
    assert state.synthesis is not None
    assert state.synthesis.partial
    assert state.events[-1].stage == "pipeline"
    assert state.events[-1].status == status.value
    saved = tmp_path / state.run_id / "state.json"
    assert RunSummary.model_validate_json(saved.read_text()) == RunSummary.from_state(state)
