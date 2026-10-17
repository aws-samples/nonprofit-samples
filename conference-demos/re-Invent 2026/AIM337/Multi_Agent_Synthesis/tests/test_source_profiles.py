# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel, ValidationError

from evidence_synthesis.agents import (
    AnalystAgent,
    EvidenceExtractorAgent,
    SynthesizerAgent,
    VerifierAgent,
)
from evidence_synthesis.artifacts import FileArtifactStore
from evidence_synthesis.config import PipelinePolicy
from evidence_synthesis.deterministic_runtime import DeterministicRuntime
from evidence_synthesis.models import (
    AgentRole,
    AnalysisInput,
    EvidenceExtractorInput,
    EvidenceExtractorResult,
    EvidenceRecord,
    ResearchState,
    RunRequest,
    RunSummary,
    SourceChunk,
    SourceContext,
    SourceProfile,
    SourceReference,
    SynthesisInput,
    TerminalStatus,
    VerificationInput,
    VerificationResult,
)
from evidence_synthesis.orchestrator import EvidenceSynthesisOrchestrator
from evidence_synthesis.runtime import RuntimeResult
from evidence_synthesis.source_loader import SourceLoader
from evidence_synthesis.source_profiles import (
    excerpt_is_supported,
    grounded_profiles,
    merge_evidence_gaps,
)


def _chunk(source_id: str = "records") -> SourceChunk:
    return SourceChunk(
        chunk_id=f"{source_id}:1", source_id=source_id, title="Records",
        content="Measurements were collected hourly.\nValues are estimates.",
        locator=f"{source_id}.txt#paragraph=1",
    )


def _record(chunk: SourceChunk) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id="E001", source_id=chunk.source_id, title=chunk.title,
        excerpt=chunk.content, locator=chunk.locator, relevance=0.8,
    )


def _profile(chunk: SourceChunk) -> SourceProfile:
    reference = SourceReference(
        locator=chunk.locator, excerpt="Measurements were collected hourly.",
    )
    return SourceProfile(
        source_id=chunk.source_id, title="Hourly measurements",
        description="This source describes measurements collected hourly.",
        references=[reference],
        context=[SourceContext(note="The values are estimates.", references=[
            SourceReference(locator=chunk.locator, excerpt="Values are estimates."),
        ])],
    )


def test_profiles_are_grounded_and_domain_neutral() -> None:
    chunk = _chunk()
    profiles, gaps = grounded_profiles([chunk], [_record(chunk)], [_profile(chunk)])

    assert not gaps
    assert profiles[0].context[0].note == "The values are estimates."
    assert profiles[0].origin == "extracted"
    assert "geography" not in profiles[0].model_dump()
    assert "methodology" not in profiles[0].model_dump()
    assert "stance" not in profiles[0].model_dump()


@pytest.mark.parametrize("tampering", ["source", "locator", "excerpt", "empty", "mechanical"])
def test_ungrounded_descriptions_fall_back_without_retrying(tampering: str) -> None:
    chunk = _chunk()
    proposed = _profile(chunk)
    if tampering == "source":
        proposed.source_id = "invented-source"
    elif tampering == "locator":
        proposed.references[0].locator = "invented.txt#page=1"
    elif tampering == "excerpt":
        proposed.references[0].excerpt = "These are authoritative measurements."
    else:
        proposed.references = []
        if tampering == "mechanical":
            proposed.origin = "mechanical"

    profiles, gaps = grounded_profiles([chunk], [_record(chunk)], [proposed])

    assert gaps
    assert len(profiles) == 1
    assert profiles[0].source_id == chunk.source_id
    assert profiles[0].origin == "mechanical"
    assert profiles[0].context == []
    assert profiles[0].references == []


def test_bad_context_is_discarded_without_losing_supported_description() -> None:
    chunk = _chunk()
    proposed = _profile(chunk)
    proposed.context[0].references[0].excerpt = "The results prove a causal relationship."

    profiles, gaps = grounded_profiles([chunk], [_record(chunk)], [proposed])

    assert profiles[0].description == proposed.description
    assert profiles[0].context == []
    assert gaps


def test_profiles_cannot_bypass_evidence_budget_or_reference_another_source() -> None:
    first, second = _chunk(), _chunk("other")
    proposed = _profile(first)
    proposed.references[0].locator = second.locator

    profiles, gaps = grounded_profiles([first, second], [_record(first)], [
        proposed, _profile(second),
    ])

    assert len(profiles) == 2
    assert all(profile.origin == "mechanical" for profile in profiles)
    assert gaps
    assert "0 evidence records retained" in profiles[1].description


def test_empty_and_duplicate_profiles_do_not_invent_context() -> None:
    chunk = _chunk()
    profiles, gaps = grounded_profiles([chunk], [_record(chunk)], [])
    assert not gaps
    assert profiles[0].origin == "mechanical"
    assert not profiles[0].context

    profiles, gaps = grounded_profiles(
        [chunk], [_record(chunk)], [_profile(chunk), _profile(chunk)],
    )
    assert len(profiles) == 1
    assert gaps


@pytest.mark.parametrize(
    ("excerpt", "expected"),
    [("hourly. Values", True), ("hourly.\nValues", True), ("", False),
     ("  ", False), ("hourly... estimates", False), ("Hourly", False)],
)
def test_reference_matching_normalizes_only_whitespace(excerpt: str, expected: bool) -> None:
    assert excerpt_is_supported(excerpt, _chunk().content) == expected


def test_source_context_is_bounded() -> None:
    with pytest.raises(ValidationError):
        SourceContext(note="A note without support", references=[])
    with pytest.raises(ValidationError):
        SourceProfile(source_id="source", title="Source", description="x" * 401)


def test_saved_state_without_optional_source_profiles_loads() -> None:
    state = ResearchState.model_validate({
        "run_id": "old-run",
        "request": {"question": "What is supported?", "sources": [
            {"source_id": "records", "content": "Original source."},
        ]},
        "evidence_extractor": {"records": [_record(_chunk()).model_dump()], "gaps": []},
        "analysis": {"claims": [{
            "claim_id": "claim", "statement": "Original claim", "evidence_ids": ["E001"],
            "confidence": 0.8, "methodology": "original method",
        }]},
    })
    assert state.evidence_extractor is not None
    assert state.evidence_extractor.source_profiles == []
    assert state.analysis is not None
    assert state.analysis.claims[0].methodology == "original method"


class DescribingRuntime(DeterministicRuntime):
    def __init__(self) -> None:
        self.invocations: list[AgentRole] = []
        self.payloads: list[BaseModel] = []

    def invoke[OutputT: BaseModel](
        self, role: AgentRole, payload: BaseModel, output_type: type[OutputT], system_prompt: str,
    ) -> RuntimeResult[OutputT]:
        self.invocations.append(role)
        self.payloads.append(payload.model_copy(deep=True))
        result = super().invoke(role, payload, output_type, system_prompt)
        if role is AgentRole.EVIDENCE_EXTRACTOR:
            extraction = cast(EvidenceExtractorResult, result.output)
            chunk = cast(EvidenceExtractorInput, payload).chunks[0]
            extraction.source_profiles = [_profile(chunk)]
            extraction.records[0].evidence_id = "model-chosen-id"
            extraction.records[0].metadata = {"made-up": "context"}
            extraction.records[0].methodology = "Invented method"
        return result


@pytest.mark.asyncio
async def test_description_and_evidence_share_one_invocation_and_code_assigns_ids() -> None:
    runtime = DescribingRuntime()
    request = RunRequest(question="How were measurements collected?", sources=[{
        "source_id": "records", "content": _chunk().content,
    }])
    result = await EvidenceExtractorAgent(runtime, SourceLoader()).extract(request, Path.cwd())

    assert runtime.invocations == [AgentRole.EVIDENCE_EXTRACTOR]
    assert result.output.records[0].evidence_id == "E001"
    assert result.output.records[0].metadata == {}
    assert result.output.records[0].methodology == "unspecified"
    assert result.output.source_profiles[0].origin == "extracted"


def test_invented_evidence_excerpt_is_replaced_with_original_text() -> None:
    chunk = _chunk()
    record = _record(chunk)
    record.excerpt = "These measurements prove that risk is low."
    result = EvidenceExtractorAgent._ensure_chunk_coverage(
        EvidenceExtractorInput(question="What is supported?", chunks=[chunk], max_evidence=1),
        EvidenceExtractorResult(records=[record], source_profiles=[_profile(chunk)]),
    )

    assert result.records[0].evidence_id == "E001"
    assert excerpt_is_supported(result.records[0].excerpt, chunk.content)
    assert any("ungrounded excerpt" in gap for gap in result.gaps)


class RetryingDescriptionRuntime(DescribingRuntime):
    def invoke[OutputT: BaseModel](
        self, role: AgentRole, payload: BaseModel, output_type: type[OutputT], system_prompt: str,
    ) -> RuntimeResult[OutputT]:
        result = super().invoke(role, payload, output_type, system_prompt)
        if role is AgentRole.EVIDENCE_EXTRACTOR:
            cast(EvidenceExtractorResult, result.output).gaps = [
                "The observation period is not stated.",
            ]
        if role is AgentRole.VERIFIER and self.invocations.count(role) == 1:
            verification = cast(VerificationResult, result.output)
            verification.citation_closure_complete = False
            verification.retry_queries = ["Recheck the observation period."]
        return result


@pytest.mark.asyncio
@pytest.mark.parametrize("cycle_limit", [1, 2])
async def test_profiles_and_gaps_survive_retries_partial_synthesis_and_artifacts(
    tmp_path: Path, cycle_limit: int,
) -> None:
    runtime = RetryingDescriptionRuntime()
    orchestrator = EvidenceSynthesisOrchestrator(
        evidence_extractor=EvidenceExtractorAgent(runtime, SourceLoader()),
        analyst=AnalystAgent(runtime),
        verifier=VerifierAgent(runtime), synthesizer=SynthesizerAgent(runtime),
        policy=PipelinePolicy(max_verification_cycles=cycle_limit),
        artifact_store=FileArtifactStore(tmp_path),
    )
    request = RunRequest(question="How were measurements collected?", sources=[{
        "source_id": "records", "content": _chunk().content,
    }])

    state = await orchestrator.run(request)

    expected_status = TerminalStatus.PARTIAL if cycle_limit == 1 else TerminalStatus.COMPLETED
    assert state.status is expected_status
    assert runtime.invocations.count(AgentRole.EVIDENCE_EXTRACTOR) == 1
    assert runtime.invocations.count(AgentRole.ANALYST) == cycle_limit
    assert state.evidence_extractor is not None
    expected_profiles = state.evidence_extractor.source_profiles
    assert expected_profiles[0].origin == "extracted"
    for payload in runtime.payloads:
        if isinstance(payload, (AnalysisInput, VerificationInput, SynthesisInput)):
            assert payload.source_profiles == expected_profiles
        if isinstance(payload, AnalysisInput):
            assert payload.evidence_gaps == state.evidence_extractor.gaps
        if isinstance(payload, VerificationInput):
            assert set(state.evidence_extractor.gaps).issubset(payload.analysis.evidence_gaps)
        if isinstance(payload, SynthesisInput):
            assert set(state.evidence_extractor.gaps).issubset(payload.verification.evidence_gaps)
            assert payload.partial is (cycle_limit == 1)
    saved = RunSummary.model_validate_json(
        (tmp_path / state.run_id / "state.json").read_text(encoding="utf-8"),
    )
    assert saved.synthesis == state.synthesis
    assert "evidence_extractor" not in saved.model_dump()
    assert saved.request_fingerprint == request.fingerprint()
    assert [metric.attempts for metric in state.metrics] == [1] * len(state.metrics)
    assert (tmp_path / state.run_id / "evidence_extractor.json").exists()
    assert (tmp_path / state.run_id / "verification-1.json").exists()
    if cycle_limit == 2:
        assert (tmp_path / state.run_id / "analysis-retry-1.json").exists()
        assert (tmp_path / state.run_id / "verification-2.json").exists()


def test_revision_feedback_preserves_the_serialized_contract() -> None:
    review = VerificationResult(claims=[], citation_closure_complete=False, retry_queries=[
        "Support the observation period with an excerpt.",
    ])

    assert review.revision_feedback == review.retry_queries
    assert "revision_feedback" not in review.model_dump()
    assert VerificationResult.model_validate_json(review.model_dump_json()) == review


def test_merge_evidence_gaps_preserves_order_without_duplicates() -> None:
    assert merge_evidence_gaps(["first", "second", "first"], [], ["second", "third", ""]) == [
        "first", "second", "third", "",
    ]


def test_constructor_requires_evidence_extractor() -> None:
    runtime = DeterministicRuntime()
    agents = {
        "analyst": AnalystAgent(runtime), "verifier": VerifierAgent(runtime),
        "synthesizer": SynthesizerAgent(runtime),
    }
    with pytest.raises(TypeError, match="evidence_extractor"):
        EvidenceSynthesisOrchestrator(**agents)
