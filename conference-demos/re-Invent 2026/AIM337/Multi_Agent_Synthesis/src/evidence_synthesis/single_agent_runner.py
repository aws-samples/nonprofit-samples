# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from pathlib import Path

from evidence_synthesis.agents.single_agent import SingleAgent
from evidence_synthesis.artifacts import ArtifactStore, NullArtifactStore
from evidence_synthesis.config import PipelinePolicy
from evidence_synthesis.models import (
    ExecutionMode,
    ResearchState,
    RunRequest,
    SingleAgentInput,
    SourceChunk,
    StageEvent,
    SynthesisResult,
    TerminalStatus,
)
from evidence_synthesis.run_logging import log_run_event
from evidence_synthesis.runtime import RuntimeInvocationError
from evidence_synthesis.safety import LocalSafetyGate, SafetyDecision, SafetyGate, SafetyViolation
from evidence_synthesis.source_loader import SourceLimitError, SourceLoader
from evidence_synthesis.validation import ContractError, validate_single_agent_synthesis

_LOGGER = logging.getLogger(__name__)


class SingleAgentFailed(RuntimeError):
    pass


class SingleAgentRunner:
    def __init__(
        self,
        *,
        agent: SingleAgent,
        source_loader: SourceLoader,
        policy: PipelinePolicy | None = None,
        safety_gate: SafetyGate | None = None,
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        self._agent = agent
        self._loader = source_loader
        self._policy = policy if policy is not None else PipelinePolicy()
        self._safety = safety_gate if safety_gate is not None else LocalSafetyGate()
        self._artifacts = artifact_store if artifact_store is not None else NullArtifactStore()

    async def run(self, request: RunRequest, *, base_dir: Path | None = None) -> ResearchState:
        if request.execution_mode is not ExecutionMode.SINGLE_AGENT:
            raise ValueError("The single-agent runner requires execution_mode='single_agent'")
        state = ResearchState(run_id=request.run_id or uuid.uuid4().hex, request=request)
        started_at = time.monotonic()
        await asyncio.to_thread(self._artifacts.reserve_run, state.run_id)
        self._event(state, "started")
        try:
            await self._screen(request.question, "input", started_at)
            chunks = await self._load_sources(request, base_dir or Path.cwd(), started_at)
            if not chunks:
                raise SingleAgentFailed("no_readable_sources")
            payload = SingleAgentInput(question=request.question, chunks=chunks)
            await self._screen(payload.model_dump_json(), "source", started_at)

            answer = await self._invoke(state, payload, started_at)
            validate_single_agent_synthesis(answer, chunks)
            # Terminal and safety fields belong to the runner, not the model.
            answer.partial = False
            answer.terminal_reason = None
            answer.safety_action = "NONE"
            decision = await self._screen(answer.model_dump_json(), "output", started_at)
            answer.safety_action = decision.action
            state.synthesis = answer
            self._artifacts.save_stage(state.run_id, "single_agent", answer)
            return self._finish(state, TerminalStatus.COMPLETED)
        except SafetyViolation as error:
            state.synthesis = SynthesisResult(
                answer=error.decision.text, partial=True,
                terminal_reason=error.reason, safety_action=error.decision.action,
            )
            return self._finish(state, TerminalStatus.BLOCKED, error.reason)
        except (SingleAgentFailed, ContractError) as error:
            reason = str(error)
        except SourceLimitError:
            reason = "source_limit_exceeded"
        except TimeoutError:
            reason = "deadline_exceeded"
        except Exception:
            # A fixed response keeps provider diagnostics and unreviewed output out of artifacts.
            reason = "single_agent_failed"
        state.synthesis = SynthesisResult(
            answer="The single agent could not produce a complete, cited answer.",
            partial=True, terminal_reason=reason,
        )
        return self._finish(state, TerminalStatus.FAILED, reason)

    async def _load_sources(
        self, request: RunRequest, base_dir: Path, started_at: float,
    ) -> list[SourceChunk]:
        remaining = self._remaining(started_at)
        cancelled = threading.Event()
        try:
            chunks = await asyncio.wait_for(
                asyncio.to_thread(
                    self._loader.load_all, request.sources, base_dir, cancel_signal=cancelled,
                ),
                timeout=remaining,
            )
        finally:
            cancelled.set()
        # IDs name the supplied chunks directly; no extraction model runs in this path.
        return [
            chunk.model_copy(update={"chunk_id": f"E{index:03}"})
            for index, chunk in enumerate(chunks, start=1)
        ]

    async def _screen(self, text: str, stage: str, started_at: float) -> SafetyDecision:
        remaining = self._remaining(started_at)
        operation = (
            self._safety.evaluate_output if stage == "output" else self._safety.evaluate_input
        )
        try:
            decision = await asyncio.wait_for(operation(text), timeout=remaining)
        except Exception:
            raise SafetyViolation(
                f"{stage}_safety_unavailable",
                SafetyDecision(False, "The request could not be evaluated safely.", "ERROR"),
            ) from None
        if not decision.allowed:
            raise SafetyViolation(f"{stage}_safety_policy", decision)
        return decision

    async def _invoke(
        self, state: ResearchState, payload: SingleAgentInput, started_at: float,
    ) -> SynthesisResult:
        for attempt in range(1, self._policy.max_retries + 2):
            remaining = self._remaining(started_at)
            if state.total_tokens >= self._policy.token_budget:
                raise SingleAgentFailed("token_budget_exceeded")
            if attempt > 1:
                self._event(state, "started", attempt)
            try:
                result = await asyncio.wait_for(
                    self._agent.synthesize(payload), timeout=remaining,
                )
            except RuntimeInvocationError as error:
                error.metrics.attempts = attempt
                state.metrics.append(error.metrics)
                state.token_usage_complete = error.usage_complete
                self._event(state, "failed", attempt)
                # Stop when consumption is unknown; a retry could overspend the run budget.
                if not error.usage_complete:
                    raise SingleAgentFailed("model_usage_unavailable") from error
                if state.total_tokens >= self._policy.token_budget:
                    raise SingleAgentFailed("token_budget_exceeded") from error
                if attempt > self._policy.max_retries:
                    raise SingleAgentFailed("model_invocation_failed") from error
                await asyncio.sleep(min(2 ** (attempt - 1), self._remaining(started_at)))
                continue
            except TimeoutError:
                state.token_usage_complete = False
                self._event(state, "timed_out", attempt)
                raise SingleAgentFailed("model_timeout_usage_unavailable") from None
            except asyncio.CancelledError:
                state.token_usage_complete = False
                raise
            except Exception:
                state.token_usage_complete = False
                raise SingleAgentFailed("model_usage_unavailable") from None
            result.metrics.attempts = attempt
            state.metrics.append(result.metrics)
            if state.total_tokens > self._policy.token_budget:
                raise SingleAgentFailed("token_budget_exceeded")
            return result.output
        raise SingleAgentFailed("model_invocation_failed")

    def _remaining(self, started_at: float) -> float:
        remaining = self._policy.deadline_seconds - (time.monotonic() - started_at)
        if remaining <= 0:
            raise TimeoutError("deadline_exceeded")
        return remaining

    @staticmethod
    def _event(state: ResearchState, status: str, attempt: int = 1) -> None:
        state.events.append(StageEvent(stage="single_agent", status=status))
        log_run_event(_LOGGER, state.run_id, "single_agent", status, attempt=attempt)

    def _finish(
        self, state: ResearchState, status: TerminalStatus, reason: str | None = None,
    ) -> ResearchState:
        state.status = status
        state.terminal_reason = reason
        state.artifact_uri = self._artifacts.uri_for_run(state.run_id)
        state.events.append(StageEvent(stage="single_agent", status=status.value, detail=reason))
        self._artifacts.save_state(state)
        log_run_event(
            _LOGGER, state.run_id, "single_agent", status.value,
            attempt=state.metrics[-1].attempts if state.metrics else 1,
            emit_metrics=True, total_tokens=state.total_tokens, verification_cycles=0,
            safety_interventions=int(
                state.synthesis is not None and state.synthesis.safety_action != "NONE"
            ),
        )
        return state
