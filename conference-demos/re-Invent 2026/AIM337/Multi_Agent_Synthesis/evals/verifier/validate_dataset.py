# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
"""Check reference-data consistency without invoking an agent or judge."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from evidence_synthesis.models import VerificationInput, VerificationStatus  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def normalized(text: str) -> str:
    return " ".join(text.split())


def validate_dataset(folder: Path, *, approved_only: bool = False) -> dict[str, object]:
    folder = folder.resolve()
    dataset = json.loads((folder / "dataset.json").read_text())
    manifest = json.loads((folder / "source-manifest.json").read_text())
    sources = {source["source_id"]: source for source in manifest["sources"]}
    require(len(sources) == len(manifest["sources"]), "Duplicate source IDs")

    for source in sources.values():
        path = (folder / source["path"]).resolve()
        require(path.is_relative_to(folder / "sources"), "Source path leaves the snapshot folder")
        require(path.is_file(), f"Missing source: {source['source_id']}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        require(actual == source["sha256"], f"Changed source: {source['source_id']}")
        require(set(source.get("parents", [])).issubset(sources), "Unknown parent snapshot")

    all_cases = dataset["cases"]
    require(len({case["case_id"] for case in all_cases}) == len(all_cases), "Duplicate case IDs")
    cases = [
        case for case in all_cases
        if not approved_only or case["review"]["status"] == "approved"
    ]
    require(bool(cases), "No cases selected")
    valid_statuses = {status.value for status in VerificationStatus}
    judgment_counts: Counter[str] = Counter()

    for case in cases:
        name = case["case_id"]
        require(
            case["review"]["status"] in {"approved", "pending"},
            f"{name}: invalid review status",
        )
        require(case["split"] == "learning_development", f"{name}: unexpected split")
        payload = VerificationInput.model_validate(case["input"])
        evidence_ids = {record.evidence_id for record in payload.evidence}
        require(len(evidence_ids) == len(payload.evidence), f"{name}: duplicate evidence IDs")

        for record in payload.evidence:
            require(record.source_id in sources, f"{name}: unknown source")
            source = sources[record.source_id]
            require(
                record.locator.startswith(source["path"] + "#"),
                f"{name}: locator does not match source",
            )
            text = (folder / source["path"]).read_text()
            require(
                normalized(record.excerpt) in normalized(text),
                f"{name}: excerpt does not occur in the frozen source",
            )

        claim_ids = {claim.claim_id for claim in payload.analysis.claims}
        require(len(claim_ids) == len(payload.analysis.claims), f"{name}: duplicate claim IDs")
        for claim in payload.analysis.claims:
            require(set(claim.evidence_ids).issubset(evidence_ids), f"{name}: unknown evidence ID")
            require(claim.confidence == 0.5, f"{name}: confidence differs from fixture policy")
        judgments = case["expected"]["judgments"]
        require(len(judgments) == len(claim_ids), f"{name}: missing or duplicate expectations")
        require(
            {j["claim_id"] for j in judgments} == claim_ids,
            f"{name}: expectation coverage mismatch",
        )
        for judgment in judgments:
            require(judgment["status"] in valid_statuses, f"{name}: invalid expected status")
            require(bool(judgment["rationale"].strip()), f"{name}: missing rationale")
            require(bool(judgment["reason_requirements"]), f"{name}: missing reason requirements")
            judgment_counts[judgment["status"]] += 1

        for calculation in case.get("calculations", []):
            operation = calculation["operation"]
            if operation == "sum":
                total = sum(Decimal(value) for value in calculation["operands"])
                require(total == Decimal(calculation["exact"]), f"{name}: incorrect sum")
            elif operation == "divide":
                left, right = (Decimal(value) for value in calculation["operands"])
                quantum = Decimal(1).scaleb(-calculation["decimal_places"])
                require(
                    (left / right).quantize(quantum) == Decimal(calculation["rounded"]),
                    f"{name}: incorrect ratio",
                )
            elif operation == "interval":
                estimate = Decimal(calculation["estimate"])
                moe = Decimal(calculation["moe"])
                require(
                    estimate - moe == Decimal(calculation["lower"]),
                    f"{name}: wrong lower endpoint",
                )
                require(
                    estimate + moe == Decimal(calculation["upper"]),
                    f"{name}: wrong upper endpoint",
                )
            else:
                raise ValueError(f"{name}: unknown calculation")

    return {
        "validation": "passed",
        "cases_checked": len(cases),
        "claims_checked": sum(judgment_counts.values()),
        "sources_checked": len(sources),
        "review_statuses": dict(Counter(case["review"]["status"] for case in cases)),
        "proposed_verdicts": dict(judgment_counts),
        "model_calls": 0,
        "note": "Consistency checks do not certify the semantic labels.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved-only", action="store_true")
    args = parser.parse_args()
    result = validate_dataset(Path(__file__).resolve().parent, approved_only=args.approved_only)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
