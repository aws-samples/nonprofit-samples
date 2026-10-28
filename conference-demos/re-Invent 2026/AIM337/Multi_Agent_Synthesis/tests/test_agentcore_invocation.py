# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from starlette.testclient import TestClient

import evidence_synthesis.agentcore_app as agentcore_app
from evidence_synthesis.agentcore_client import AgentCoreRuntimeClient
from evidence_synthesis.config import PipelinePolicy
from evidence_synthesis.factory import build_orchestrator
from evidence_synthesis.models import ResearchState, RunRequest, TerminalStatus


class FakeBody:
    def __init__(self, content: bytes | Exception) -> None:
        self.content = content
        self.closed = False

    def read(self) -> bytes:
        if isinstance(self.content, Exception):
            raise self.content
        return self.content

    def close(self) -> None:
        self.closed = True


class FakeAgentCoreClient:
    def __init__(self, body: FakeBody) -> None:
        self.body = body
        self.requests: list[dict[str, Any]] = []

    def invoke_agent_runtime(self, **request: Any) -> dict[str, Any]:
        self.requests.append(request)
        return {"contentType": "application/json", "response": self.body}


def _request() -> RunRequest:
    return RunRequest(
        question="What is supported?",
        sources=[{"source_id": "one", "content": "Supported evidence."}],
    )


@pytest.mark.asyncio
async def test_agentcore_returns_the_complete_pipeline_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        agentcore_app, "orchestrator", build_orchestrator(runtime_name="deterministic"),
    )

    result = await agentcore_app.invoke(_request().model_dump(mode="json"))
    state = ResearchState.model_validate(result)

    assert state.status is TerminalStatus.COMPLETED
    assert state.evidence_extractor is not None
    assert state.analysis is not None
    assert state.verification is not None
    assert state.synthesis is not None
    assert state.synthesis.citations
    assert len(state.metrics) == 4
    assert state.events[-1].status == "completed"
    assert json.loads(json.dumps(result)) == result


@pytest.mark.parametrize(("question", "deadline", "status"), [
    ("What is supported?", 300, TerminalStatus.COMPLETED),
    ("What is supported?", 0, TerminalStatus.PARTIAL),
    ("Should we deny insurance to this individual?", 300, TerminalStatus.BLOCKED),
])
def test_agentcore_http_endpoint_returns_terminal_state_as_json(
    monkeypatch: pytest.MonkeyPatch, question: str, deadline: int, status: TerminalStatus,
) -> None:
    monkeypatch.setattr(
        agentcore_app, "orchestrator", build_orchestrator(
            runtime_name="deterministic", policy=PipelinePolicy(deadline_seconds=deadline),
        ),
    )
    payload = _request().model_dump(mode="json")
    payload["question"] = question

    with TestClient(agentcore_app.app) as client:
        response = client.post("/invocations", json=payload)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    state = ResearchState.model_validate(response.json())
    assert state.status is status
    assert state.synthesis is not None
    assert state.events[-1].status == status.value


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {**_request().model_dump(mode="json"), "_stream": True}])
async def test_agentcore_validates_requests_before_running(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, object],
) -> None:
    runner = AsyncMock()
    monkeypatch.setattr(agentcore_app, "orchestrator", runner)

    with pytest.raises(ValidationError):
        await agentcore_app.invoke(payload)

    runner.run.assert_not_called()


@pytest.mark.asyncio
async def test_agentcore_client_reads_one_final_json_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        agentcore_app, "orchestrator", build_orchestrator(runtime_name="deterministic"),
    )
    request = _request()
    state = await agentcore_app.invoke(request.model_dump(mode="json"))
    body = FakeBody(json.dumps(state).encode())
    fake = FakeAgentCoreClient(body)

    result = AgentCoreRuntimeClient(fake).invoke(
        request, runtime_arn="arn:runtime", qualifier="live", session_id="test-session",
    )

    assert result.model_dump(mode="json") == state
    assert body.closed
    assert fake.requests == [{
        "agentRuntimeArn": "arn:runtime",
        "qualifier": "live",
        "runtimeSessionId": "test-session",
        "contentType": "application/json",
        "accept": "application/json",
        "payload": request.model_dump_json().encode(),
    }]


@pytest.mark.parametrize(("content", "error"), [
    (b"not json", json.JSONDecodeError),
    (b'{"unexpected": "response"}', ValidationError),
    (TimeoutError("Read timed out"), TimeoutError),
])
def test_agentcore_client_closes_response_when_reading_or_validation_fails(
    content: bytes | Exception, error: type[Exception],
) -> None:
    body = FakeBody(content)
    client = AgentCoreRuntimeClient(FakeAgentCoreClient(body))

    with pytest.raises(error):
        client.invoke(
            _request(), runtime_arn="arn:runtime", qualifier="live", session_id="test-session",
        )

    assert body.closed


@pytest.mark.asyncio
async def test_runtime_request_cannot_read_a_local_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = tmp_path / "private.md"
    document.write_text("DUMMY_LOCAL_CONTENT")
    monkeypatch.setattr(
        agentcore_app, "orchestrator", build_orchestrator(
            runtime_name="deterministic",
            allow_local_files=False,
            policy=PipelinePolicy(max_retries=0),
        ),
    )
    payload = RunRequest(
        question="What does this document say?",
        sources=[{"source_id": "local", "uri": str(document)}],
    )
    result = await agentcore_app.invoke(payload.model_dump(mode="json"))

    assert result["status"] == "partial"
    assert result["evidence_extractor"] is None
    assert "Local file sources are not allowed" in str(result["terminal_reason"])
    assert "DUMMY_LOCAL_CONTENT" not in json.dumps(result)
