# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
"""Check citation relationships separately from the model's semantic review."""

from __future__ import annotations

import re

from evidence_synthesis.models import (
    AnalysisResult,
    Citation,
    EvidenceRecord,
    SynthesisResult,
    VerificationResult,
    VerificationStatus,
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
    inline_ids = list(dict.fromkeys(
        eid
        for bracket in re.findall(
            r"\[([^\]]+)\]", "\n".join([result.answer, *result.limitations]),
        )
        for eid in re.findall(r"\bE[0-9]+\b", bracket)
    ))
    if not set(inline_ids).issubset(allowed_ids):
        raise ContractError("inline_citation_outside_reviewed_evidence")
    if allowed_ids and not inline_ids:
        raise ContractError("missing_inline_citations")
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
