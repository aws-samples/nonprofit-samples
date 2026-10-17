# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import BaseModel

from evidence_synthesis.agents import (
    AnalystAgent,
    EvidenceExtractorAgent,
    SynthesizerAgent,
    VerifierAgent,
)
from evidence_synthesis.artifacts import FileArtifactStore
from evidence_synthesis.config import PipelinePolicy
from evidence_synthesis.deterministic_runtime import DeterministicRuntime
from evidence_synthesis.models import AgentRole, RunRequest, TerminalStatus
from evidence_synthesis.orchestrator import EvidenceSynthesisOrchestrator
from evidence_synthesis.runtime import AgentRuntime, RuntimeResult
from evidence_synthesis.safety import SafetyDecision, SafetyGate
from evidence_synthesis.source_loader import SourceLoader


class FailingRuntime(DeterministicRuntime):
    def __init__(self, role: AgentRole, *, always_fail: bool = False) -> None:
        self.failing_role = role
        self.always_fail = always_fail
        self.calls: list[AgentRole] = []
        self.payloads: list[str] = []

    def invoke[OutputT: BaseModel](
        self, role: AgentRole, payload: BaseModel, output_type: type[OutputT], system_prompt: str,
    ) -> RuntimeResult[OutputT]:
        self.calls.append(role)
        if role is self.failing_role:
            self.payloads.append(payload.model_dump_json())
            if self.always_fail or self.calls.count(role) == 1:
                raise RuntimeError("temporary stage failure")
        return super().invoke(role, payload, output_type, system_prompt)


def _request(source_id: str = "one") -> RunRequest:
    return RunRequest(question="What is supported?", sources=[{
        "source_id": source_id, "content": f"Evidence from {source_id}.",
    }])


def _orchestrator(
    runtime: AgentRuntime,
    directory: Path,
    policy: PipelinePolicy | None = None,
    safety_gate: SafetyGate | None = None,
) -> EvidenceSynthesisOrchestrator:
    return EvidenceSynthesisOrchestrator(
        evidence_extractor=EvidenceExtractorAgent(runtime, SourceLoader()),
        analyst=AnalystAgent(runtime),
        verifier=VerifierAgent(runtime),
        synthesizer=SynthesizerAgent(runtime),
        policy=policy,
        safety_gate=safety_gate,
        artifact_store=FileArtifactStore(directory),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("role", list(AgentRole))
async def test_each_stage_retries_a_fresh_invocation_with_the_same_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, role: AgentRole,
) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("evidence_synthesis.orchestrator.asyncio.sleep", sleep)
    runtime = FailingRuntime(role)
    runner = _orchestrator(runtime, tmp_path)
    state = await runner.run(_request())

    assert state.status is TerminalStatus.COMPLETED
    assert runtime.calls.count(role) == 2
    assert runtime.payloads[0] == runtime.payloads[1]
    assert len(state.metrics) == 4
    assert [metric.attempts for metric in state.metrics if metric.role is role] == [2]
    failures = [event for event in state.events if event.status == "failed"]
    assert len(failures) == 1
    assert failures[0].stage == role.value
    assert failures[0].detail == "temporary stage failure"
    assert [
        event.status for event in state.events if event.stage == role.value
    ] == ["started", "failed", "started", "completed"]
    sleep.assert_awaited_once_with(1)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", list(AgentRole))
async def test_exhausted_retries_keep_partial_result_and_one_shot_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, role: AgentRole,
) -> None:
    monkeypatch.setattr("evidence_synthesis.orchestrator.asyncio.sleep", AsyncMock())
    runtime = FailingRuntime(role, always_fail=True)
    state = await _orchestrator(runtime, tmp_path).run(_request())

    assert state.status is TerminalStatus.PARTIAL
    assert state.terminal_reason == f"{role.value}_failed: temporary stage failure"
    assert state.synthesis is not None
    assert state.synthesis.partial
    assert all(metric.role is not role for metric in state.metrics)
    assert runtime.calls.count(role) == (3 if role is AgentRole.SYNTHESIZER else 2)
    fallback = [event for event in state.events if event.stage == "partial-synthesis"]
    if role is AgentRole.SYNTHESIZER:
        assert [event.status for event in fallback] == ["started", "failed"]
        assert fallback[-1].detail == "temporary stage failure"
    else:
        assert not fallback


@pytest.mark.asyncio
async def test_shared_deadline_can_prevent_a_configured_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("evidence_synthesis.orchestrator.asyncio.sleep", AsyncMock())
    runtime = FailingRuntime(AgentRole.EVIDENCE_EXTRACTOR)
    runner = _orchestrator(runtime, tmp_path)
    monkeypatch.setattr(runner, "_remaining", Mock(side_effect=[300, 0, 0]))

    state = await runner.run(_request())

    assert state.status is TerminalStatus.PARTIAL
    assert state.terminal_reason == "deadline_exceeded"
    assert runtime.calls == [AgentRole.EVIDENCE_EXTRACTOR]


@pytest.mark.asyncio
async def test_concurrent_runs_keep_their_own_state_and_artifacts(tmp_path: Path) -> None:
    runner = _orchestrator(DeterministicRuntime(), tmp_path)
    states = await asyncio.gather(runner.run(_request("first")), runner.run(_request("second")))

    assert states[0].run_id != states[1].run_id
    for state in states:
        assert state.status is TerminalStatus.COMPLETED
        assert state.evidence_extractor is not None
        assert len(state.metrics) == 4
        assert {record.source_id for record in state.evidence_extractor.records} == {
            state.request.sources[0].source_id,
        }
        assert (tmp_path / state.run_id / "state.json").exists()


class FalsySafetyGate:
    def __bool__(self) -> bool:
        return False

    async def evaluate_input(self, text: str) -> SafetyDecision:
        return SafetyDecision(allowed=True, text=text)

    async def evaluate_output(self, text: str) -> SafetyDecision:
        return SafetyDecision(allowed=False, text="Blocked by supplied gate.", action="CUSTOM_GATE")


@pytest.mark.asyncio
async def test_explicit_defaults_preserve_a_supplied_falsy_safety_gate(tmp_path: Path) -> None:
    runner = _orchestrator(DeterministicRuntime(), tmp_path, safety_gate=FalsySafetyGate())

    state = await runner.run(_request())

    assert state.status is TerminalStatus.BLOCKED
    assert state.synthesis is not None
    assert state.synthesis.answer == "Blocked by supplied gate."
    assert state.synthesis.safety_action == "CUSTOM_GATE"
