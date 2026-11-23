# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
import hashlib
import json
import logging
import threading
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, ValidationError

from evidence_synthesis.agents.single_agent import SingleAgent
from evidence_synthesis.artifacts import DuplicateRunError, FileArtifactStore
from evidence_synthesis.config import PipelinePolicy
from evidence_synthesis.factory import build_runner
from evidence_synthesis.models import (
    AgentRole,
    Citation,
    ExecutionMode,
    InvocationMetrics,
    RunRequest,
    RunSummary,
    SingleAgentInput,
    SourceChunk,
    SynthesisResult,
    TerminalStatus,
)
from evidence_synthesis.runtime import RuntimeInvocationError, RuntimeResult
from evidence_synthesis.safety import SafetyDecision
from evidence_synthesis.single_agent_runner import SingleAgentRunner
from evidence_synthesis.source_loader import SourceLoader
from evidence_synthesis.ui.artifact_progress import FileArtifactReader, start_run


def request(**kwargs: object) -> RunRequest:
    return RunRequest.model_validate({
        "question": "What is supported?", "execution_mode": "single_agent",
        "sources": [
            {"source_id": "one", "content": "SOURCE_SECRET first paragraph.\n\nSecond paragraph."},
            {"source_id": "two", "content": "Other supplied text.", "metadata": {"note": "hint"}},
        ],
        **kwargs,
    })


class ScriptedRuntime:
    def __init__(self, *responses: SynthesisResult | Exception) -> None:
        self.responses = responses or (SynthesisResult(answer="A cited answer [E001]."),)
        self.inputs: list[SingleAgentInput] = []

    def invoke[OutputT: BaseModel](
        self, role: AgentRole, payload: BaseModel, output_type: type[OutputT], system_prompt: str,
    ) -> RuntimeResult[OutputT]:
        assert role is AgentRole.SINGLE_AGENT
        assert isinstance(payload, SingleAgentInput)
        assert system_prompt == SingleAgent.system_prompt
        self.inputs.append(payload.model_copy(deep=True))
        response = self.responses[len(self.inputs) - 1]
        if isinstance(response, Exception):
            raise response
        return RuntimeResult(
            output=output_type.model_validate(response.model_dump()),
            metrics=InvocationMetrics(
                role=role, model_id="test", latency_ms=1,
                input_tokens=12, output_tokens=8, total_tokens=20,
            ),
        )


class SafetyProbe:
    def __init__(self, *, block: str = "", unavailable: str = "") -> None:
        self.block = block
        self.unavailable = unavailable
        self.inputs: list[str] = []
        self.outputs: list[str] = []

    def _decision(self, stage: str, text: str) -> SafetyDecision:
        if self.unavailable == stage:
            raise RuntimeError("PROVIDER_SECRET")
        if self.block == stage:
            return SafetyDecision(False, "Blocked by safety.", "BLOCK")
        return SafetyDecision(True, text, "NONE")

    async def evaluate_input(self, text: str) -> SafetyDecision:
        self.inputs.append(text)
        return self._decision("input" if len(self.inputs) == 1 else "source", text)

    async def evaluate_output(self, text: str) -> SafetyDecision:
        self.outputs.append(text)
        return self._decision("output", text)


def runner(
    directory: Path, runtime: ScriptedRuntime, *, safety: SafetyProbe | None = None,
    policy: PipelinePolicy | None = None, loader: SourceLoader | None = None,
) -> SingleAgentRunner:
    return SingleAgentRunner(
        agent=SingleAgent(runtime), source_loader=loader or SourceLoader(),
        artifact_store=FileArtifactStore(directory),
        safety_gate=safety or SafetyProbe(), policy=policy or PipelinePolicy(_env_file=None),
    )


@pytest.mark.asyncio
async def test_single_agent_uses_all_loaded_chunks_and_returns_only_a_synthesis(
    tmp_path: Path,
) -> None:
    invented_citation = Citation(
        evidence_id="E001", source_id="invented", title="Invented", locator="made-up",
    )
    runtime = ScriptedRuntime(SynthesisResult(
        answer="A cited answer [E001, E003].", citations=[invented_citation],
        limitations=["A limitation [E002]."], partial=True,
        terminal_reason="MODEL_SECRET", safety_action="MODEL_SECRET",
    ))
    safety = SafetyProbe()
    state = await runner(tmp_path, runtime, safety=safety).run(request(max_evidence=1))

    assert state.status is TerminalStatus.COMPLETED
    assert state.evidence_extractor is state.analysis is state.verification is None
    assert state.verification_cycles == 0
    assert len(runtime.inputs) == 1
    payload = runtime.inputs[0]
    assert list(payload.model_dump()) == ["question", "chunks"]
    assert [chunk.chunk_id for chunk in payload.chunks] == ["E001", "E002", "E003"]
    assert [chunk.content for chunk in payload.chunks] == [
        "SOURCE_SECRET first paragraph.", "Second paragraph.", "Other supplied text.",
    ]
    assert len(safety.inputs) == 2
    assert "SOURCE_SECRET" in safety.inputs[-1]
    assert state.synthesis is not None
    assert not state.synthesis.partial
    assert state.synthesis.terminal_reason is None
    assert state.synthesis.safety_action == "NONE"
    assert [citation.evidence_id for citation in state.synthesis.citations] == [
        "E001", "E003", "E002",
    ]
    assert all(citation.source_id in {"one", "two"} for citation in state.synthesis.citations)
    assert "MODEL_SECRET" not in safety.outputs[0]
    assert len(state.metrics) == 1
    assert state.total_tokens == 20
    summary = RunSummary.model_validate_json((tmp_path / state.run_id / "state.json").read_text())
    assert summary.restore(state.request).synthesis == state.synthesis
    assert {path.name for path in (tmp_path / state.run_id).iterdir()} == {
        "single_agent.json", "state.json", "answer.md",
    }
    for path in (tmp_path / state.run_id).iterdir():
        assert "SOURCE_SECRET" not in path.read_text()
        assert "invented" not in path.read_text()


@pytest.mark.asyncio
@pytest.mark.parametrize(("answer", "limitations", "reason"), [
    ("Missing a citation.", [], "missing_inline_citations"),
    ("Unsupported reference [E099].", [], "inline_citation_outside_supplied_sources"),
    ("A citation [E001].", ["Unknown [E002, E999]."], "inline_citation_outside_supplied_sources"),
    ("A citation [E001].</answer>", [], "unexpected_output_markup"),
    ("A citation [E001].", ['<parameter name="limitations">[]'], "unexpected_output_markup"),
    ('147 claims [C4]. [{"evidence_id":"E001"}]', [], "invalid_inline_citation"),
    ("No answer citation.", ["A caveat [E001]."], "missing_inline_citations"),
])
async def test_invalid_citations_fail_without_saving_model_output(
    tmp_path: Path, answer: str, limitations: list[str], reason: str,
) -> None:
    safety = SafetyProbe()
    runtime = ScriptedRuntime(SynthesisResult(answer=answer, limitations=limitations))
    state = await runner(tmp_path, runtime, safety=safety).run(request())
    assert state.status is TerminalStatus.FAILED
    assert state.terminal_reason == reason
    assert not safety.outputs
    assert len(runtime.inputs) == 1
    assert state.total_tokens == 20
    assert answer not in (tmp_path / state.run_id / "state.json").read_text()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["input", "source", "output"])
@pytest.mark.parametrize("unavailable", [False, True])
async def test_safety_blocks_input_sources_and_output_without_leaking_content(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, stage: str, unavailable: bool,
) -> None:
    caplog.set_level(logging.INFO, logger="evidence_synthesis.single_agent_runner")
    runtime = ScriptedRuntime(SynthesisResult(answer="MODEL_SECRET [E001]."))
    safety = SafetyProbe(**{"unavailable" if unavailable else "block": stage})
    state = await runner(tmp_path, runtime, safety=safety).run(request())
    assert state.status is TerminalStatus.BLOCKED
    assert state.terminal_reason == f"{stage}_safety_{'unavailable' if unavailable else 'policy'}"
    assert len(runtime.inputs) == (1 if stage == "output" else 0)
    saved_text = "\n".join(path.read_text() for path in (tmp_path / state.run_id).iterdir())
    for secret in ("MODEL_SECRET", "SOURCE_SECRET", "PROVIDER_SECRET"):
        assert secret not in saved_text
        assert secret not in caplog.text


@pytest.mark.asyncio
async def test_known_usage_failure_retries_fresh_input_and_counts_both_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = RuntimeInvocationError(
        InvocationMetrics(
            role=AgentRole.SINGLE_AGENT, model_id="test", latency_ms=1, total_tokens=7,
        ),
        usage_complete=True,
    )
    runtime = ScriptedRuntime(error, SynthesisResult(answer="A cited answer [E001]."))
    monkeypatch.setattr("evidence_synthesis.single_agent_runner.asyncio.sleep", AsyncMock())
    state = await runner(tmp_path, runtime).run(request())
    assert state.status is TerminalStatus.COMPLETED
    assert len(runtime.inputs) == 2
    assert runtime.inputs[0] == runtime.inputs[1]
    assert [metric.attempts for metric in state.metrics] == [1, 2]
    assert state.total_tokens == 27
    assert [event.status for event in state.events] == ["started", "failed", "started", "completed"]


@pytest.mark.asyncio
async def test_unknown_usage_failure_stops_without_retry(tmp_path: Path) -> None:
    error = RuntimeInvocationError(
        InvocationMetrics(
            role=AgentRole.SINGLE_AGENT, model_id="test", latency_ms=1, total_tokens=7,
        ),
        usage_complete=False,
    )
    runtime = ScriptedRuntime(error)
    state = await runner(tmp_path, runtime).run(request())
    assert state.status is TerminalStatus.FAILED
    assert state.terminal_reason == "model_usage_unavailable"
    assert not state.token_usage_complete
    assert state.total_tokens == 7
    assert len(runtime.inputs) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("budget", "calls", "tokens"), [(0, 0, 0), (1, 1, 20)])
async def test_token_limit_stops_without_a_second_model_call(
    tmp_path: Path, budget: int, calls: int, tokens: int,
) -> None:
    runtime = ScriptedRuntime()
    state = await runner(
        tmp_path, runtime, policy=PipelinePolicy(_env_file=None, token_budget=budget),
    ).run(request())
    assert state.status is TerminalStatus.FAILED
    assert state.terminal_reason == "token_budget_exceeded"
    assert len(runtime.inputs) == calls
    assert state.total_tokens == tokens
    assert state.token_usage_complete


@pytest.mark.asyncio
async def test_model_timeout_stops_with_unknown_usage(tmp_path: Path) -> None:
    service = runner(tmp_path, ScriptedRuntime())
    operation = AsyncMock(side_effect=TimeoutError("PROVIDER_SECRET"))
    service._agent.synthesize = operation
    state = await service.run(request())
    assert state.terminal_reason == "model_timeout_usage_unavailable"
    assert not state.token_usage_complete
    operation.assert_awaited_once()


@pytest.mark.asyncio
async def test_source_timeout_cancels_loading_before_model_invocation(tmp_path: Path) -> None:
    stopped = threading.Event()
    class SlowLoader(SourceLoader):
        def load_all(self, sources: object, base_dir: Path, *, cancel_signal: object) -> list:
            assert isinstance(cancel_signal, threading.Event)
            if cancel_signal.wait(timeout=2):
                stopped.set()
            return []

    runtime = ScriptedRuntime()
    state = await runner(
        tmp_path, runtime, loader=SlowLoader(),
        policy=PipelinePolicy(_env_file=None, deadline_seconds=0.1),
    ).run(request())
    assert state.terminal_reason == "deadline_exceeded"
    assert stopped.wait(timeout=1)
    assert not runtime.inputs
    assert state.token_usage_complete


@pytest.mark.asyncio
async def test_duplicate_run_is_rejected_and_saved_single_result_recovers(tmp_path: Path) -> None:
    service = runner(tmp_path, ScriptedRuntime())
    original = request(run_id="a" * 32)
    state = await service.run(original)
    with pytest.raises(DuplicateRunError):
        await service.run(original)
    reader = FileArtifactReader(tmp_path, original.run_id)
    reader.poll()
    assert "single_agent" in reader.outputs
    def lost_response() -> None:
        raise TimeoutError("Lost response")
    active = start_run(original, reader, lost_response)
    active.future.exception(timeout=5)
    restored = active.result()
    assert restored.summary_only
    assert restored.request.execution_mode is ExecutionMode.SINGLE_AGENT
    assert restored.synthesis == state.synthesis
    with pytest.raises(ValueError, match="different request"):
        reader.outputs["state"].restore(original.model_copy(update={
            "execution_mode": ExecutionMode.MULTI_AGENT,
        }))


def test_mode_serialization_preserves_legacy_requests_and_fingerprints() -> None:
    legacy = {
        "question": "What is supported?",
        "sources": [{
            "source_id": "one", "uri": None, "content": "Supplied text.",
            "title": None, "methodology": "unspecified", "media_type": None, "metadata": {},
        }],
        "max_evidence": 20, "run_id": None,
    }
    default = RunRequest.model_validate(legacy)
    explicit = RunRequest.model_validate({**legacy, "execution_mode": "multi_agent"})
    expected_hash = hashlib.sha256(json.dumps(legacy, sort_keys=True).encode()).hexdigest()
    for value in (default, explicit):
        assert value.execution_mode is ExecutionMode.MULTI_AGENT
        assert value.model_dump(mode="json") == legacy
        assert json.loads(value.model_dump_json()) == legacy
        assert value.fingerprint() == expected_hash
    single = RunRequest.model_validate({**legacy, "execution_mode": "single_agent"})
    assert single.model_dump(mode="json")["execution_mode"] == "single_agent"
    assert single.fingerprint() != expected_hash
    with pytest.raises(ValidationError):
        RunRequest.model_validate({**legacy, "execution_mode": "compare"})


@pytest.mark.asyncio
async def test_cli_and_factory_dispatch_single_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    from evidence_synthesis.cli import _parser, _run

    manifest = tmp_path / "manifest.json"
    manifest.write_text(request(execution_mode="multi_agent").model_dump_json())
    args = _parser().parse_args([
        "run", "--manifest", str(manifest), "--mode", "single_agent",
        "--output", str(tmp_path / "runs"), "--json",
    ])
    assert await _run(args) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["request"]["execution_mode"] == "single_agent"
    assert len(output["metrics"]) == 1
    assert output["metrics"][0]["role"] == "single_agent"
    assert output["verification"] is None
    assert output["synthesis"]["citations"]
    assert "simulation" in output["synthesis"]["answer"]
    assert isinstance(build_runner(
        execution_mode=ExecutionMode.SINGLE_AGENT, runtime_name="deterministic",
    ), SingleAgentRunner)


@pytest.mark.asyncio
async def test_no_readable_sources_skips_inference(tmp_path: Path) -> None:
    class EmptyLoader(SourceLoader):
        def load_all(self, *args: object, **kwargs: object) -> list[SourceChunk]:
            return []
    runtime = ScriptedRuntime()
    state = await runner(tmp_path, runtime, loader=EmptyLoader()).run(request())
    assert state.terminal_reason == "no_readable_sources"
    assert not runtime.inputs


@pytest.mark.asyncio
async def test_runners_reject_mismatched_modes_before_starting(tmp_path: Path) -> None:
    for mode in ExecutionMode:
        service = build_runner(
            execution_mode=mode, runtime_name="deterministic", output_dir=tmp_path,
        )
        other = (
            ExecutionMode.SINGLE_AGENT
            if mode is ExecutionMode.MULTI_AGENT else ExecutionMode.MULTI_AGENT
        )
        with pytest.raises(ValueError, match="execution_mode"):
            await service.run(request(execution_mode=other))
    assert not list(tmp_path.iterdir())
