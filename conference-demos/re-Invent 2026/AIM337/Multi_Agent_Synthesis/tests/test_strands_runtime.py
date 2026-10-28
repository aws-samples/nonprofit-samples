# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from evidence_synthesis.config import StrandsSettings
from evidence_synthesis.models import (
    AgentRole,
    AnalysisInput,
    AnalysisResult,
    EvidenceExtractorInput,
    EvidenceExtractorResult,
    SynthesisInput,
    SynthesisResult,
    VerificationInput,
    VerificationResult,
)
from evidence_synthesis.strands_runtime import StrandsRuntime


def test_strands_invocation_keeps_role_routing_and_uses_fresh_agent_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analysis = AnalysisResult(claims=[])
    verification = VerificationResult(claims=[], citation_closure_complete=True)
    outputs = {
        EvidenceExtractorResult: EvidenceExtractorResult(records=[]), AnalysisResult: analysis,
        VerificationResult: verification,
        SynthesisResult: SynthesisResult(answer="Supported answer."),
    }
    agents: list[Mock] = []

    def create_agent(**configuration: object) -> Mock:
        def answer(
            prompt: str, *, structured_output_model: type,
            limits: dict[str, int], cancel_signal: object,
        ) -> SimpleNamespace:
            assert limits == {"turns": 2}
            assert prompt.startswith("Use only the following typed pipeline payload.")
            payload = json.loads(prompt.split("\n\n", maxsplit=1)[1])
            assert payload["question"] == "What is supported?"
            return SimpleNamespace(
                structured_output=outputs[structured_output_model].model_copy(deep=True),
                metrics=SimpleNamespace(accumulated_usage={"inputTokens": 12, "outputTokens": 8}),
            )

        agent = Mock(side_effect=answer)
        agents.append(agent)
        return agent

    agent_factory = Mock(side_effect=create_agent)
    model_factory = Mock()
    strands = ModuleType("strands")
    models = ModuleType("strands.models")
    monkeypatch.setattr(strands, "Agent", agent_factory, raising=False)
    monkeypatch.setattr(models, "BedrockModel", model_factory, raising=False)
    monkeypatch.setitem(sys.modules, "strands", strands)
    monkeypatch.setitem(sys.modules, "strands.models", models)
    settings = StrandsSettings(
        region="us-east-1", evidence_extractor_model_id="extractor-test",
        analyst_model_id="analyst-test",
        verifier_model_id="verifier-test", synthesizer_model_id="synthesizer-test",
    )
    runtime = StrandsRuntime(settings)
    question = "What is supported?"
    calls = [
        (AgentRole.EVIDENCE_EXTRACTOR, EvidenceExtractorInput(
            question=question, chunks=[], max_evidence=1,
        ), EvidenceExtractorResult),
        (AgentRole.ANALYST, AnalysisInput(question=question, evidence=[]), AnalysisResult),
        (AgentRole.VERIFIER, VerificationInput(
            question=question, evidence=[], analysis=analysis,
        ), VerificationResult),
        (AgentRole.SYNTHESIZER, SynthesisInput(
            question=question, evidence=[], verification=verification,
        ), SynthesisResult),
    ]
    for role, payload, output_type in [*calls, calls[0]]:
        result = runtime.invoke(role, payload, output_type, f"Instructions for {role.value}")
        assert isinstance(result.output, output_type)
        assert result.metrics.role is role
        assert result.metrics.model_id == settings.model_ids[role]
        assert result.metrics.total_tokens == 20

    assert agent_factory.call_count == 5
    assert model_factory.call_count == 5
    assert [agent.call_count for agent in agents] == [1] * 5
    roles = [*AgentRole, AgentRole.EVIDENCE_EXTRACTOR]
    for role, call in zip(roles, model_factory.call_args_list, strict=True):
        actual = {key: value for key, value in call.kwargs.items() if key != "boto_client_config"}
        assert actual == {
            "model_id": settings.model_ids[role], "region_name": settings.region,
            "max_tokens": settings.max_tokens[role],
        }
        assert call.kwargs["boto_client_config"].read_timeout == 60
        assert call.kwargs["boto_client_config"].retries == {"total_max_attempts": 1}
    assert [call.kwargs["system_prompt"] for call in agent_factory.call_args_list] == [
        f"Instructions for {role.value}" for role in roles
    ]
    assert all(call.kwargs["callback_handler"] is None for call in agent_factory.call_args_list)
    assert all(call.kwargs["retry_strategy"] is None for call in agent_factory.call_args_list)
