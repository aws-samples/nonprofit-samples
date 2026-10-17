# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel

from evidence_synthesis.agents import EvidenceExtractorAgent
from evidence_synthesis.manifest_generator import generate_local_from_folder
from evidence_synthesis.models import (
    AgentRole,
    EvidenceExtractorInput,
    EvidenceExtractorResult,
    EvidenceRecord,
    InvocationMetrics,
    RunRequest,
)
from evidence_synthesis.runtime import RuntimeResult
from evidence_synthesis.source_loader import SourceLoader

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_MANIFEST = PROJECT_ROOT / "examples" / "coastal-resilience" / "manifest.json"


@pytest.mark.parametrize("generated", [False, True])
def test_coastal_fixture_does_not_supply_stance_labels(generated: bool) -> None:
    request = RunRequest.model_validate_json(EXAMPLE_MANIFEST.read_text(encoding="utf-8"))
    if generated:
        request = generate_local_from_folder(
            folder=EXAMPLE_MANIFEST.parent,
            question=request.question,
            max_evidence=request.max_evidence,
        )

    assert len(request.sources) == 4
    assert {Path(source.uri or "").suffix for source in request.sources} == {
        ".pdf", ".csv", ".json", ".md",
    }
    assert not (EXAMPLE_MANIFEST.parent / "mapped-risk.md").exists()
    assert all("stance" not in source.metadata for source in request.sources)
    chunks = SourceLoader().load_all(request.sources, EXAMPLE_MANIFEST.parent)
    assert len(chunks) == 6
    assert all("stance" not in chunk.metadata for chunk in chunks)
    assert all('"stance"' not in chunk.content for chunk in chunks)


class SparseEvidenceExtractorRuntime:
    def invoke[OutputT: BaseModel](
        self,
        role: AgentRole,
        payload: BaseModel,
        output_type: type[OutputT],
        system_prompt: str,
    ) -> RuntimeResult[OutputT]:
        del system_prompt
        assert role is AgentRole.EVIDENCE_EXTRACTOR
        extraction = cast(EvidenceExtractorInput, payload)
        first = extraction.chunks[0]
        output = EvidenceExtractorResult(
            records=[
                EvidenceRecord(
                    evidence_id=first.chunk_id,
                    source_id=first.source_id,
                    title=first.title,
                    excerpt=first.content,
                    locator=first.locator,
                    methodology=first.methodology,
                    relevance=1,
                    metadata=first.metadata,
                )
            ]
        )
        return RuntimeResult(
            output=output_type.model_validate(output),
            metrics=InvocationMetrics(
                role=role,
                model_id="sparse-test-runtime",
                latency_ms=1,
            ),
        )


@pytest.mark.asyncio
async def test_evidence_extractor_backfills_omitted_source_chunks() -> None:
    request = RunRequest.model_validate_json(EXAMPLE_MANIFEST.read_text(encoding="utf-8"))
    agent = EvidenceExtractorAgent(SparseEvidenceExtractorRuntime(), SourceLoader())

    result = await agent.extract(request, EXAMPLE_MANIFEST.parent)

    assert len(result.output.records) == 6
    assert {record.source_id for record in result.output.records} == {
        "mapped-risk",
        "claims-history",
        "water-levels",
        "field-notes",
    }
    assert {record.locator for record in result.output.records} == {
        str((EXAMPLE_MANIFEST.parent / "mapped-risk.pdf").resolve()) + "#page=1",
        str((EXAMPLE_MANIFEST.parent / "claims.csv").resolve()) + "#row=2",
        str((EXAMPLE_MANIFEST.parent / "claims.csv").resolve()) + "#row=3",
        str((EXAMPLE_MANIFEST.parent / "water-levels.json").resolve()) + "#item=1",
        str((EXAMPLE_MANIFEST.parent / "water-levels.json").resolve()) + "#item=2",
        str((EXAMPLE_MANIFEST.parent / "field-notes.md").resolve()) + "#paragraph=1",
    }


@pytest.mark.asyncio
async def test_evidence_extractor_preserves_source_diversity_when_budget_is_limited() -> None:
    request = RunRequest(
        question="What evidence is supplied?",
        max_evidence=2,
        sources=[
            {"source_id": "one", "content": "First source."},
            {"source_id": "two", "content": "Second source."},
            {"source_id": "three", "content": "Third source."},
        ],
    )
    agent = EvidenceExtractorAgent(SparseEvidenceExtractorRuntime(), SourceLoader())

    result = await agent.extract(request, Path.cwd())

    assert len(result.output.records) == 2
    assert len({record.source_id for record in result.output.records}) == 2
    assert any("Evidence budget retained 2 of 3" in gap for gap in result.output.gaps)
    assert any("Evidence budget could not represent sources" in gap for gap in result.output.gaps)
