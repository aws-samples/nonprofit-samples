# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
from typing import Any

from evidence_synthesis.models import ResearchState, RunRequest


class AgentCoreRuntimeClient:
    def __init__(self, client: Any) -> None:
        self._client = client

    def invoke(
        self,
        request: RunRequest,
        *,
        runtime_arn: str,
        qualifier: str,
        session_id: str,
    ) -> ResearchState:
        response = self._client.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            qualifier=qualifier,
            runtimeSessionId=session_id,
            contentType="application/json",
            accept="application/json",
            payload=request.model_dump_json().encode(),
        )
        body = response["response"]
        try:
            # Read the final state returned by the deployed pipeline.
            value = json.loads(body.read().decode("utf-8"))
        finally:
            body.close()
        return ResearchState.model_validate(value)
