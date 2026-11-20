# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
"""Read-only SDK observations for the sequential evaluation runner."""

from copy import deepcopy
from typing import Any
from unittest.mock import patch

import strands
from strands import Agent
from strands.hooks import AfterModelCallEvent, AfterToolCallEvent

from evidence_synthesis.agents.verifier import VerifierAgent
from evidence_synthesis.models import VerificationInput, VerificationResult
from evidence_synthesis.runtime import AgentRuntime, RuntimeInvocationError, RuntimeResult


def observed_agent(observation: dict[str, Any]) -> type[Agent]:
    class ObservedAgent(Agent):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.hooks.add_callback(AfterModelCallEvent, self.after_model)
            self.hooks.add_callback(AfterToolCallEvent, self.after_tool)

        def after_model(self, event: AfterModelCallEvent) -> None:
            response = event.stop_response
            observation["events"].append({
                "kind": "model",
                "stop_reason": response.stop_reason if response else None,
                "exception_type": type(event.exception).__name__ if event.exception else None,
            })

        def after_tool(self, event: AfterToolCallEvent) -> None:
            arguments = event.tool_use.get("input", {})
            observation["events"].append({
                "kind": "tool",
                "name": event.tool_use.get("name"),
                "input_contradictions": deepcopy(arguments.get("contradictions"))
                if isinstance(arguments, dict) else None,
                "result": deepcopy(event.result),
                "exception_type": type(event.exception).__name__ if event.exception else None,
            })

        def __call__(self, *args: Any, **kwargs: Any) -> Any:
            observation["limits"] = deepcopy(kwargs.get("limits"))
            result: Any = super().__call__(*args, **kwargs)
            invocation = result.metrics.latest_agent_invocation
            observation["agent_result"] = {
                "stop_reason": result.stop_reason,
                "structured_output_present": result.structured_output is not None,
                "cycle_count": len(invocation.cycles) if invocation else None,
                "usage": deepcopy(invocation.usage) if invocation else None,
            }
            return result

    return ObservedAgent


class ObservedVerifier(VerifierAgent):
    def __init__(self, runtime: AgentRuntime) -> None:
        super().__init__(runtime)
        self.last_diagnostics: dict[str, Any] = {}

    async def verify(
        self, payload: VerificationInput,
    ) -> RuntimeResult[VerificationResult]:
        self.last_diagnostics = {"events": []}
        # The runner is sequential; this temporary hook never changes the SDK on disk.
        with patch.object(strands, "Agent", observed_agent(self.last_diagnostics)):
            try:
                return await super().verify(payload)
            except RuntimeInvocationError as error:
                cause = error.__cause__ or error
                self.last_diagnostics["error"] = {
                    "type": type(cause).__name__, "message": str(cause),
                }
                raise
