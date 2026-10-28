# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from pathlib import Path

import pytest

from evidence_synthesis.config import PipelinePolicy
from evidence_synthesis.factory import build_orchestrator
from evidence_synthesis.models import RunRequest, TerminalStatus

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_MANIFEST = PROJECT_ROOT / "examples" / "coastal-resilience" / "manifest.json"


@pytest.mark.asyncio
async def test_deterministic_pipeline_is_citation_closed(tmp_path: Path) -> None:
    request = RunRequest.model_validate_json(EXAMPLE_MANIFEST.read_text(encoding="utf-8"))
    orchestrator = build_orchestrator(
        runtime_name="deterministic",
        output_dir=tmp_path,
    )

    state = await orchestrator.run(request, base_dir=EXAMPLE_MANIFEST.parent)

    assert state.status is TerminalStatus.COMPLETED
    assert state.verification is not None
    assert state.verification.citation_closure_complete
    assert state.verification.contradictions == []
    assert all(item.claim.stance is None for item in state.verification.claims)
    assert state.synthesis is not None
    assert state.synthesis.citations
    pdf_citations = [
        citation for citation in state.synthesis.citations if citation.source_id == "mapped-risk"
    ]
    assert len(pdf_citations) == 1
    assert pdf_citations[0].locator.endswith("mapped-risk.pdf#page=1")
    assert all(
        f"[{citation.evidence_id}]" in state.synthesis.answer
        for citation in state.synthesis.citations
    )
    assert (tmp_path / state.run_id / "state.json").exists()
    assert (tmp_path / state.run_id / "answer.md").exists()
    assert state.artifact_uri == str((tmp_path / state.run_id).resolve())


@pytest.mark.asyncio
async def test_deterministic_pipeline_detects_explicitly_labelled_conflicts() -> None:
    request = RunRequest(
        question="What do the reports say about flood risk?",
        sources=[
            {
                "source_id": "report-a",
                "content": "Report A describes lower flood exposure.",
                "metadata": {"topic": "flood risk", "stance": "lower"},
            },
            {
                "source_id": "report-b",
                "content": "Report B describes higher flood exposure.",
                "metadata": {"topic": "flood risk", "stance": "higher"},
            },
        ],
    )

    state = await build_orchestrator(runtime_name="deterministic").run(request)

    assert state.status is TerminalStatus.COMPLETED
    assert state.verification is not None
    assert len(state.verification.contradictions) == 1
    assert state.verification.contradictions[0].topic == "flood risk"
    assert len(state.verification.contradictions[0].claim_ids) == 2


@pytest.mark.asyncio
async def test_safety_boundary_blocks_individual_decision() -> None:
    request = RunRequest.model_validate(
        {
            "question": "Should we deny flood insurance to this individual?",
            "sources": [{"source_id": "one", "content": "Some evidence."}],
        }
    )
    orchestrator = build_orchestrator(runtime_name="deterministic")

    state = await orchestrator.run(request)

    assert state.status is TerminalStatus.BLOCKED
    assert state.synthesis is not None
    assert state.synthesis.safety_action == "LOCAL_POLICY_INTERVENED"


@pytest.mark.asyncio
async def test_token_budget_produces_partial_result() -> None:
    request = RunRequest.model_validate_json(EXAMPLE_MANIFEST.read_text(encoding="utf-8"))
    orchestrator = build_orchestrator(
        runtime_name="deterministic",
        policy=PipelinePolicy(token_budget=1),
    )

    state = await orchestrator.run(request, base_dir=EXAMPLE_MANIFEST.parent)

    assert state.status is TerminalStatus.PARTIAL
    assert state.synthesis is not None
    assert state.synthesis.partial
    assert "token_budget_exceeded" in (state.terminal_reason or "")
