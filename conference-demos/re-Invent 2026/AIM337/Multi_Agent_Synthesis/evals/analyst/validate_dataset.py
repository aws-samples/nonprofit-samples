# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
"""Validate frozen Analyst references independently of the current application."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FOLDER = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from evidence_synthesis.models import AnalysisInput  # noqa: E402


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def canonical_hash(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate_dataset(folder: Path = FOLDER) -> dict[str, object]:
    folder = folder.resolve()
    dataset_bytes = (folder / "dataset.json").read_bytes()
    data = json.loads(dataset_bytes)
    version = data["dataset_version"]
    require(bool(re.fullmatch(r"\d+\.\d+", version)), "Invalid dataset version")
    freeze = json.loads((folder / f"versions/freeze-v{version}.json").read_text())
    require(sha256(dataset_bytes) == freeze["dataset_sha256"], "Frozen dataset changed")
    require(
        dataset_bytes == (folder / f"versions/dataset-v{version}.json").read_bytes(),
        "Dataset differs from its versioned snapshot",
    )
    manifest_bytes = (folder / "source-manifest.json").read_bytes()
    require(
        sha256(manifest_bytes) == freeze["source_manifest_sha256"],
        "Frozen source manifest changed",
    )
    require(
        manifest_bytes == (folder / f"versions/source-manifest-v{version}.json").read_bytes(),
        "Source manifest differs from its versioned snapshot",
    )
    manifest = json.loads(manifest_bytes)
    sources = {source["source_id"]: source for source in manifest["sources"]}
    require(len(sources) == len(manifest["sources"]), "Duplicate source IDs")
    for source in sources.values():
        path = (folder / source["path"]).resolve()
        require(path.is_relative_to(folder / "sources"), "Source path leaves snapshot folder")
        require(sha256(path.read_bytes()) == source["sha256"], "Frozen source changed")
        require(set(source.get("parents", [])).issubset(sources), "Unknown parent source")

    cases = {case["case_id"]: case for case in data["cases"]}
    require(len(cases) == len(data["cases"]), "Duplicate case IDs")
    require(list(cases) == freeze["case_ids"], "Frozen case selection changed")
    require(data["frozen_before_model_evaluation"], "References must precede model calls")
    for case_id, case in cases.items():
        require(bool(re.fullmatch(r"A\d{3}", case_id)), "Invalid case ID")
        require(case["review"]["status"] == "approved", "Case is not approved")
        approval_name = case["review"]["approval_record"]
        approval_path = (folder / approval_name).resolve()
        require(approval_path.is_relative_to(folder / "reviews"), "Invalid approval path")
        approval_bytes = approval_path.read_bytes()
        require(
            sha256(approval_bytes) == freeze["approval_record_sha256"][approval_name],
            "Approval record changed",
        )
        approval = json.loads(approval_bytes)
        require(approval["case_id"] == case_id, "Approval belongs to a different case")
        require(approval["decision"] == "approved", "Reference approval is missing")
        require(canonical_hash(case["input"]) == approval["input_sha256"], "Input changed")
        require(
            canonical_hash(case["expected"]) == approval["expected_sha256"],
            "Approved expectations changed",
        )
        payload = AnalysisInput.model_validate(case["input"])
        records = {item.evidence_id: item for item in payload.evidence}
        require(len(records) == len(payload.evidence), "Duplicate evidence IDs")
        for record in records.values():
            source = sources[record.source_id]
            require(record.locator.startswith(source["path"] + "#"), "Invalid source locator")
            content = (folder / source["path"]).read_text()
            require(
                " ".join(record.excerpt.split()) in " ".join(content.split()),
                "Evidence excerpt does not match the snapshot",
            )
        for profile in payload.source_profiles:
            for reference in profile.references:
                matching = [
                    record for record in records.values()
                    if record.source_id == profile.source_id and record.locator == reference.locator
                ]
                require(
                    any(" ".join(reference.excerpt.split()) in " ".join(r.excerpt.split())
                        for r in matching),
                    "Profile reference is not a supplied source excerpt",
                )
        facts = case["expected"]["required_facts"]
        require(bool(facts), "Required facts are missing")
        require(len({fact["id"] for fact in facts}) == len(facts), "Duplicate fact IDs")
        for fact in facts:
            require(bool(fact["meaning"].strip()), "Missing reference meaning")
            require(
                bool(fact["supported_by"]) and set(fact["supported_by"]).issubset(records),
                "Reference fact names unknown evidence",
            )
        for calculation in case.get("calculations", []):
            if calculation["operation"] == "subtract":
                left, right = map(Decimal, calculation["operands"])
                result = left - right
            elif calculation["operation"] == "percentage_change":
                earlier, recent = Decimal(calculation["earlier"]), Decimal(calculation["recent"])
                result = (recent - earlier) / earlier * 100
            else:
                raise ValueError("Unknown reference calculation")
            require(result == Decimal(calculation["exact"]), "Incorrect reference arithmetic")
        if "construction" in case:
            base = cases[case["construction"]["paired_case_id"]]
            require(
                canonical_hash(base["input"]) == case["construction"]["base_input_sha256"],
                "Paired control changed",
            )
            changed = {key for key in case["input"] if case["input"][key] != base["input"][key]}
            require(changed == {"source_profiles"}, "Profile challenge changed other inputs")
            require(
                facts == base["expected"]["required_facts"],
                "Profile challenge changed the required factual meanings",
            )

    prompt = (folder / "analyst-prompt-baseline.txt").read_text().strip()
    require(sha256(prompt.encode()) == freeze["analyst_prompt_sha256"], "Prompt snapshot changed")
    return {
        "validation": "passed", "dataset_version": version,
        "cases_checked": len(cases), "sources_checked": len(sources),
        "source_families": len({case["source_family"] for case in cases.values()}),
        "model_calls": 0,
        "note": "Integrity checks only; A008 contains a deliberately false profile.",
    }


if __name__ == "__main__":
    print(json.dumps(validate_dataset(), indent=2))
