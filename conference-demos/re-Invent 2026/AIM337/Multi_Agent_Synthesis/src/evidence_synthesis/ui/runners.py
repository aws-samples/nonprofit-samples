# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

import asyncio
from pathlib import Path

from evidence_synthesis.factory import build_orchestrator
from evidence_synthesis.models import ResearchState, RunRequest


def run_deterministic(
    request: RunRequest,
    *,
    base_dir: Path,
    output_dir: Path,
) -> ResearchState:
    orchestrator = build_orchestrator(
        runtime_name="deterministic",
        output_dir=output_dir,
    )
    return asyncio.run(orchestrator.run(request, base_dir=base_dir))
