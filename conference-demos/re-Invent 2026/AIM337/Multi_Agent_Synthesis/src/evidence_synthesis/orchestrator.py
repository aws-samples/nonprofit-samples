# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import ParamSpec, TypeVar

from pydantic import BaseModel

from evidence_synthesis.agents import (
    AnalystAgent,
    EvidenceExtractorAgent,
    SynthesizerAgent,
    VerifierAgent,
)
from evidence_synthesis.artifacts import ArtifactStore, NullArtifactStore
from evidence_synthesis.config import PipelinePolicy
from evidence_synthesis.models import (
    AnalysisInput,
    ResearchState,
    RunRequest,
    StageEvent,
    SynthesisInput,
    SynthesisResult,
    TerminalStatus,
    VerificationInput,
    VerificationResult,
    VerificationStatus,
)
from evidence_synthesis.runtime import RuntimeInvocationError, RuntimeResult
from evidence_synthesis.safety import LocalSafetyGate, SafetyGate, SafetyViolation
from evidence_synthesis.source_loader import SourceLimitError
from evidence_synthesis.source_profiles import merge_evidence_gaps
from evidence_synthesis.validation import (
    ContractError,
    validate_analysis,
    validate_synthesis,
    validate_verification,
)

OutputT = TypeVar("OutputT", bound=BaseModel)
StageArguments = ParamSpec("StageArguments")
_LOGGER = logging.getLogger(__name__)


class StageFailedError(RuntimeError):
    pass


class TokenBudgetExceeded(StageFailedError):
    pass


# Keep each run's state separate from the shared orchestrator.
@dataclass
class RunContext:
    state: ResearchState
    started_at: float


class EvidenceSynthesisOrchestrator:
    def __init__(
        self,
        *,
        evidence_extractor: EvidenceExtractorAgent,
        analyst: AnalystAgent,
        verifier: VerifierAgent,
        synthesizer: SynthesizerAgent,
        policy: PipelinePolicy | None = None,
        safety_gate: SafetyGate | None = None,
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        if policy is None:
            policy = PipelinePolicy()
        if safety_gate is None:
            safety_gate = LocalSafetyGate()
        if artifact_store is None:
            artifact_store = NullArtifactStore()

        self._evidence_extractor = evidence_extractor
        self._analyst = analyst
        self._verifier = verifier
        self._synthesizer = synthesizer
        self._policy = policy
        self._safety = safety_gate
        self._artifacts = artifact_store

    # SHOW 4 - Orchestrator core method
    async def run(
        self,
        request: RunRequest,
        *,
        base_dir: Path | None = None,
    ) -> ResearchState:
        # Reuse the caller's ID so the UI can find this run's logs and artifacts.
        state = ResearchState(run_id=request.run_id or uuid.uuid4().hex, request=request)
        started_at = time.monotonic()
        if base_dir is None:
            base_dir = Path.cwd()
        context = RunContext(state=state, started_at=started_at)
        # Claim the ID before any log or artifact can replace an earlier run.
        await asyncio.to_thread(self._artifacts.reserve_run, state.run_id)
        self._log(state.run_id, "pipeline", "started")

        blocked_state = await self._check_input_safety(context)
        if blocked_state is not None:
            return blocked_state

        try:
            await self._extract_evidence(context, base_dir)
            await self._analyze_evidence(context)
            review = await self._verify_claims(context)

            # Revisit the same evidence while citation support is incomplete.
            while not review.citation_closure_complete:
                if state.verification_cycles >= self._policy.max_verification_cycles:
                    return await self._partial(context, "verification_cycle_limit")

                await self._analyze_evidence(
                    context, revision_feedback=review.revision_feedback,
                )
                review = await self._verify_claims(context)

            await self._synthesize_answer(context)
            return await self._check_output_safety(context)
        except SafetyViolation as error:
            return self._blocked(
                state, error.reason, error.decision.text, error.decision.action,
            )
        except (StageFailedError, TimeoutError, SourceLimitError, ContractError) as error:
            return await self._partial(context, self._error_detail(error))

    async def _extract_evidence(self, context: RunContext, base_dir: Path) -> None:
        result = await self._stage(
            context, "evidence_extractor", self._evidence_extractor.extract,
            context.state.request, base_dir,
        )
        context.state.evidence_extractor = result.output
        self._record(context.state, "evidence_extractor", result)

    async def _analyze_evidence(
        self, context: RunContext, *, revision_feedback: list[str] | None = None,
    ) -> None:
        state = context.state
        assert state.evidence_extractor is not None
        stage_name = "analyst"
        artifact_name = "analysis"
        feedback = []
        if revision_feedback is not None:
            # Keep each revision as a separate artifact.
            stage_name = "analyst-retry"
            artifact_name = f"analysis-retry-{state.verification_cycles}"
            feedback = revision_feedback

        analysis_input = AnalysisInput(
            question=state.request.question,
            evidence=state.evidence_extractor.records,
            source_profiles=state.evidence_extractor.source_profiles,
            evidence_gaps=state.evidence_extractor.gaps,
            verification_feedback=feedback,
        )
        result = await self._stage(context, stage_name, self._analyst.analyze, analysis_input)
        analysis = result.output
        validate_analysis(analysis, state.evidence_extractor.records)
        # Carry forward gaps the Analyst may have omitted.
        analysis.evidence_gaps = merge_evidence_gaps(
            state.evidence_extractor.gaps, analysis.evidence_gaps,
        )
        state.analysis = analysis
        self._record(state, artifact_name, result)

    async def _verify_claims(self, context: RunContext) -> VerificationResult:
        state = context.state
        assert state.evidence_extractor is not None
        assert state.analysis is not None
        # Review the claims against the same evidence used for analysis.
        verification_input = VerificationInput(
            question=state.request.question,
            evidence=state.evidence_extractor.records,
            source_profiles=state.evidence_extractor.source_profiles,
            analysis=state.analysis,
        )
        result = await self._stage(context, "verifier", self._verifier.verify, verification_input)
        review = result.output
        validate_verification(review, state.analysis, state.evidence_extractor.records)
        review.evidence_gaps = merge_evidence_gaps(
            state.evidence_extractor.gaps, review.evidence_gaps,
        )
        state.verification = review
        state.verification_cycles += 1
        self._record(state, f"verification-{state.verification_cycles}", result)
        return review

    async def _synthesize_answer(self, context: RunContext) -> None:
        state = context.state
        assert state.evidence_extractor is not None
        assert state.verification is not None
        if not any(
            item.status is not VerificationStatus.REJECTED
            for item in state.verification.claims
        ):
            state.synthesis = SynthesisResult(answer="No claims survived verification.")
            return
        synthesis_input = SynthesisInput(
            question=state.request.question,
            evidence=state.evidence_extractor.records,
            source_profiles=state.evidence_extractor.source_profiles,
            verification=state.verification,
        )
        result = await self._stage(
            context, "synthesizer", self._synthesizer.synthesize, synthesis_input,
        )
        validate_synthesis(result.output, state.verification, state.evidence_extractor.records)
        result.output.partial = False
        result.output.terminal_reason = None
        state.synthesis = result.output
        self._record(state, "synthesis", result)

    async def _check_input_safety(self, context: RunContext) -> ResearchState | None:
        state = context.state
        try:
            decision = await self._safety.evaluate_input(state.request.question)
        except Exception:
            return self._blocked(
                state, "input_safety_unavailable",
                "The request could not be evaluated safely. Please try again.", "ERROR",
            )
        if decision.allowed:
            return None
        return self._blocked(
            state, "input_safety_policy", decision.text, decision.action,
        )

    async def _check_output_safety(
        self, context: RunContext, status: TerminalStatus = TerminalStatus.COMPLETED,
        reason: str | None = None,
    ) -> ResearchState:
        state = context.state
        assert state.synthesis is not None
        try:
            # Check all user-visible fields before saving either kind of answer.
            decision = await asyncio.wait_for(
                self._safety.evaluate_output(state.synthesis.model_dump_json()),
                timeout=max(0.001, self._remaining(context.started_at)),
            )
        except Exception:
            return self._blocked(
                state, "output_safety_unavailable",
                "The answer could not be evaluated safely. Please try again.", "ERROR",
            )
        state.synthesis.safety_action = decision.action
        if not decision.allowed:
            return self._blocked(
                state, "output_safety_policy", decision.text, decision.action,
            )
        return self._finish(state, status, reason)

    def _blocked(
        self, state: ResearchState, reason: str, answer: str, action: str,
    ) -> ResearchState:
        state.synthesis = SynthesisResult(
            answer=answer, partial=True, terminal_reason=reason, safety_action=action,
        )
        return self._finish(state, TerminalStatus.BLOCKED, reason)

    async def _stage(
        self,
        context: RunContext,
        name: str,
        operation: Callable[StageArguments, Awaitable[RuntimeResult[OutputT]]],
        *args: StageArguments.args,
        **kwargs: StageArguments.kwargs,
    ) -> RuntimeResult[OutputT]:
        state = context.state
        started_at = context.started_at
        last_error: Exception | None = None
        # A retry repeats the call without resetting the run's deadline.
        for attempt in range(1, self._policy.max_retries + 2):
            remaining = self._remaining(started_at)
            if remaining <= 0:
                raise TimeoutError("deadline_exceeded")
            if state.total_tokens >= self._policy.token_budget:
                raise TokenBudgetExceeded("token_budget_exceeded")
            state.events.append(StageEvent(stage=name, status="started"))
            self._log(state.run_id, name, "started", attempt=attempt)
            try:
                result = await asyncio.wait_for(operation(*args, **kwargs), timeout=remaining)
                result.metrics.attempts = attempt
                # Consumed tokens still count when the result exceeds the budget.
                state.metrics.append(result.metrics)
                if state.total_tokens > self._policy.token_budget:
                    raise TokenBudgetExceeded("token_budget_exceeded")
                state.events.append(StageEvent(stage=name, status="completed"))
                self._log(
                    state.run_id,
                    name,
                    "completed",
                    attempt=attempt,
                    latency_ms=round(result.metrics.latency_ms, 3),
                    total_tokens=result.metrics.total_tokens,
                )
                return result
            except (TokenBudgetExceeded, SafetyViolation, SourceLimitError):
                raise
            except TimeoutError as error:
                last_error = error
                detail = self._error_detail(error)
                state.events.append(
                    StageEvent(stage=name, status="timed_out", detail=detail)
                )
                self._log(
                    state.run_id,
                    name,
                    "timed_out",
                    attempt=attempt,
                    error_type=type(error).__name__,
                )
                state.token_usage_complete = False
                raise StageFailedError("model_timeout_usage_unavailable") from error
            except Exception as error:
                if isinstance(error, RuntimeInvocationError):
                    error.metrics.attempts = attempt
                    state.metrics.append(error.metrics)
                    state.token_usage_complete = error.usage_complete
                    if not error.usage_complete:
                        raise StageFailedError("model_usage_unavailable") from error
                    if state.total_tokens >= self._policy.token_budget:
                        raise TokenBudgetExceeded("token_budget_exceeded") from error
                last_error = error
                detail = self._error_detail(error)
                state.events.append(
                    StageEvent(stage=name, status="failed", detail=detail)
                )
                self._log(
                    state.run_id,
                    name,
                    "failed",
                    attempt=attempt,
                    error_type=type(error).__name__,
                )
            if attempt <= self._policy.max_retries:
                await asyncio.sleep(min(2 ** (attempt - 1), self._remaining(started_at)))
        assert last_error is not None
        raise StageFailedError(f"{name}_failed: {self._error_detail(last_error)}")

    def _record(
        self, state: ResearchState, stage: str, result: RuntimeResult[OutputT]
    ) -> None:
        # Persist progress counts; raw stage content stays in this run's memory.
        self._artifacts.save_stage(state.run_id, stage, result.output)

    async def _partial(
        self,
        context: RunContext,
        reason: str,
    ) -> ResearchState:
        state = context.state
        # Try one final synthesis from the review we already have.
        if (
            state.verification is not None and state.evidence_extractor is not None
            and any(
                item.status is not VerificationStatus.REJECTED
                for item in state.verification.claims
            )
        ):
            try:
                remaining = self._remaining(context.started_at)
                if (
                    remaining > 0 and state.token_usage_complete
                    and state.total_tokens < self._policy.token_budget
                ):
                    state.events.append(StageEvent(stage="partial-synthesis", status="started"))
                    self._log(state.run_id, "partial-synthesis", "started")
                    result = await asyncio.wait_for(
                        self._synthesizer.synthesize(
                            SynthesisInput(
                                question=state.request.question,
                                evidence=state.evidence_extractor.records,
                                source_profiles=state.evidence_extractor.source_profiles,
                                verification=state.verification,
                                partial=True,
                                terminal_reason=reason.split(":", 1)[0],
                            )
                        ),
                        timeout=remaining,
                    )
                    state.metrics.append(result.metrics)
                    if state.total_tokens > self._policy.token_budget:
                        raise TokenBudgetExceeded("token_budget_exceeded")
                    validate_synthesis(
                        result.output, state.verification, state.evidence_extractor.records,
                    )
                    result.output.partial = True
                    result.output.terminal_reason = reason.split(":", 1)[0]
                    state.synthesis = result.output
                    self._record(state, "partial-synthesis", result)
                    state.events.append(StageEvent(stage="partial-synthesis", status="completed"))
                    self._log(state.run_id, "partial-synthesis", "completed")
            except Exception as error:
                if isinstance(error, RuntimeInvocationError):
                    state.metrics.append(error.metrics)
                    state.token_usage_complete = error.usage_complete
                elif isinstance(error, TimeoutError):
                    state.token_usage_complete = False
                elif isinstance(error, TokenBudgetExceeded):
                    reason = "token_budget_exceeded"
                state.events.append(
                    StageEvent(
                        stage="partial-synthesis",
                        status="failed",
                        detail=self._error_detail(error),
                    )
                )
                self._log(
                    state.run_id, "partial-synthesis", "failed", error_type=type(error).__name__,
                )
        if state.synthesis is None:
            state.synthesis = SynthesisResult(
                answer=(
                    "The pipeline ended before a verified synthesis could be produced."
                ),
                partial=True,
                terminal_reason=reason.split(":", 1)[0],
            )
            # This fixed fallback contains no generated or source content.
            return self._finish(state, TerminalStatus.PARTIAL, reason)
        return await self._check_output_safety(context, TerminalStatus.PARTIAL, reason)

    def _finish(
        self,
        state: ResearchState,
        status: TerminalStatus,
        reason: str | None,
    ) -> ResearchState:
        state.status = status
        state.terminal_reason = reason
        state.artifact_uri = self._artifacts.uri_for_run(state.run_id)
        state.events.append(
            StageEvent(stage="pipeline", status=status.value, detail=reason)
        )
        self._artifacts.save_state(state)
        self._log(
            state.run_id,
            "pipeline",
            status.value,
            emit_metrics=True,
            total_tokens=state.total_tokens,
            verification_cycles=state.verification_cycles,
            safety_interventions=(
                1
                if state.synthesis and state.synthesis.safety_action != "NONE"
                else 0
            ),
        )
        return state

    def _remaining(self, started_at: float) -> float:
        return self._policy.deadline_seconds - (time.monotonic() - started_at)

    @staticmethod
    def _error_detail(error: BaseException) -> str:
        return str(error).strip() or type(error).__name__

    @staticmethod
    def _log(
        run_id: str,
        stage: str,
        status: str,
        *,
        emit_metrics: bool = False,
        **metadata: object,
    ) -> None:
        payload: dict[str, object] = {
            "event": "evidence_synthesis",
            "run_id": run_id,
            "stage": stage,
            "status": status,
            **metadata,
        }
        if emit_metrics:
            payload.update(
                {
                    "Runtime": "evidence-synthesis",
                    "Runs": 1,
                    "TotalTokens": metadata.get("total_tokens", 0),
                    "VerificationCycles": metadata.get("verification_cycles", 0),
                    "SafetyInterventions": metadata.get("safety_interventions", 0),
                    "_aws": {
                        "Timestamp": int(time.time() * 1000),
                        "CloudWatchMetrics": [
                            {
                                "Namespace": "EvidenceSynthesis",
                                "Dimensions": [["Runtime"]],
                                "Metrics": [
                                    {"Name": "Runs", "Unit": "Count"},
                                    {"Name": "TotalTokens", "Unit": "Count"},
                                    {"Name": "VerificationCycles", "Unit": "Count"},
                                    {"Name": "SafetyInterventions", "Unit": "Count"},
                                ],
                            }
                        ],
                    },
                }
            )
        _LOGGER.info(
            json.dumps(payload, sort_keys=True)
        )
