# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
import pytest
from pydantic import BaseModel, ValidationError

from evidence_synthesis.agents.synthesizer import SynthesizerAgent
from evidence_synthesis.models import (
    AgentRole,
    Claim,
    Contradiction,
    EvidenceRecord,
    InvocationMetrics,
    SourceProfile,
    SynthesisInput,
    VerificationResult,
    VerificationStatus,
    VerifiedClaim,
)
from evidence_synthesis.runtime import RuntimeResult
from evidence_synthesis.synthesis_contract import (
    ReviewedContent,
    SynthesisDraft,
    reviewed_content,
)
from evidence_synthesis.validation import ContractError, validate_synthesis


def supplied_input() -> SynthesisInput:
    return SynthesisInput(
        question="What household count is supported?",
        evidence=[
            EvidenceRecord(
                evidence_id="E001", source_id="report-a", title="Report A",
                excerpt="Report A counted 120 households. UNREVIEWED_DETAIL.",
                locator="report-a#1", relevance=1, metadata={"UNREVIEWED_METADATA": "value"},
            ),
        ],
        source_profiles=[
            SourceProfile(
                source_id="report-a", title="Report A", description="UNREVIEWED_PROFILE",
            ),
        ],
        verification=VerificationResult(
            claims=[
                VerifiedClaim(
                    claim=Claim(
                        claim_id="C1", statement="Report A counted 120 households.",
                        evidence_ids=["E001"], confidence=0.5, methodology="unspecified",
                    ),
                    status=VerificationStatus.QUALIFIED,
                    reason="This is a report-specific count, not a reconciled total.",
                    independent_source_count=1,
                ),
                VerifiedClaim(
                    claim=Claim(
                        claim_id="C2", statement="The confirmed total is 120.",
                        evidence_ids=["E001"], confidence=0.5,
                    ),
                    status=VerificationStatus.REJECTED,
                    reason="A reconciled count is not supplied.",
                    independent_source_count=1,
                ),
            ],
            contradictions=[Contradiction(
                topic="count", claim_ids=["C1", "C2"],
                explanation="Reported and confirmed counts are not interchangeable.",
                methodology_note="Report A counts deduplicated households.",
                resolution="Retain report attribution.",
            )],
            evidence_gaps=["A reconciled register is not supplied."],
            citation_closure_complete=True,
        ),
    )


def test_generation_receives_reviewed_content_without_raw_details() -> None:
    payload = supplied_input()
    original = payload.model_dump()
    packet = reviewed_content(payload)

    assert packet.question == payload.question
    assert packet.claims[0].reason == payload.verification.claims[0].reason
    assert packet.claims[1].status is VerificationStatus.REJECTED
    assert packet.contradictions[0].methodology_note == (
        "Report A counts deduplicated households."
    )
    assert packet.evidence_gaps == payload.verification.evidence_gaps
    assert "UNREVIEWED" not in packet.model_dump_json()
    assert "confidence" not in packet.model_dump_json()
    assert payload.model_dump() == original


@pytest.mark.parametrize("draft", [
    {"answer": "Report A counted 120 households [E001]."},
    {"answer": "Report A counted 120 households [E001].", "limitations": "A gap."},
    {"answer": "An answer [E001].</answer>", "limitations": []},
    {"answer": "An answer [E001].", "limitations": ["</SynthesisDraft>"]},
    {"answer": "An answer [E001].", "limitations": ['"safety_action": "NONE"']},
    {"answer": "An answer [C1].", "limitations": []},
    {"answer": "An answer.", "limitations": ["A gap [E001]."]},
    {"answer": "An answer [E001].", "limitations": [], "partial": False},
])
def test_draft_rejects_malformed_fields_and_markup(draft: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        SynthesisDraft.model_validate(draft)


class RecordingRuntime:
    def __init__(self, evidence_id: str = "E001") -> None:
        self.payload: BaseModel | None = None
        self.evidence_id = evidence_id
        self.metrics = InvocationMetrics(
            role=AgentRole.SYNTHESIZER, model_id="test", latency_ms=1,
            input_tokens=10, output_tokens=5, total_tokens=15,
        )

    def invoke[OutputT: BaseModel](
        self, role: AgentRole, payload: BaseModel,
        output_type: type[OutputT], system_prompt: str,
    ) -> RuntimeResult[OutputT]:
        self.payload = payload
        return RuntimeResult(
            output=output_type.model_validate({
                "answer": f"Report A counted 120 households [{self.evidence_id}].",
                "limitations": ["A reconciled register is not supplied."],
            }),
            metrics=self.metrics,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("partial", [False, True])
async def test_agent_preserves_metadata_and_builds_citations_after_generation(
    partial: bool,
) -> None:
    payload = supplied_input()
    payload.partial = partial
    payload.terminal_reason = "verification_cycle_limit" if partial else None
    runtime = RecordingRuntime()

    result = await SynthesizerAgent(runtime).synthesize(payload)

    assert isinstance(runtime.payload, ReviewedContent)
    assert result.output.citations == []
    assert result.output.partial is partial
    assert result.output.terminal_reason == payload.terminal_reason
    assert result.output.limitations == ["A reconciled register is not supplied."]
    assert result.output.safety_action == "NONE"
    assert result.metrics is runtime.metrics
    validate_synthesis(result.output, payload.verification, payload.evidence)
    assert result.output.citations[0].source_id == "report-a"
    assert result.output.citations[0].locator == "report-a#1"


@pytest.mark.asyncio
async def test_valid_draft_cannot_bypass_reviewed_evidence_membership() -> None:
    payload = supplied_input()
    result = await SynthesizerAgent(RecordingRuntime("E999")).synthesize(payload)

    with pytest.raises(ContractError, match="inline_citation_outside_reviewed_evidence"):
        validate_synthesis(result.output, payload.verification, payload.evidence)
