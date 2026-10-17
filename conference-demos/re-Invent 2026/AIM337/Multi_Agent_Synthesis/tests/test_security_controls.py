# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
import asyncio
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError
from pydantic import BaseModel, ValidationError

from evidence_synthesis import pdf_reader
from evidence_synthesis.agents import (
    AnalystAgent,
    EvidenceExtractorAgent,
    SynthesizerAgent,
    VerifierAgent,
)
from evidence_synthesis.artifacts import DuplicateRunError, FileArtifactStore, S3ArtifactStore
from evidence_synthesis.config import PipelinePolicy
from evidence_synthesis.deterministic_runtime import DeterministicRuntime
from evidence_synthesis.models import AgentRole, RunRequest, RunSummary, TerminalStatus
from evidence_synthesis.orchestrator import EvidenceSynthesisOrchestrator
from evidence_synthesis.runtime import RuntimeResult
from evidence_synthesis.safety import SafetyDecision
from evidence_synthesis.source_loader import SourceLoader
from evidence_synthesis.strands_runtime import StrandsRuntime


class CountingRuntime(DeterministicRuntime):
    def __init__(self, *, closure: bool = True) -> None:
        self.calls: list[AgentRole] = []
        self.closure = closure

    def invoke[OutputT: BaseModel](
        self, role: AgentRole, payload: BaseModel, output_type: type[OutputT], system_prompt: str,
    ) -> RuntimeResult[OutputT]:
        self.calls.append(role)
        result = super().invoke(role, payload, output_type, system_prompt)
        if role is AgentRole.VERIFIER:
            result.output.citation_closure_complete = self.closure
        return result


class RecordingSafety:
    def __init__(self, *, reject_sources: bool = False, unavailable_output: bool = False) -> None:
        self.inputs: list[str] = []
        self.outputs: list[str] = []
        self.reject_sources = reject_sources
        self.unavailable_output = unavailable_output

    async def evaluate_input(self, text: str) -> SafetyDecision:
        self.inputs.append(text)
        reject = self.reject_sources and "SOURCE_SECRET" in text
        return SafetyDecision(not reject, "Source blocked." if reject else text, "TEST")

    async def evaluate_output(self, text: str) -> SafetyDecision:
        self.outputs.append(text)
        if self.unavailable_output:
            raise RuntimeError("Sensitive provider diagnostic")
        return SafetyDecision(False, "Answer blocked.", "TEST")


def request(run_id: str | None = None, content: str = "SOURCE_SECRET") -> RunRequest:
    return RunRequest(
        question="What is supported?", run_id=run_id,
        sources=[{"source_id": "one", "content": content}],
    )


def runner(
    directory: Path, runtime: CountingRuntime, safety: RecordingSafety,
    policy: PipelinePolicy | None = None,
) -> EvidenceSynthesisOrchestrator:
    return EvidenceSynthesisOrchestrator(
        evidence_extractor=EvidenceExtractorAgent(
            runtime, SourceLoader(), safety,
        ),
        analyst=AnalystAgent(runtime), verifier=VerifierAgent(runtime),
        synthesizer=SynthesizerAgent(runtime), safety_gate=safety,
        artifact_store=FileArtifactStore(directory), policy=policy,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("closure", [True, False])
async def test_complete_and_partial_answers_are_screened_without_saving_raw_text(
    tmp_path: Path, closure: bool,
) -> None:
    runtime = CountingRuntime(closure=closure)
    safety = RecordingSafety()
    state = await runner(
        tmp_path, runtime, safety, PipelinePolicy(max_verification_cycles=1),
    ).run(request())

    assert state.status is TerminalStatus.BLOCKED
    assert state.synthesis.answer == "Answer blocked."
    assert len(safety.outputs) == 1
    assert "SOURCE_SECRET" in safety.outputs[0]
    for path in (tmp_path / state.run_id).iterdir():
        assert "SOURCE_SECRET" not in path.read_text()
    summary = RunSummary.model_validate_json(
        (tmp_path / state.run_id / "state.json").read_text(),
    )
    assert summary.synthesis.answer == "Answer blocked."
    assert "request" not in summary.model_dump()


@pytest.mark.asyncio
async def test_output_safety_service_failure_fails_closed(tmp_path: Path) -> None:
    state = await runner(
        tmp_path, CountingRuntime(closure=False), RecordingSafety(unavailable_output=True),
        PipelinePolicy(max_verification_cycles=1),
    ).run(request())
    assert state.status is TerminalStatus.BLOCKED
    assert state.terminal_reason == "output_safety_unavailable"
    saved = (tmp_path / state.run_id / "state.json").read_text()
    assert "SOURCE_SECRET" not in saved
    assert "Sensitive provider diagnostic" not in saved


@pytest.mark.asyncio
async def test_untrusted_source_is_checked_before_any_model_call(tmp_path: Path) -> None:
    runtime = CountingRuntime()
    safety = RecordingSafety(reject_sources=True)
    state = await runner(tmp_path, runtime, safety).run(request())
    assert state.status is TerminalStatus.BLOCKED
    assert state.terminal_reason == "source_safety_policy"
    assert not runtime.calls
    assert len(safety.inputs) == 2
    assert "SOURCE_SECRET" in safety.inputs[-1]


@pytest.mark.asyncio
async def test_budget_failure_records_spent_tokens_without_retry(tmp_path: Path) -> None:
    runtime = CountingRuntime()
    state = await runner(
        tmp_path, runtime, RecordingSafety(), PipelinePolicy(token_budget=1),
    ).run(request())
    assert state.status is TerminalStatus.PARTIAL
    assert state.total_tokens > 1
    assert runtime.calls == [AgentRole.EVIDENCE_EXTRACTOR]
    assert state.token_usage_complete


@pytest.mark.asyncio
async def test_unknown_timeout_usage_prevents_retry_and_partial_model_calls(tmp_path: Path) -> None:
    class TimeoutRuntime(CountingRuntime):
        async def invoke_async[OutputT: BaseModel](
            self, role: AgentRole, payload: BaseModel,
            output_type: type[OutputT], system_prompt: str,
        ) -> RuntimeResult[OutputT]:
            if role is AgentRole.SYNTHESIZER:
                self.calls.append(role)
                raise TimeoutError("Model response timed out")
            return self.invoke(role, payload, output_type, system_prompt)

    runtime = TimeoutRuntime()
    state = await runner(tmp_path, runtime, RecordingSafety()).run(request())
    assert state.status is TerminalStatus.PARTIAL
    assert not state.token_usage_complete
    assert runtime.calls.count(AgentRole.SYNTHESIZER) == 1


@pytest.mark.asyncio
async def test_concurrent_duplicate_run_cannot_replace_existing_artifacts(tmp_path: Path) -> None:
    service = runner(tmp_path, CountingRuntime(), RecordingSafety())
    results = await asyncio.gather(
        service.run(request("a" * 32, "first input")),
        service.run(request("a" * 32, "second input")),
        return_exceptions=True,
    )
    assert sum(isinstance(value, DuplicateRunError) for value in results) == 1
    winner = next(value for value in results if not isinstance(value, BaseException))
    saved = RunSummary.model_validate_json((tmp_path / ("a" * 32) / "state.json").read_text())
    assert saved.request_fingerprint == winner.request.fingerprint()


def test_s3_reservation_uses_a_conditional_write() -> None:
    class Client:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def put_object(self, **kwargs: Any) -> None:
            self.calls.append(kwargs)
            if len(self.calls) > 1:
                raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")

    client = Client()
    store = S3ArtifactStore("results", client=client)
    store.reserve_run("a" * 32)
    with pytest.raises(DuplicateRunError):
        store.reserve_run("a" * 32)
    assert all(call["IfNoneMatch"] == "*" for call in client.calls)
    assert all(call["Key"] == f"reservations/{'a' * 32}.json" for call in client.calls)


@pytest.mark.asyncio
async def test_async_runtime_propagates_cancellation_to_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started, stopped = threading.Event(), threading.Event()

    def blocking(*args: object, cancel_signal: threading.Event, **kwargs: object) -> None:
        started.set()
        if cancel_signal.wait(timeout=2):
            stopped.set()

    runtime = StrandsRuntime()
    monkeypatch.setattr(runtime, "invoke", blocking)
    task = asyncio.create_task(runtime.invoke_async(
        AgentRole.ANALYST, request(), RunRequest, "Instructions",
    ))
    while not started.is_set():
        await asyncio.sleep(0.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.wait(timeout=1)


def test_request_rejects_duplicate_sources_and_excess_source_count() -> None:
    source = {"source_id": "one", "content": "text"}
    with pytest.raises(ValidationError, match="unique"):
        RunRequest(question="Question?", sources=[source, source])
    with pytest.raises(ValidationError):
        RunRequest(question="Question?", sources=[
            {"source_id": str(i), "content": "text"} for i in range(21)
        ])


def _slow_pdf_parser(data: bytes, output: Any) -> None:
    time.sleep(5)


def test_pdf_worker_is_stopped_at_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pdf_reader, "_read_pages", _slow_pdf_parser)
    monkeypatch.setattr(pdf_reader, "PDF_TIMEOUT_SECONDS", 0.05)
    started = time.monotonic()
    with pytest.raises(ValueError, match="time limit"):
        pdf_reader.read_pdf_pages(b"synthetic input")
    assert time.monotonic() - started < 3
