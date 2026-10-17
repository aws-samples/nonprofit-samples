# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

import os
from pathlib import Path

import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks
from evidence_synthesis_infra import FoundationStack, RuntimeStack
from evidence_synthesis_infra.availability_zones import (
    resolve_availability_zone_names,
)

from evidence_synthesis.config import StrandsSettings


def _field_default(name: str) -> object:
    """Read a StrandsSettings field default without instantiating the class.

    Instantiation would resolve env vars and .env files, letting the
    deployer's shell leak into the deployment. Field defaults are the
    single source of truth shared with the application runtime.
    """
    return StrandsSettings.model_fields[name].default

app = cdk.App()
region = app.node.try_get_context("deployment_region") or "us-east-1"
availability_zone_ids = app.node.try_get_context("agentcore_availability_zone_ids") or [
    "use1-az4",
    "use1-az1",
]
environment = cdk.Environment(
    account=os.getenv("CDK_DEFAULT_ACCOUNT"),
    region=region,
)
roles = ("evidence_extractor", "analyst", "verifier", "synthesizer")
model_ids = {
    role: str(
        app.node.try_get_context(f"{role}_model_id")
        or _field_default(f"{role}_model_id")
    )
    for role in roles
}
max_tokens = {
    role: int(
        app.node.try_get_context(f"{role}_max_tokens")
        or _field_default(f"{role}_max_tokens")
    )
    for role in roles
}

foundation = FoundationStack(
    app,
    "EvidenceSynthesisFoundation",
    availability_zones=resolve_availability_zone_names(
        region,
        availability_zone_ids,
    ),
    replacement_subnet_cidr=(
        app.node.try_get_context("agentcore_replacement_subnet_cidr")
        or "10.42.2.0/24"
    ),
    env=environment,
    termination_protection=True,
    description="Stateful data, safety, and network resources for evidence synthesis.",
)
runtime = RuntimeStack(
    app,
    "EvidenceSynthesisRuntime",
    env=environment,
    foundation=foundation,
    model_ids=model_ids,
    max_tokens=max_tokens,
    source_path=Path(__file__).resolve().parents[1],
    description="AgentCore Runtime and observability for evidence synthesis.",
)
runtime.add_stack_dependency(foundation)

cdk.Tags.of(app).add("Application", "evidence-synthesis")
cdk.Aspects.of(app).add(AwsSolutionsChecks(verbose=True))

app.synth()
