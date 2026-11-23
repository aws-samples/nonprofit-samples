# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
"""Check citation relationships separately from the model's semantic review."""

from __future__ import annotations

import re

from evidence_synthesis.models import (
    AnalysisResult,
    Citation,
    EvidenceRecord,
    SourceChunk,
    SynthesisResult,
    VerificationResult,
    VerificationStatus,
)

_RESULT_FIELDS = "answer|citations|limitations|partial|terminal_reason|safety_action"
_OUTPUT_MARKUP = re.compile(
    rf"<\s*/?\s*(?:{_RESULT_FIELDS}|SynthesisResult|parameter|invoke|function_calls)\b"
    rf"""|["'](?:{_RESULT_FIELDS})["']\s*:"""
    r"|<\|[^>]+\|>",
    re.IGNORECASE,
)


class ContractError(ValueError):
    pass


def validate_analysis(result: AnalysisResult, evidence: list[EvidenceRecord]) -> None:
    evidence_ids = {item.evidence_id for item in evidence}
    claim_ids = [claim.claim_id for claim in result.claims]
    if len(set(claim_ids)) != len(claim_ids):
        raise ContractError("duplicate_claim_ids")
    for claim in result.claims:
        if not set(claim.evidence_ids).issubset(evidence_ids):
            raise ContractError("unknown_evidence_id")


def validate_verification(
    result: VerificationResult, analysis: AnalysisResult, evidence: list[EvidenceRecord],
) -> None:
    original = {claim.claim_id: claim for claim in analysis.claims}
    reviewed_ids = [item.claim.claim_id for item in result.claims]
    if len(set(reviewed_ids)) != len(reviewed_ids) or set(reviewed_ids) != set(original):
        raise ContractError("review_claim_coverage_mismatch")
    sources = {item.evidence_id: item.source_id for item in evidence}
    for item in result.claims:
        if item.claim != original[item.claim.claim_id]:
            raise ContractError("verifier_changed_claim")
        source_count = len({sources[eid] for eid in item.claim.evidence_ids})
        if item.independent_source_count > source_count:
            raise ContractError("overstated_source_count")
    for contradiction in result.contradictions:
        if not set(contradiction.claim_ids).issubset(original):
            raise ContractError("unknown_contradiction_claim")


def validate_synthesis(
    result: SynthesisResult, review: VerificationResult, evidence: list[EvidenceRecord],
) -> None:
    allowed_ids = {
        eid for item in review.claims if item.status is not VerificationStatus.REJECTED
        for eid in item.claim.evidence_ids
    }
    if not allowed_ids:
        raise ContractError("no_reviewed_claims_for_generated_synthesis")
    evidence_by_id = {item.evidence_id: item for item in evidence}
    inline_ids = _inline_ids(result)
    if not set(inline_ids).issubset(allowed_ids):
        raise ContractError("inline_citation_outside_reviewed_evidence")
    # Source labels and locations come from retained evidence, not generated metadata.
    result.citations = [
        Citation(
            evidence_id=eid,
            source_id=evidence_by_id[eid].source_id,
            title=evidence_by_id[eid].title,
            locator=evidence_by_id[eid].locator,
        )
        for eid in inline_ids
    ]


def validate_single_agent_synthesis(result: SynthesisResult, chunks: list[SourceChunk]) -> None:
    chunks_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    inline_ids = _inline_ids(result)
    if not set(inline_ids).issubset(chunks_by_id):
        raise ContractError("inline_citation_outside_supplied_sources")
    # These references identify supplied text; they do not imply a separate semantic review.
    result.citations = [
        Citation(
            evidence_id=chunk_id,
            source_id=chunks_by_id[chunk_id].source_id,
            title=chunks_by_id[chunk_id].title,
            locator=chunks_by_id[chunk_id].locator,
        )
        for chunk_id in inline_ids
    ]


def validate_synthesis_text(answer: str, limitations: list[str]) -> None:
    """Check prose format and citation syntax; reviewed-source checks follow generation."""
    _inline_ids(SynthesisResult(answer=answer, limitations=limitations))


def _inline_ids(result: SynthesisResult) -> list[str]:
    texts = [result.answer, *result.limitations]
    for text in texts:
        # Reject misplaced fields; stripping them could discard important caveats.
        if _OUTPUT_MARKUP.search(text):
            raise ContractError("unexpected_output_markup")

    inline_ids = _citation_ids(result.answer)
    if not inline_ids:
        raise ContractError("missing_inline_citations")
    for limitation in result.limitations:
        inline_ids.extend(_citation_ids(limitation))
    return list(dict.fromkeys(inline_ids))


def _citation_ids(text: str) -> list[str]:
    inline_ids = []
    for bracket in re.findall(r"\[([^\]]*)\]", text):
        # The whole bracket must be an ID or comma-separated IDs, not metadata.
        if re.fullmatch(r"\s*E[0-9]+(?:\s*,\s*E[0-9]+)*\s*", bracket):
            inline_ids.extend(re.findall(r"E[0-9]+", bracket))
        elif re.search(r"\b[EC][0-9]+\b", bracket, re.IGNORECASE):
            raise ContractError("invalid_inline_citation")
    return inline_ids
