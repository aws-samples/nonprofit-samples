# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from typing import TypeVar, cast

from pydantic import BaseModel

from evidence_synthesis.models import (
    AgentRole,
    AnalysisInput,
    AnalysisResult,
    Citation,
    Claim,
    Contradiction,
    EvidenceExtractorInput,
    EvidenceExtractorResult,
    EvidenceRecord,
    SourceChunk,
    SynthesisInput,
    SynthesisResult,
    VerificationInput,
    VerificationResult,
    VerificationStatus,
    VerifiedClaim,
)
from evidence_synthesis.runtime import RuntimeResult, timed_metrics
from evidence_synthesis.source_profiles import normalized_text

OutputT = TypeVar("OutputT", bound=BaseModel)

_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "have",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "was",
    "were",
    "what",
    "with",
}


class DeterministicRuntime:
    model_id = "deterministic-local"

    def invoke(
        self,
        role: AgentRole,
        payload: BaseModel,
        output_type: type[OutputT],
        system_prompt: str,
    ) -> RuntimeResult[OutputT]:
        del system_prompt
        started_at = time.perf_counter()
        output: BaseModel
        if role is AgentRole.EVIDENCE_EXTRACTOR:
            output = self._extract(cast(EvidenceExtractorInput, payload))
        elif role is AgentRole.ANALYST:
            output = self._analyze(cast(AnalysisInput, payload))
        elif role is AgentRole.VERIFIER:
            output = self._verify(cast(VerificationInput, payload))
        elif role is AgentRole.SYNTHESIZER:
            output = self._synthesize(cast(SynthesisInput, payload))
        else:
            raise ValueError(f"Unsupported role: {role}")
        validated = output_type.model_validate(output)
        approximate_input = len(payload.model_dump_json()) // 4
        approximate_output = len(validated.model_dump_json()) // 4
        return RuntimeResult(
            output=validated,
            metrics=timed_metrics(
                role=role,
                model_id=self.model_id,
                started_at=started_at,
                input_tokens=approximate_input,
                output_tokens=approximate_output,
            ),
        )

    def _extract(self, payload: EvidenceExtractorInput) -> EvidenceExtractorResult:
        question_terms = self._terms(payload.question)
        scored: list[tuple[float, SourceChunk]] = []
        for chunk in payload.chunks:
            content_terms = self._terms(chunk.content)
            overlap = len(question_terms & content_terms)
            score = min(1.0, 0.2 + overlap / max(1, len(question_terms)))
            scored.append((score, chunk))
        scored.sort(key=lambda item: item[0], reverse=True)

        records = [
            EvidenceRecord(
                evidence_id=f"E{index:03}",
                source_id=chunk.source_id,
                title=chunk.title,
                excerpt=normalized_text(chunk.content)[:1000],
                locator=chunk.locator,
                methodology=chunk.methodology,
                relevance=round(score, 3),
                metadata=chunk.metadata,
            )
            for index, (score, chunk) in enumerate(
                scored[: payload.max_evidence], start=1
            )
        ]
        gaps = [] if records else ["No readable evidence was found in the supplied sources."]
        return EvidenceExtractorResult(records=records, gaps=gaps)

    def _analyze(self, payload: AnalysisInput) -> AnalysisResult:
        claims = []
        for index, record in enumerate(payload.evidence, start=1):
            statement = self._claim_text(record.excerpt)
            claims.append(
                Claim(
                    claim_id=f"C{index:03}",
                    statement=statement,
                    evidence_ids=[record.evidence_id],
                    confidence=round(min(0.95, 0.55 + record.relevance * 0.4), 3),
                    methodology=record.methodology,
                    topic=str(record.metadata.get("topic", record.source_id)),
                    stance=self._optional_string(record.metadata.get("stance")),
                )
            )
        return AnalysisResult(
            claims=claims,
            evidence_gaps=list(dict.fromkeys([
                *payload.evidence_gaps, *payload.verification_feedback,
            ])),
        )

    def _verify(self, payload: VerificationInput) -> VerificationResult:
        evidence_by_id = {record.evidence_id: record for record in payload.evidence}
        verified_claims = []
        gaps = list(payload.analysis.evidence_gaps)
        for claim in payload.analysis.claims:
            cited = [evidence_by_id[item] for item in claim.evidence_ids if item in evidence_by_id]
            source_count = len({item.source_id for item in cited})
            if len(cited) != len(claim.evidence_ids):
                status = VerificationStatus.REJECTED
                reason = "One or more cited evidence IDs do not exist."
                gaps.append(f"Repair citations for claim {claim.claim_id}.")
            elif claim.confidence < 0.6:
                status = VerificationStatus.QUALIFIED
                reason = "The claim is cited but confidence is below the verification threshold."
            else:
                status = VerificationStatus.VERIFIED
                reason = "The claim is directly supported by the cited evidence."
            verified_claims.append(
                VerifiedClaim(
                    claim=claim,
                    status=status,
                    reason=reason,
                    independent_source_count=source_count,
                )
            )

        contradictions = self._find_contradictions(verified_claims)
        closure = all(
            item.status is not VerificationStatus.REJECTED for item in verified_claims
        )
        retry_queries = [] if closure else gaps[:2]
        return VerificationResult(
            claims=verified_claims,
            contradictions=contradictions,
            evidence_gaps=gaps,
            retry_queries=retry_queries,
            citation_closure_complete=closure,
        )

    def _find_contradictions(
        self, claims: list[VerifiedClaim]
    ) -> list[Contradiction]:
        by_topic: dict[str, list[VerifiedClaim]] = defaultdict(list)
        for item in claims:
            if item.status is not VerificationStatus.REJECTED:
                by_topic[item.claim.topic].append(item)

        contradictions = []
        for topic, items in by_topic.items():
            stances = {item.claim.stance for item in items if item.claim.stance}
            if len(stances) < 2:
                continue
            methodologies = sorted({item.claim.methodology for item in items})
            contradictions.append(
                Contradiction(
                    topic=topic,
                    claim_ids=[item.claim.claim_id for item in items],
                    explanation=(
                        f"Sources addressing '{topic}' express different stances: "
                        f"{', '.join(sorted(stances))}."
                    ),
                    methodology_note=(
                        "The records use different methods or measures: "
                        + ", ".join(methodologies)
                        + "."
                    ),
                    resolution=(
                        "Keep the findings separate, report both, and avoid treating one "
                        "measure as a substitute for another."
                    ),
                )
            )
        return contradictions

    def _synthesize(self, payload: SynthesisInput) -> SynthesisResult:
        evidence_by_id = {record.evidence_id: record for record in payload.evidence}
        usable = [
            item
            for item in payload.verification.claims
            if item.status is not VerificationStatus.REJECTED
        ]
        lines = [f"Evidence synthesis for: {payload.question}", ""]
        if usable:
            lines.append("Supported findings:")
            for item in usable:
                marker = " ".join(f"[{evidence_id}]" for evidence_id in item.claim.evidence_ids)
                qualifier = (
                    "Qualified" if item.status is VerificationStatus.QUALIFIED else "Verified"
                )
                lines.append(f"- {qualifier}: {item.claim.statement} {marker}")
        else:
            lines.append("No claims survived verification.")

        if payload.verification.contradictions:
            lines.extend(["", "Unresolved methodological conflicts:"])
            for conflict in payload.verification.contradictions:
                lines.append(
                    f"- {conflict.explanation} {conflict.methodology_note} "
                    f"{conflict.resolution}"
                )

        limitations = list(dict.fromkeys(payload.verification.evidence_gaps))
        if payload.partial and payload.terminal_reason:
            limitations.append(f"Pipeline ended early: {payload.terminal_reason}.")
        if limitations:
            lines.extend(["", "Limitations:"])
            lines.extend(f"- {item}" for item in limitations)

        evidence_ids = list(
            dict.fromkeys(
                evidence_id
                for item in usable
                for evidence_id in item.claim.evidence_ids
                if evidence_id in evidence_by_id
            )
        )
        citations = [
            Citation(
                evidence_id=evidence_id,
                source_id=evidence_by_id[evidence_id].source_id,
                title=evidence_by_id[evidence_id].title,
                locator=evidence_by_id[evidence_id].locator,
            )
            for evidence_id in evidence_ids
        ]
        return SynthesisResult(
            answer="\n".join(lines),
            citations=citations,
            limitations=limitations,
            partial=payload.partial,
            terminal_reason=payload.terminal_reason,
        )

    @staticmethod
    def _terms(value: str) -> set[str]:
        return {
            term
            for term in re.findall(r"[a-z0-9]+", value.lower())
            if term not in _STOP_WORDS and len(term) > 1
        }

    @staticmethod
    def _compact(value: str, limit: int) -> str:
        compact = re.sub(r"\s+", " ", value).strip()
        return compact if len(compact) <= limit else compact[: limit - 3] + "..."

    @classmethod
    def _claim_text(cls, excerpt: str) -> str:
        if excerpt.startswith("{"):
            try:
                value = json.loads(excerpt)
            except json.JSONDecodeError:
                return cls._compact(excerpt, 400)
            return "; ".join(f"{key}: {item}" for key, item in value.items())
        sentence = re.split(r"(?<=[.!?])\s+", excerpt, maxsplit=1)[0]
        return cls._compact(sentence, 400)

    @staticmethod
    def _optional_string(value: object) -> str | None:
        return str(value) if value not in (None, "") else None
