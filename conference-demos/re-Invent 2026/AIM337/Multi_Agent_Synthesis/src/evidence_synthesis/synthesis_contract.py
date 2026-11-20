# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
"""Reviewed content for generation; result metadata remains with the application."""

from __future__ import annotations

import re
from typing import Self

from pydantic import Field, model_validator

from evidence_synthesis.models import (
    Contradiction,
    StrictModel,
    SynthesisInput,
    VerificationStatus,
)
from evidence_synthesis.validation import ContractError, validate_synthesis_text


class ReviewedClaim(StrictModel):
    claim_id: str
    statement: str
    evidence_ids: list[str]
    methodology: str
    status: VerificationStatus
    reason: str


class ReviewedContent(StrictModel):
    question: str
    claims: list[ReviewedClaim]
    contradictions: list[Contradiction]
    evidence_gaps: list[str]


def reviewed_content(payload: SynthesisInput) -> ReviewedContent:
    # Raw excerpts and profile hints can contain facts that have not passed review.
    return ReviewedContent(
        question=payload.question,
        claims=[
            ReviewedClaim(
                claim_id=item.claim.claim_id,
                statement=item.claim.statement,
                evidence_ids=item.claim.evidence_ids,
                methodology=item.claim.methodology,
                status=item.status,
                reason=item.reason,
            )
            for item in payload.verification.claims
        ],
        contradictions=payload.verification.contradictions,
        evidence_gaps=payload.verification.evidence_gaps,
    )


class SynthesisDraft(StrictModel):
    answer: str = Field(
        description="Readable Markdown answer with exact evidence citations, e.g. [E001].",
    )
    limitations: list[str] = Field(
        description="Separate limitation strings; use an empty list when none apply.",
    )

    @model_validator(mode="after")
    def check_prose(self) -> Self:
        # Strands returns validation errors to the model within its existing turn limit.
        if any(
            re.search(r"<\s*/?\s*SynthesisDraft\b", text, re.IGNORECASE)
            for text in [self.answer, *self.limitations]
        ):
            raise ValueError("Use plain prose; remove SynthesisDraft XML tags.")
        try:
            validate_synthesis_text(self.answer, self.limitations)
        except ContractError as error:
            corrections = {
                "unexpected_output_markup": (
                    "Remove XML tags, serialized fields, and tool syntax from the strings. "
                    "Return answer and limitations as separate schema fields."
                ),
                "missing_inline_citations": "Cite factual statements in answer using [E001].",
                "invalid_inline_citation": (
                    "Use exact evidence citations such as [E001] or [E001, E002], "
                    "never claim IDs or other bracket formats."
                ),
            }
            raise ValueError(corrections.get(str(error), str(error))) from error
        return self
