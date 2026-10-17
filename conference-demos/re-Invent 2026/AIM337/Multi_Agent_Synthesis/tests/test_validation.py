# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
import pytest

from evidence_synthesis.models import (
    AnalysisResult,
    Citation,
    Claim,
    EvidenceRecord,
    SynthesisResult,
    VerificationResult,
    VerificationStatus,
    VerifiedClaim,
)
from evidence_synthesis.validation import (
    ContractError,
    validate_analysis,
    validate_synthesis,
    validate_verification,
)


def evidence() -> list[EvidenceRecord]:
    return [EvidenceRecord(
        evidence_id="E001", source_id="source", title="Original title",
        excerpt="The observation is supported.", locator="source.md#paragraph=1", relevance=1,
    )]


def analysis() -> AnalysisResult:
    return AnalysisResult(claims=[Claim(
        claim_id="C001", statement="Supported observation.", evidence_ids=["E001"], confidence=0.8,
    )])


def review() -> VerificationResult:
    return VerificationResult(
        claims=[VerifiedClaim(
            claim=analysis().claims[0], status="verified", reason="Supported.",
            independent_source_count=1,
        )],
        citation_closure_complete=True,
    )


def synthesis() -> SynthesisResult:
    return SynthesisResult(
        answer="Supported observation [E001].",
        citations=[Citation(
            evidence_id="E001", source_id="invented", title="Invented title", locator="invented",
        )],
    )


def test_unknown_evidence_and_duplicate_claim_ids_are_rejected() -> None:
    result = analysis()
    result.claims[0].evidence_ids = ["E999"]
    with pytest.raises(ContractError, match="unknown_evidence"):
        validate_analysis(result, evidence())
    result = analysis()
    result.claims.append(result.claims[0].model_copy())
    with pytest.raises(ContractError, match="duplicate_claim"):
        validate_analysis(result, evidence())


def test_review_cannot_replace_or_omit_the_analysts_claim() -> None:
    result = review()
    result.claims[0].claim.statement = "An invented replacement."
    with pytest.raises(ContractError, match="changed_claim"):
        validate_verification(result, analysis(), evidence())
    result.claims = []
    with pytest.raises(ContractError, match="coverage_mismatch"):
        validate_verification(result, analysis(), evidence())


def test_review_cannot_inflate_the_number_of_independent_sources() -> None:
    result = review()
    result.claims[0].independent_source_count = 2
    with pytest.raises(ContractError, match="overstated_source"):
        validate_verification(result, analysis(), evidence())


def test_citation_metadata_is_rebuilt_from_retained_evidence() -> None:
    result = synthesis()
    validate_synthesis(result, review(), evidence())
    assert result.citations[0].source_id == "source"
    assert result.citations[0].title == "Original title"
    assert result.citations[0].locator == "source.md#paragraph=1"


def test_valid_inline_ids_do_not_require_repeated_model_generated_metadata() -> None:
    result = synthesis()
    result.citations = []
    validate_synthesis(result, review(), evidence())
    assert [citation.evidence_id for citation in result.citations] == ["E001"]


def test_unknown_inline_citations_and_missing_citations_are_rejected() -> None:
    result = synthesis()
    result.answer = "Invented support [E999]."
    with pytest.raises(ContractError, match="inline_citation"):
        validate_synthesis(result, review(), evidence())
    result.answer = "No citations attached."
    with pytest.raises(ContractError, match="missing_inline"):
        validate_synthesis(result, review(), evidence())


def test_synthesis_cannot_cite_a_rejected_claim() -> None:
    rejected = review()
    rejected.claims[0].status = VerificationStatus.REJECTED
    with pytest.raises(ContractError, match="no_reviewed_claims"):
        validate_synthesis(synthesis(), rejected, evidence())
