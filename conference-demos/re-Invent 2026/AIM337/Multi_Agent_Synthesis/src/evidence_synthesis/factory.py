# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

import os
from pathlib import Path

from evidence_synthesis.agents import (
    AnalystAgent,
    EvidenceExtractorAgent,
    SynthesizerAgent,
    VerifierAgent,
)
from evidence_synthesis.artifacts import (
    ArtifactStore,
    FileArtifactStore,
    NullArtifactStore,
    S3ArtifactStore,
)
from evidence_synthesis.config import GuardrailSettings, PipelinePolicy, StrandsSettings
from evidence_synthesis.deterministic_runtime import DeterministicRuntime
from evidence_synthesis.orchestrator import EvidenceSynthesisOrchestrator
from evidence_synthesis.runtime import AgentRuntime
from evidence_synthesis.safety import (
    BedrockGuardrailGate,
    LocalSafetyGate,
    MissingGuardrailGate,
    SafetyGate,
)
from evidence_synthesis.source_loader import SourceLoader
from evidence_synthesis.strands_runtime import StrandsRuntime


# SHOW 2 - Building an orchestrator with specific Runtime
def build_orchestrator(
    *,
    runtime_name: str,
    output_dir: Path | None = None,
    policy: PipelinePolicy | None = None,
    allow_local_files: bool = True,
) -> EvidenceSynthesisOrchestrator:
    runtime: AgentRuntime
    safety_gate: SafetyGate
    if runtime_name == "deterministic":
        runtime = DeterministicRuntime()
        safety_gate = LocalSafetyGate()
        region = None
    elif runtime_name == "strands":
        settings = StrandsSettings()
        runtime = StrandsRuntime(settings)
        guardrail_settings = GuardrailSettings.from_env()
        safety_gate = (
            BedrockGuardrailGate(guardrail_settings)
            if guardrail_settings is not None
            else MissingGuardrailGate()
        )
        region = settings.region
    else:
        raise ValueError("runtime_name must be 'deterministic' or 'strands'")

    loader = SourceLoader(
        region=region,
        allowed_s3_bucket=os.getenv("SOURCE_BUCKET") or None,
        allowed_s3_prefix=os.getenv("SOURCE_PREFIX", ""),
        allow_local_files=allow_local_files,
    )
    artifact_bucket = os.getenv("ARTIFACT_BUCKET", "").strip()
    artifacts: ArtifactStore
    if output_dir is not None:
        artifacts = FileArtifactStore(output_dir)
    elif artifact_bucket:
        artifacts = S3ArtifactStore(
            artifact_bucket,
            prefix=os.getenv("ARTIFACT_PREFIX", "runs"),
            region=region,
        )
    else:
        artifacts = NullArtifactStore()
    # All four roles use the selected runtime and their own instructions.
    return EvidenceSynthesisOrchestrator(
        evidence_extractor=EvidenceExtractorAgent(runtime, loader, safety_gate),
        analyst=AnalystAgent(runtime),
        verifier=VerifierAgent(runtime),
        synthesizer=SynthesizerAgent(runtime),
        policy=policy,
        safety_gate=safety_gate,
        artifact_store=artifacts,
    )
