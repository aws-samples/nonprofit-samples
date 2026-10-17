# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from evidence_synthesis.models import AgentRole

_REGION_ALIASES = AliasChoices("AWS_REGION", "AWS_DEFAULT_REGION")


class PipelinePolicy(BaseSettings):
    """Orchestration limits, read from PIPELINE_* env vars or a local .env file."""

    model_config = SettingsConfigDict(
        env_prefix="PIPELINE_",
        env_file=".env",
        extra="ignore",
        frozen=True,
    )

    deadline_seconds: float = 300.0
    token_budget: int = 200_000
    # Retries repeat failed calls; verification cycles include the first review.
    max_retries: int = 1
    max_verification_cycles: int = 2


class StrandsSettings(BaseSettings):
    """Per-role Bedrock model routing, read from env vars or a local .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        frozen=True,
        populate_by_name=True,
    )

    region: str = Field(default="us-east-1", validation_alias=_REGION_ALIASES)

    evidence_extractor_model_id: str = "us.amazon.nova-lite-v1:0"
    analyst_model_id: str = "us.anthropic.claude-sonnet-5"
    verifier_model_id: str = "us.anthropic.claude-sonnet-5"
    synthesizer_model_id: str = "us.anthropic.claude-sonnet-5"

    evidence_extractor_max_tokens: int = 4096
    analyst_max_tokens: int = 10_000
    verifier_max_tokens: int = 15_000
    synthesizer_max_tokens: int = 15_000

    @property
    def model_ids(self) -> dict[AgentRole, str]:
        return {
            AgentRole.EVIDENCE_EXTRACTOR: self.evidence_extractor_model_id,
            AgentRole.ANALYST: self.analyst_model_id,
            AgentRole.VERIFIER: self.verifier_model_id,
            AgentRole.SYNTHESIZER: self.synthesizer_model_id,
        }

    @property
    def max_tokens(self) -> dict[AgentRole, int]:
        return {
            AgentRole.EVIDENCE_EXTRACTOR: self.evidence_extractor_max_tokens,
            AgentRole.ANALYST: self.analyst_max_tokens,
            AgentRole.VERIFIER: self.verifier_max_tokens,
            AgentRole.SYNTHESIZER: self.synthesizer_max_tokens,
        }


class GuardrailSettings(BaseSettings):
    """Standalone Bedrock Guardrail configuration, required for live Strands runs."""

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        frozen=True,
        populate_by_name=True,
    )

    guardrail_id: str = Field(default="", validation_alias="BEDROCK_GUARDRAIL_ID")
    guardrail_version: str = Field(default="", validation_alias="BEDROCK_GUARDRAIL_VERSION")
    region: str = Field(default="us-east-1", validation_alias=_REGION_ALIASES)

    @classmethod
    def from_env(cls) -> GuardrailSettings | None:
        settings = cls()
        if not settings.guardrail_id.strip() or not settings.guardrail_version.strip():
            return None
        return settings
