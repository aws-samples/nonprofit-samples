# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class AgentRole(StrEnum):
    EVIDENCE_EXTRACTOR = "evidence_extractor"
    ANALYST = "analyst"
    VERIFIER = "verifier"
    SYNTHESIZER = "synthesizer"


class TerminalStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    FAILED = "failed"


class VerificationStatus(StrEnum):
    VERIFIED = "verified"
    QUALIFIED = "qualified"
    REJECTED = "rejected"


class SourceSpec(StrictModel):
    source_id: str = Field(min_length=1, max_length=128)
    uri: str | None = Field(default=None, max_length=2048)
    content: str | None = Field(default=None, max_length=1_000_000)
    title: str | None = Field(default=None, max_length=160)
    methodology: str = Field(default="unspecified", max_length=160)
    media_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def exactly_one_location(self) -> Self:
        if bool(self.uri) == bool(self.content):
            raise ValueError("Provide exactly one of uri or content")
        if len(json.dumps(self.metadata).encode()) > 4096:
            raise ValueError("Source metadata exceeds the 4 KiB limit")
        return self

# SHOW 3.1 : Pydantic Models
class RunRequest(StrictModel):
    question: str = Field(min_length=3, max_length=1000)
    sources: list[SourceSpec] = Field(min_length=1, max_length=20)
    max_evidence: int = Field(default=20, ge=1, le=100)
    # The caller can supply an ID to follow progress before the response arrives.
    run_id: str | None = Field(default=None, max_length=32, pattern=r"^[a-f0-9]{32}$")

    def fingerprint(self) -> str:
        encoded = json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()
        return hashlib.sha256(encoded).hexdigest()

    @model_validator(mode="after")
    def bounded_payload(self) -> Self:
        if len({source.source_id for source in self.sources}) != len(self.sources):
            raise ValueError("Source IDs must be unique within a request")
        if len(self.model_dump_json().encode()) > 4 * 1024 * 1024:
            raise ValueError("Request exceeds the 4 MiB payload limit")
        return self


class SourceChunk(StrictModel):
    chunk_id: str
    source_id: str
    title: str
    content: str
    locator: str
    methodology: str = "unspecified"
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceExtractorInput(StrictModel):
    question: str
    chunks: list[SourceChunk]
    max_evidence: int


# Keep each excerpt tied to its source and location.
class EvidenceRecord(StrictModel):
    evidence_id: str
    source_id: str
    title: str
    excerpt: str
    locator: str
    methodology: str = "unspecified"
    relevance: float = Field(ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceReference(StrictModel):
    locator: str = Field(min_length=1)
    excerpt: str = Field(min_length=1, max_length=1000)


class SourceContext(StrictModel):
    note: str = Field(min_length=1, max_length=300)
    references: list[SourceReference] = Field(min_length=1, max_length=3)


class SourceProfile(StrictModel):
    source_id: str
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=400)
    references: list[SourceReference] = Field(default_factory=list, max_length=3)
    context: list[SourceContext] = Field(default_factory=list, max_length=3)
    origin: Literal["extracted", "mechanical"] = "extracted"


class EvidenceExtractorResult(StrictModel):
    records: list[EvidenceRecord]
    source_profiles: list[SourceProfile] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class AnalysisInput(StrictModel):
    question: str
    evidence: list[EvidenceRecord]
    source_profiles: list[SourceProfile] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    verification_feedback: list[str] = Field(default_factory=list)


# SHOW 3.2 Proposed claims point back to the extracted evidence.
class Claim(StrictModel):
    claim_id: str
    statement: str
    evidence_ids: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    methodology: str = "unspecified"
    topic: str = "general"
    stance: str | None = None


class AnalysisResult(StrictModel):
    claims: list[Claim]
    evidence_gaps: list[str] = Field(default_factory=list)


class VerificationInput(StrictModel):
    question: str
    evidence: list[EvidenceRecord]
    analysis: AnalysisResult
    source_profiles: list[SourceProfile] = Field(default_factory=list)


class VerifiedClaim(StrictModel):
    claim: Claim
    status: VerificationStatus
    reason: str
    independent_source_count: int = Field(ge=0)


class Contradiction(StrictModel):
    topic: str
    claim_ids: list[str] = Field(min_length=2)
    explanation: str
    methodology_note: str = ""
    resolution: str


# SHOW 3.3 Verification and Citation Closure
class VerificationResult(StrictModel):
    claims: list[VerifiedClaim]
    contradictions: list[Contradiction] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    retry_queries: list[str] = Field(default_factory=list)
    # The Verifier reports whether the surviving claims have evidence support.
    citation_closure_complete: bool

    @property
    def revision_feedback(self) -> list[str]:
        # Revision feedback is stored as retry_queries in the saved JSON.
        return self.retry_queries


class SynthesisInput(StrictModel):
    question: str
    evidence: list[EvidenceRecord]
    verification: VerificationResult
    source_profiles: list[SourceProfile] = Field(default_factory=list)
    partial: bool = False
    terminal_reason: str | None = None


class Citation(StrictModel):
    evidence_id: str
    source_id: str
    title: str
    locator: str


class SynthesisResult(StrictModel):
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    partial: bool = False
    terminal_reason: str | None = None
    safety_action: str = "NONE"


class InvocationMetrics(StrictModel):
    role: AgentRole
    model_id: str
    latency_ms: float = Field(ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    attempts: int = Field(default=1, ge=1)


class StageEvent(StrictModel):
    stage: str
    status: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    detail: str | None = None


# SHOW 3.4 Collect the stage outputs and execution history for one run.
class ResearchState(StrictModel):
    run_id: str
    request: RunRequest
    artifact_uri: str | None = None
    evidence_extractor: EvidenceExtractorResult | None = None
    analysis: AnalysisResult | None = None
    verification: VerificationResult | None = None
    synthesis: SynthesisResult | None = None
    metrics: list[InvocationMetrics] = Field(default_factory=list)
    events: list[StageEvent] = Field(default_factory=list)
    verification_cycles: int = 0
    token_usage_complete: bool = True
    summary_only: bool = False
    status: TerminalStatus = TerminalStatus.FAILED
    terminal_reason: str | None = None

    @property
    def total_tokens(self) -> int:
        return sum(metric.total_tokens for metric in self.metrics)


class StageSummary(StrictModel):
    artifact_kind: Literal["stage_summary"] = "stage_summary"
    stage: str
    item_count: int = 0
    citation_closure_complete: bool | None = None


class RunSummary(StrictModel):
    """Saved recovery data excludes source text and intermediate model responses."""

    artifact_kind: Literal["run_summary"] = "run_summary"
    run_id: str
    request_fingerprint: str
    artifact_uri: str | None = None
    synthesis: SynthesisResult | None = None
    metrics: list[InvocationMetrics]
    events: list[StageEvent]
    verification_cycles: int
    token_usage_complete: bool = True
    status: TerminalStatus
    terminal_reason: str | None = None

    @classmethod
    def from_state(cls, state: ResearchState) -> RunSummary:
        return cls(
            run_id=state.run_id,
            request_fingerprint=state.request.fingerprint(),
            artifact_uri=state.artifact_uri,
            synthesis=(
                state.synthesis.model_copy(update={
                    "terminal_reason": (
                        state.synthesis.terminal_reason.split(":", 1)[0]
                        if state.synthesis.terminal_reason else None
                    ),
                }) if state.synthesis else None
            ),
            metrics=state.metrics,
            events=[event.model_copy(update={"detail": None}) for event in state.events],
            verification_cycles=state.verification_cycles,
            token_usage_complete=state.token_usage_complete,
            status=state.status,
            terminal_reason=(
                state.terminal_reason.split(":", 1)[0] if state.terminal_reason else None
            ),
        )

    def restore(self, request: RunRequest) -> ResearchState:
        if self.request_fingerprint != request.fingerprint():
            raise ValueError("The saved result belongs to a different request")
        return ResearchState(
            request=request,
            summary_only=True,
            **self.model_dump(exclude={"artifact_kind", "request_fingerprint"}),
        )
