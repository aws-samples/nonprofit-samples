# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, TypeVar, cast

from pydantic import BaseModel

from evidence_synthesis.config import StrandsSettings
from evidence_synthesis.models import AgentRole
from evidence_synthesis.runtime import RuntimeInvocationError, RuntimeResult, timed_metrics

OutputT = TypeVar("OutputT", bound=BaseModel)


class StrandsRuntime:
    def __init__(self, settings: StrandsSettings | None = None) -> None:
        if settings is None:
            settings = StrandsSettings()
        self._settings = settings
        # The orchestrator records safe diagnostics; SDK logs can include model content.
        logging.getLogger("strands").setLevel(logging.CRITICAL + 1)

    async def invoke_async(
        self, role: AgentRole, payload: BaseModel,
        output_type: type[OutputT], system_prompt: str,
    ) -> RuntimeResult[OutputT]:
        cancelled = threading.Event()
        try:
            return await asyncio.to_thread(
                self.invoke, role, payload, output_type, system_prompt,
                cancel_signal=cancelled,
            )
        except asyncio.CancelledError:
            cancelled.set()
            raise

    def invoke(
        self,
        role: AgentRole,
        payload: BaseModel,
        output_type: type[OutputT],
        system_prompt: str,
        *,
        cancel_signal: threading.Event | None = None,
    ) -> RuntimeResult[OutputT]:
        from botocore.config import Config
        from strands import Agent
        from strands.models import BedrockModel

        model = BedrockModel(
            model_id=self._settings.model_ids[role],
            region_name=self._settings.region,
            max_tokens=self._settings.max_tokens[role],
            boto_client_config=Config(
                connect_timeout=5, read_timeout=60, retries={"total_max_attempts": 1},
            ),
        )
        # Each call gets fresh history; the orchestrator owns retries.
        agent = Agent(
            model=model, system_prompt=system_prompt, callback_handler=None,
            retry_strategy=None,
        )
        prompt = (
            "Use only the following typed pipeline payload. Return the requested schema.\n\n"
            + payload.model_dump_json(indent=2)
        )
        started_at = time.perf_counter()
        # Ask the model to return this stage's result type.
        result = None
        try:
            result = agent(
                prompt, structured_output_model=output_type,
                limits={"turns": 2}, cancel_signal=cancel_signal,
            )
            if (
                result.structured_output is None
                or getattr(result, "stop_reason", None) == "cancelled"
            ):
                raise ValueError("Structured model output was not produced")
        except Exception as error:
            input_tokens, output_tokens = self._token_usage(result if result is not None else agent)
            metrics = timed_metrics(
                role=role, model_id=self._settings.model_ids[role], started_at=started_at,
                input_tokens=input_tokens, output_tokens=output_tokens,
            )
            complete = result is not None and getattr(result, "stop_reason", None) != "cancelled"
            raise RuntimeInvocationError(metrics, complete) from error
        output = cast(OutputT, result.structured_output)
        input_tokens, output_tokens = self._token_usage(result)
        return RuntimeResult(
            output=output,
            metrics=timed_metrics(
                role=role,
                model_id=self._settings.model_ids[role],
                started_at=started_at,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            ),
        )

    @staticmethod
    def _token_usage(result: Any) -> tuple[int, int]:
        metrics = getattr(result, "metrics", None) or getattr(result, "event_loop_metrics", None)
        if metrics is None:
            return 0, 0
        usage = getattr(metrics, "accumulated_usage", None)
        if usage is None and isinstance(metrics, dict):
            usage = metrics.get("accumulated_usage", metrics.get("usage", {}))
        if usage is None:
            return 0, 0
        if hasattr(usage, "model_dump"):
            usage = usage.model_dump()
        if not isinstance(usage, dict):
            return 0, 0
        input_tokens = StrandsRuntime._as_int(
            usage.get("inputTokens", usage.get("input_tokens", usage.get("input", 0)))
        )
        output_tokens = StrandsRuntime._as_int(
            usage.get("outputTokens", usage.get("output_tokens", usage.get("output", 0)))
        )
        return input_tokens, output_tokens

    @staticmethod
    def _as_int(value: Any) -> int:
        return int(value) if value is not None else 0
