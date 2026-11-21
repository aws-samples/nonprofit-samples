# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
"""Check the unchanged Synthesizer references without invoking a model."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from evidence_synthesis.models import AnalysisResult, SynthesisInput
from evidence_synthesis.validation import validate_analysis, validate_verification

ROOT = Path(__file__).resolve().parents[2]
FOLDER = Path(__file__).resolve().parent


def validate_input(payload: SynthesisInput) -> None:
    analysis = AnalysisResult(claims=[item.claim for item in payload.verification.claims])
    validate_analysis(analysis, payload.evidence)
    validate_verification(payload.verification, analysis, payload.evidence)
    if payload.partial or not payload.verification.citation_closure_complete:
        raise ValueError("These cases cover normal, citation-closed inputs")
    if not any(item.status.value != "rejected" for item in payload.verification.claims):
        raise ValueError("The pipeline skips generated synthesis for all-rejected inputs")


def validate_dataset(folder: Path = FOLDER, root: Path = ROOT) -> dict:
    references = json.loads((folder / "references.json").read_text())
    for name, expected in references["files"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve() / "evals"):
            raise ValueError("Reference path leaves evals")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Frozen reference changed: {name}")
    data = json.loads((folder / "dataset-v1.json").read_text())
    if [case["case_id"] for case in data["cases"]] != references["case_ids"]:
        raise ValueError("Frozen case selection changed")
    for case in data["cases"]:
        payload = SynthesisInput.model_validate(case["input"])
        validate_input(payload)
        if not case["expected"]["required_meanings"]:
            raise ValueError("Missing review requirements")
        for record in payload.evidence:
            if case["case_id"] == "S006":
                if not record.locator.startswith("synthetic:"):
                    raise ValueError("The fictional control must stay labeled synthetic")
                continue
            relative = record.locator.split("#", 1)[0]
            name = f"evals/verifier/{relative}"
            if name not in references["files"]:
                raise ValueError("Evidence references an unfrozen source")
            content = (root / name).read_text()
            if " ".join(record.excerpt.split()) not in " ".join(content.split()):
                raise ValueError("Evidence excerpt does not match its source")
    return {"validation": "passed", "cases_checked": len(data["cases"]), "model_calls": 0}


if __name__ == "__main__":
    print(json.dumps(validate_dataset(), indent=2))
