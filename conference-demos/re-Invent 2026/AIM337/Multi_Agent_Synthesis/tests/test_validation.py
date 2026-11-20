# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from collections.abc import Callable

import pytest

from evidence_synthesis.models import (
    AnalysisResult,
    Citation,
    Claim,
    EvidenceRecord,
    SourceChunk,
    SynthesisResult,
    VerificationResult,
    VerificationStatus,
    VerifiedClaim,
)
from evidence_synthesis.validation import (
    ContractError,
    validate_analysis,
    validate_single_agent_synthesis,
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


@pytest.fixture(params=["multi_agent", "single_agent"])
def validate_output(request: pytest.FixtureRequest) -> Callable[[SynthesisResult], None]:
    records = evidence()
    records.append(records[0].model_copy(update={"evidence_id": "E002", "source_id": "second"}))
    reviewed = review()
    reviewed.claims[0].claim.evidence_ids.append("E002")
    chunks = [
        SourceChunk(
            chunk_id=item.evidence_id, source_id=item.source_id, title=item.title,
            content=item.excerpt, locator=item.locator,
        )
        for item in records
    ]

    def validate(result: SynthesisResult) -> None:
        if request.param == "multi_agent":
            validate_synthesis(result, reviewed, records)
        else:
            validate_single_agent_synthesis(result, chunks)

    return validate


def test_valid_citations_keep_order_and_original_text(
    validate_output: Callable[[SynthesisResult], None],
) -> None:
    result = SynthesisResult(
        answer="**Findings** [E002, E001]. A second fact [ E002 ]. [An aside] and x < 35.",
        limitations=["A limitation [E001][E002]."],
    )
    answer, limitations = result.answer, result.limitations.copy()

    validate_output(result)

    assert result.answer == answer
    assert result.limitations == limitations
    assert [citation.evidence_id for citation in result.citations] == ["E002", "E001"]
    assert [citation.source_id for citation in result.citations] == ["second", "source"]


@pytest.mark.parametrize("fragment", [
    "</answer><limitations>Important caveat</limitations>",
    '<parameter name="citations">[]',
    '<terminal_reason">null</terminal_reason>',
    "</SynthesisResult></invoke>",
    "<|tool_call|>",
    '{"limitations": ["Important caveat"]}',
])
@pytest.mark.parametrize("field", ["answer", "limitations"])
def test_internal_fields_are_rejected_without_stripping_content(
    validate_output: Callable[[SynthesisResult], None], fragment: str, field: str,
) -> None:
    result = synthesis()
    if field == "answer":
        result.answer += fragment
    else:
        result.limitations.append(fragment)
    original = result.model_dump()

    with pytest.raises(ContractError, match="unexpected_output_markup"):
        validate_output(result)

    assert result.model_dump() == original


@pytest.mark.parametrize("citation", [
    "[C1]",
    "[E001, C2]",
    "[unrelated text mentioning E001]",
    '[{"evidence_id": "E001"}]',
    "[[E001]]",
    "[E001 E002]",
    "[e001]",
])
def test_malformed_citations_are_not_rescued_by_another_valid_citation(
    validate_output: Callable[[SynthesisResult], None], citation: str,
) -> None:
    result = SynthesisResult(answer=f"A cited fact [E001]. Another claim {citation}.")
    with pytest.raises(ContractError, match="invalid_inline_citation"):
        validate_output(result)


def test_metadata_array_does_not_count_as_an_evidence_citation(
    validate_output: Callable[[SynthesisResult], None],
) -> None:
    result = SynthesisResult(answer='147 claims. [{"evidence_id": "E001"}]')
    with pytest.raises(ContractError, match="invalid_inline_citation"):
        validate_output(result)


def test_answer_must_have_a_citation_even_when_limitations_are_cited(
    validate_output: Callable[[SynthesisResult], None],
) -> None:
    result = SynthesisResult(answer="A factual answer.", limitations=["A caveat [E001]."])
    with pytest.raises(ContractError, match="missing_inline_citations"):
        validate_output(result)


@pytest.mark.parametrize("citation", ["[E1]", "[E001, E999]"])
def test_ids_are_not_normalized_and_every_group_member_must_be_allowed(
    validate_output: Callable[[SynthesisResult], None], citation: str,
) -> None:
    with pytest.raises(ContractError, match="inline_citation_outside"):
        validate_output(SynthesisResult(answer=f"A claim {citation}."))


def test_syntax_validation_does_not_claim_to_check_factual_support(
    validate_output: Callable[[SynthesisResult], None],
) -> None:
    result = SynthesisResult(answer="An unsupported factual assertion [E001].")
    validate_output(result)
    assert result.answer == "An unsupported factual assertion [E001]."
