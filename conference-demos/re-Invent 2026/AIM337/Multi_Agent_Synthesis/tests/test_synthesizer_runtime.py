# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
"""Real SDK and orchestrator checks with scripted model responses, not Bedrock."""

from __future__ import annotations

import copy
import inspect
import json
from types import SimpleNamespace

import pytest
import strands.models
from strands import Agent
from strands.models.model import Model

from evidence_synthesis.agents.synthesizer import SynthesizerAgent
from evidence_synthesis.artifacts import FileArtifactStore
from evidence_synthesis.config import PipelinePolicy, StrandsSettings
from evidence_synthesis.models import (
    AgentRole,
    AnalysisResult,
    EvidenceExtractorResult,
    InvocationMetrics,
    RunRequest,
    SynthesisInput,
    TerminalStatus,
    VerificationStatus,
)
from evidence_synthesis.orchestrator import EvidenceSynthesisOrchestrator
from evidence_synthesis.runtime import RuntimeInvocationError, RuntimeResult
from evidence_synthesis.safety import SafetyDecision
from evidence_synthesis.strands_runtime import StrandsRuntime
from evidence_synthesis.synthesis_contract import SynthesisDraft
from evidence_synthesis.validation import ContractError, validate_synthesis

pytestmark = pytest.mark.skipif(
    "limits" not in inspect.signature(Agent.__call__).parameters,
    reason="Install the pinned Strands version to test bounded structured-output recovery.",
)
GOOD = {
    "answer": (
        "Under SSP2-4.5, likely global mean sea-level rise by 2100 is 0.44–0.76 m "
        "relative to 1995–2014, with medium assessment confidence [E001]."
    ),
    "limitations": ["The global projection does not supply a local flood-depth assessment."],
}
BAD = {"answer": "MODEL_OUTPUT [E001].</answer><limitations>Lost caveat", "limitations": []}


class ScriptedModel(Model):
    def __init__(self, responses):
        self.responses = responses
        self.requests = []
        self.config = {"model_id": "scripted", "context_window_limit": 200_000}

    def update_config(self, **model_config):
        self.config.update(model_config)

    def get_config(self):
        return self.config

    def structured_output(self, *args, **kwargs):
        raise AssertionError("The runtime must use the structured-output tool")

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        turn = len(self.requests)
        assert turn < len(self.responses), "Unexpected extra model call"
        self.requests.append(copy.deepcopy(messages))
        assert any(tool["name"] == "SynthesisDraft" for tool in tool_specs)
        tool_id = f"scripted-{turn}"
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockStart": {
            "contentBlockIndex": 0,
            "start": {"toolUse": {"toolUseId": tool_id, "name": "SynthesisDraft"}},
        }}
        yield {"contentBlockDelta": {
            "contentBlockIndex": 0,
            "delta": {"toolUse": {"input": json.dumps(self.responses[turn])}},
        }}
        yield {"contentBlockStop": {"contentBlockIndex": 0}}
        yield {"messageStop": {"stopReason": "tool_use"}}
        yield {"metadata": {
            "usage": {"inputTokens": 12, "outputTokens": 8, "totalTokens": 20},
            "metrics": {"latencyMs": 1},
        }}


@pytest.fixture
def harness(monkeypatch):
    models = []
    scripts = []

    def create_model(**kwargs):
        assert scripts, "Unexpected extra agent invocation"
        model = ScriptedModel(scripts.pop(0))
        models.append(model)
        return model

    monkeypatch.setattr(strands.models, "BedrockModel", create_model)
    runtime = StrandsRuntime(StrandsSettings(_env_file=None))
    return SimpleNamespace(agent=SynthesizerAgent(runtime), models=models, scripts=scripts)


def payload():
    return SynthesisInput.model_validate({
        "question": "What does the supplied projection establish?",
        "evidence": [{
            "evidence_id": "E001", "source_id": "projection", "title": "Projection fixture",
            "excerpt": GOOD["answer"], "locator": "projection#1", "relevance": 1,
        }],
        "verification": {
            "claims": [{
                "claim": {
                    "claim_id": "C1", "statement": GOOD["answer"],
                    "evidence_ids": ["E001"], "confidence": 0.5,
                },
                "status": "qualified",
                "reason": "Keep the scenario, likely range and medium assessment confidence.",
                "independent_source_count": 1,
            }],
            "evidence_gaps": GOOD["limitations"], "citation_closure_complete": True,
        },
    })


def messages_contain_feedback(model):
    return any(
        "Remove XML tags" in json.dumps(message)
        for message in model.requests[1]
    )


@pytest.mark.asyncio
async def test_markup_feedback_repairs_second_turn(harness):
    harness.scripts.append([BAD, GOOD])
    result = await harness.agent.synthesize(payload())
    assert result.output.answer == GOOD["answer"]
    assert result.output.limitations == GOOD["limitations"]
    assert result.metrics.total_tokens == 40
    assert len(harness.models[0].requests) == 2
    assert messages_contain_feedback(harness.models[0])


@pytest.mark.asyncio
async def test_two_bad_turns_raise_without_a_third_call(harness):
    harness.scripts.append([BAD, BAD, GOOD])
    with pytest.raises(RuntimeInvocationError) as caught:
        await harness.agent.synthesize(payload())
    assert len(harness.models[0].requests) == 2
    assert messages_contain_feedback(harness.models[0])
    assert caught.value.usage_complete is True
    assert caught.value.metrics.total_tokens == 40


@pytest.mark.asyncio
async def test_partial_mapping_preserves_qualifier_and_reason(harness):
    harness.scripts.append([GOOD])
    supplied = payload()
    supplied.partial = True
    supplied.terminal_reason = "verification_cycle_limit"
    supplied.verification.citation_closure_complete = False
    result = await harness.agent.synthesize(supplied)
    validate_synthesis(result.output, supplied.verification, supplied.evidence)
    assert result.output.partial is True
    assert result.output.terminal_reason == "verification_cycle_limit"
    assert "medium assessment confidence" in result.output.answer
    assert [citation.evidence_id for citation in result.output.citations] == ["E001"]


@pytest.mark.asyncio
async def test_unknown_evidence_still_fails_after_schema_validation(harness):
    harness.scripts.append([{**GOOD, "answer": "A claim [E999]."}])
    supplied = payload()
    result = await harness.agent.synthesize(supplied)
    with pytest.raises(ContractError, match="inline_citation_outside_reviewed_evidence"):
        validate_synthesis(result.output, supplied.verification, supplied.evidence)


class FixtureStages:
    def __init__(self, supplied):
        self.supplied = supplied

    def result(self, role, output):
        return RuntimeResult(
            output=output,
            metrics=InvocationMetrics(role=role, model_id="fixture", latency_ms=0),
        )

    async def extract(self, request, base_dir):
        return self.result(AgentRole.EVIDENCE_EXTRACTOR, EvidenceExtractorResult(
            records=self.supplied.evidence,
        ))

    async def analyze(self, request):
        return self.result(AgentRole.ANALYST, AnalysisResult(
            claims=[item.claim for item in self.supplied.verification.claims],
        ))

    async def verify(self, request):
        return self.result(AgentRole.VERIFIER, self.supplied.verification.model_copy(deep=True))


class RecordingSafety:
    def __init__(self, *, deny=False):
        self.deny = deny
        self.outputs = []

    async def evaluate_input(self, text):
        return SafetyDecision(allowed=True, text=text)

    async def evaluate_output(self, text):
        self.outputs.append(json.loads(text))
        return SafetyDecision(
            allowed=not self.deny,
            text="Blocked in boundary test." if self.deny else text,
            action="TEST_BLOCK" if self.deny else "NONE",
        )


def orchestrator(harness, tmp_path, supplied, gate=None):
    fixtures = FixtureStages(supplied)
    return EvidenceSynthesisOrchestrator(
        evidence_extractor=fixtures, analyst=fixtures, verifier=fixtures,
        synthesizer=harness.agent,
        policy=PipelinePolicy(
            _env_file=None, max_retries=1, max_verification_cycles=1,
            deadline_seconds=30, token_budget=10_000,
        ),
        safety_gate=gate,
        artifact_store=FileArtifactStore(tmp_path),
    )


def request(supplied):
    return RunRequest(
        question=supplied.question,
        sources=[{"source_id": "fixture", "content": "Inputs supplied by fixture stages."}],
    )


@pytest.mark.asyncio
async def test_exhausted_stage_and_partial_attempt_publish_only_fixed_fallback(harness, tmp_path):
    harness.scripts.extend([[BAD, BAD, GOOD] for _ in range(3)])
    supplied = payload()
    gate = RecordingSafety()
    service = orchestrator(harness, tmp_path, supplied, gate)
    state = await service.run(request(supplied))
    assert [len(model.requests) for model in harness.models] == [2, 2, 2]
    assert state.total_tokens == 120  # Scripted usage, not billed model tokens.
    assert state.token_usage_complete is True
    assert state.status is TerminalStatus.PARTIAL
    assert state.synthesis.answer == (
        "The pipeline ended before a verified synthesis could be produced."
    )
    assert state.synthesis.citations == []
    assert not gate.outputs  # The fixed fallback contains no generated content.
    assert all(
        "MODEL_OUTPUT" not in path.read_text()
        for path in (tmp_path / state.run_id).iterdir()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("deny", [False, True])
async def test_partial_synthesis_reaches_output_safety(harness, tmp_path, deny):
    harness.scripts.append([GOOD])
    supplied = payload()
    supplied.verification.citation_closure_complete = False
    gate = RecordingSafety(deny=deny)
    service = orchestrator(harness, tmp_path, supplied, gate)
    state = await service.run(request(supplied))
    assert len(harness.models) == 1
    assert len(gate.outputs) == 1
    assert gate.outputs[0]["partial"] is True
    assert gate.outputs[0]["terminal_reason"] == "verification_cycle_limit"
    assert gate.outputs[0]["answer"] == GOOD["answer"]
    assert gate.outputs[0]["citations"][0]["evidence_id"] == "E001"
    if deny:
        assert state.status is TerminalStatus.BLOCKED
        assert state.synthesis.answer == "Blocked in boundary test."
    else:
        assert state.status is TerminalStatus.PARTIAL
        assert state.synthesis.answer == GOOD["answer"]


@pytest.mark.asyncio
async def test_all_rejected_reviews_skip_generation(harness, tmp_path):
    supplied = payload()
    for item in supplied.verification.claims:
        item.status = VerificationStatus.REJECTED
    service = orchestrator(harness, tmp_path, supplied)
    state = await service.run(request(supplied))
    assert harness.models == []
    assert state.status is TerminalStatus.COMPLETED
    assert state.synthesis.answer == "No claims survived verification."


@pytest.mark.asyncio
async def test_bad_citation_is_not_published_and_partial_can_recover(harness, tmp_path):
    harness.scripts.extend([[{**GOOD, "answer": "MODEL_OUTPUT [E999]."}], [GOOD]])
    supplied = payload()
    gate = RecordingSafety()
    state = await orchestrator(harness, tmp_path, supplied, gate).run(request(supplied))
    assert [len(model.requests) for model in harness.models] == [1, 1]
    assert state.status is TerminalStatus.PARTIAL
    assert state.terminal_reason == "inline_citation_outside_reviewed_evidence"
    assert state.synthesis.answer == GOOD["answer"]
    assert len(gate.outputs) == 1
    assert all(
        "MODEL_OUTPUT" not in path.read_text()
        for path in (tmp_path / state.run_id).iterdir()
    )


def test_draft_has_no_application_owned_fields():
    assert set(SynthesisDraft.model_fields) == {"answer", "limitations"}
