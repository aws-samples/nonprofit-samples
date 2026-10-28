# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
import pytest

from evidence_synthesis.config import PipelinePolicy, StrandsSettings
from evidence_synthesis.models import AgentRole


def test_default_claude_roles_use_sonnet_5() -> None:
    settings = StrandsSettings(_env_file=None)

    assert settings.model_ids[AgentRole.ANALYST] == "us.anthropic.claude-sonnet-5"
    assert settings.model_ids[AgentRole.VERIFIER] == "us.anthropic.claude-sonnet-5"
    assert settings.model_ids[AgentRole.SYNTHESIZER] == "us.anthropic.claude-sonnet-5"


def test_default_max_tokens_are_role_specific() -> None:
    settings = StrandsSettings(_env_file=None)

    assert settings.max_tokens == {
        AgentRole.EVIDENCE_EXTRACTOR: 4096,
        AgentRole.ANALYST: 10_000,
        AgentRole.VERIFIER: 15_000,
        AgentRole.SYNTHESIZER: 15_000,
    }


def test_role_env_vars_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVIDENCE_EXTRACTOR_MODEL_ID", "extractor-test-model")
    monkeypatch.setenv("EVIDENCE_EXTRACTOR_MAX_TOKENS", "2048")
    monkeypatch.setenv("VERIFIER_MAX_TOKENS", "8000")
    monkeypatch.setenv("ANALYST_MODEL_ID", "us.amazon.nova-pro-v1:0")

    settings = StrandsSettings(_env_file=None)

    assert settings.model_ids[AgentRole.EVIDENCE_EXTRACTOR] == "extractor-test-model"
    assert settings.max_tokens[AgentRole.EVIDENCE_EXTRACTOR] == 2048
    assert settings.max_tokens[AgentRole.VERIFIER] == 8000
    assert settings.max_tokens[AgentRole.ANALYST] == 10_000
    assert settings.model_ids[AgentRole.ANALYST] == "us.amazon.nova-pro-v1:0"


def test_pipeline_policy_reads_prefixed_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPELINE_TOKEN_BUDGET", "12345")

    policy = PipelinePolicy(_env_file=None)

    assert policy.token_budget == 12345
    assert policy.deadline_seconds == 300.0


def test_explicit_kwargs_win_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPELINE_TOKEN_BUDGET", "12345")

    policy = PipelinePolicy(_env_file=None, token_budget=1)

    assert policy.token_budget == 1
