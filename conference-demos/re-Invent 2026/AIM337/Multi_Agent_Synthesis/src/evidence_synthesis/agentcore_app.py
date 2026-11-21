# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

import asyncio
import logging
import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from evidence_synthesis.factory import build_orchestrator, build_single_agent_runner
from evidence_synthesis.models import ExecutionMode, RunRequest

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())

#  SHOW 1: AgentCore and Orchestrator Init
app = BedrockAgentCoreApp()
orchestrator = build_orchestrator(runtime_name="strands", allow_local_files=False)
single_agent = build_single_agent_runner(runtime_name="strands", allow_local_files=False)
_invocation_lock = asyncio.Lock()


@app.entrypoint
async def invoke(payload: dict[str, object]) -> dict[str, object]:
    request = RunRequest.model_validate(payload)
    if _invocation_lock.locked():
        raise ValueError("This runtime session is already processing a run")
    async with _invocation_lock:
        runner = (
            single_agent
            if request.execution_mode is ExecutionMode.SINGLE_AGENT else orchestrator
        )
        state = await runner.run(request)
    return state.model_dump(mode="json")


if __name__ == "__main__":
    app.run()
