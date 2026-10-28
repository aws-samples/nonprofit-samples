# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from evidence_synthesis.models import AgentRole, InvocationMetrics

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)


@dataclass(slots=True)
class RuntimeResult[OutputT: BaseModel]:
    output: OutputT
    metrics: InvocationMetrics


# The agents use the same interface for live models and the offline simulation.
class AgentRuntime(Protocol):
    def invoke(
        self,
        role: AgentRole,
        payload: BaseModel,
        output_type: type[OutputT],
        system_prompt: str,
    ) -> RuntimeResult[OutputT]: ...


class RuntimeInvocationError(RuntimeError):
    def __init__(self, metrics: InvocationMetrics, usage_complete: bool) -> None:
        super().__init__("model_invocation_failed")
        self.metrics = metrics
        self.usage_complete = usage_complete


@runtime_checkable
class AsyncAgentRuntime(Protocol):
    async def invoke_async(
        self, role: AgentRole, payload: BaseModel,
        output_type: type[OutputT], system_prompt: str,
    ) -> RuntimeResult[OutputT]: ...


async def invoke_runtime[OutputT: BaseModel](
    runtime: AgentRuntime, role: AgentRole, payload: BaseModel,
    output_type: type[OutputT], system_prompt: str,
) -> RuntimeResult[OutputT]:
    if isinstance(runtime, AsyncAgentRuntime):
        return await runtime.invoke_async(role, payload, output_type, system_prompt)
    return await asyncio.to_thread(runtime.invoke, role, payload, output_type, system_prompt)


def timed_metrics(
    *,
    role: AgentRole,
    model_id: str,
    started_at: float,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> InvocationMetrics:
    return InvocationMetrics(
        role=role,
        model_id=model_id,
        latency_ms=(time.perf_counter() - started_at) * 1000,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )
