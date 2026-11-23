# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
"""Grade explanations from a completed, contract-valid saved Verifier run."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import math
import statistics
import time
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from run_judge import (
    CHECKS,
    FOLDER,
    ROOT,
    EvidenceContextJudgeInput,
    Judgment,
    OriginalClaim,
    overall,
    sha256,
    write_json,
)

from evidence_synthesis.config import StrandsSettings
from evidence_synthesis.models import AgentRole, VerificationInput, VerificationResult
from evidence_synthesis.runtime import AgentRuntime, RuntimeInvocationError, invoke_runtime
from evidence_synthesis.strands_runtime import StrandsRuntime
from evidence_synthesis.validation import validate_verification


def prepare_reviews(source: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Keep reference verdicts outside the allowlisted judge payload."""
    hashes: dict[str, str] = {}

    def read(name: str) -> Any:
        content = (source / name).read_bytes()
        hashes[name] = sha256(content)
        return json.loads(content)

    manifest = read("manifest.json")
    baseline = read("summary.json")
    cases = read("cases.json")
    prompt = (source / "system-prompt.txt").read_bytes()
    hashes["system-prompt.txt"] = sha256(prompt)
    if sha256(prompt.decode().strip().encode()) != manifest["prompt_sha256"]:
        raise ValueError("Saved Verifier prompt hash mismatch.")
    if baseline["run_status"] != "complete":
        raise ValueError("This scorer requires a completed saved run.")
    if [case["case_id"] for case in cases] != manifest["case_ids"]:
        raise ValueError("Saved case list differs from its manifest.")

    reviews: list[dict[str, Any]] = []
    source_records: list[dict[str, Any]] = []
    for trial in range(1, manifest["trials_per_case"] + 1):
        for case in cases:
            case_id = case["case_id"]
            if len(case_id) != 4 or case_id[0] != "V" or not case_id[1:].isdigit():
                raise ValueError("Saved case IDs must use V followed by three digits.")
            if case["review"]["status"] != "approved":
                raise ValueError("Reference verdicts and criteria must be human-approved.")
            saved = read(f"{case_id}-trial-{trial}.json")
            if saved["case_id"] != case_id or saved["trial"] != trial:
                raise ValueError("Saved trial identity mismatch.")
            if sha256(json.dumps(case["input"], sort_keys=True).encode()) != saved["input_sha256"]:
                raise ValueError("Saved Verifier input hash mismatch.")
            if saved["outcome"] != "returned_output" or not saved["scores"]["contract_valid"]:
                raise ValueError("This scorer requires contract-valid saved outputs.")
            payload = VerificationInput.model_validate(case["input"])
            result = VerificationResult.model_validate(saved["output"])
            validate_verification(result, payload.analysis, payload.evidence)
            source_records.append({
                "case_id": case_id, "trial": trial, "metrics": saved["metrics"],
                "wall_time_seconds": saved["wall_time_seconds"],
                "token_usage_complete": saved["token_usage_complete"],
            })
            references = {item["claim_id"]: item for item in case["expected"]["judgments"]}
            recorded_scores = {item["claim_id"]: item for item in saved["scores"]["claims"]}
            if set(references) != {claim.claim_id for claim in payload.analysis.claims}:
                raise ValueError("Reference coverage differs from the original claims.")
            for reviewed in result.claims:
                claim_id = reviewed.claim.claim_id
                reference = references[claim_id]
                matches = reviewed.status.value == reference["status"]
                old_score = recorded_scores[claim_id]
                if (old_score["matches"] != matches
                        or old_score["actual_reason"] != reviewed.reason
                        or old_score["reason_requirements"] != reference["reason_requirements"]):
                    raise ValueError("Saved score differs from the captured output or criteria.")
                # All original case evidence is available; no excerpts are silently removed.
                judge_payload = EvidenceContextJudgeInput(
                    question=payload.question, claim=reviewed.claim.statement,
                    evidence=payload.evidence, verifier_verdict=reviewed.status,
                    verifier_reason=reviewed.reason,
                    reason_requirements=reference["reason_requirements"], claim_id=claim_id,
                    original_claims=[
                        OriginalClaim(claim_id=c.claim_id, statement=c.statement)
                        for c in payload.analysis.claims
                    ],
                    unavailable_evidence_ids=[],
                ).model_dump(mode="json")
                reviews.append({
                    "review_id": f"R{len(reviews) + 1:04d}", "case_id": case_id,
                    "verifier_trial": trial, "claim_id": claim_id,
                    "reference_verdict": reference["status"],
                    "returned_verdict": reviewed.status.value,
                    "verdict_matches_reference": matches,
                    "input": judge_payload,
                    "input_sha256": sha256(json.dumps(judge_payload, sort_keys=True).encode()),
                })
    if (len(source_records) != baseline["trials_attempted"]
            or len(source_records) != baseline["trials_with_valid_contract"]
            or len(reviews) != baseline["claims_attempted"]
            or sum(r["verdict_matches_reference"] for r in reviews) != baseline["labels_matching"]):
        raise ValueError("Saved aggregate counts do not match the captured records.")
    return reviews, {
        "source_run": str(source.resolve()), "manifest": manifest, "baseline": baseline,
        "source_hashes": hashes, "verifier_trials": source_records,
        "verifier_median_wall_seconds": statistics.median(
            r["wall_time_seconds"] for r in source_records
        ),
    }


def summarize(records: list[dict[str, Any]], planned: int, stop: str | None) -> dict[str, Any]:
    returned = [r for r in records if r["outcome"] == "returned_output"]
    return {
        "run_status": "complete" if len(returned) == planned and stop is None else "partial",
        "stop_reason": stop, "planned_reviews": planned, "attempted": len(records),
        "returned": len(returned), "unattempted": planned - len(records),
        "explanation_grades": dict(Counter(r["overall_grade"] for r in returned)),
        "per_check_grades": {
            k: dict(Counter(r["output"][k]["grade"] for r in returned)) for k in CHECKS
        },
        "verdict_and_explanation_pass": sum(
            r["verdict_matches_reference"] and r["overall_grade"] == "pass" for r in returned
        ),
        "wrong_verdict_with_passing_explanation": sum(
            not r["verdict_matches_reference"] and r["overall_grade"] == "pass" for r in returned
        ),
        "review_queue": [
            r["review_id"] for r in records
            if r.get("overall_grade") != "pass" or not r["verdict_matches_reference"]
        ],
        "judge_recorded_tokens": sum(r.get("metrics", {}).get("total_tokens", 0) for r in records),
        "judge_token_usage_complete": all(r["token_usage_complete"] for r in records),
        "judge_median_wall_seconds": (
            statistics.median(r["wall_time_seconds"] for r in returned) if returned else None
        ),
        "scope": "Judge-assigned explanation grades, not human explanation accuracy.",
    }


async def score_reviews(
    reviews: list[dict[str, Any]], runtime: AgentRuntime, prompt: str, folder: Path,
    *, timeout_seconds: float, token_budget: int,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    stop = None
    for review in reviews:
        if sum(r.get("metrics", {}).get("total_tokens", 0) for r in records) >= token_budget:
            stop = "token_budget_reached"
            break
        record = {**review, "started_at": datetime.now(UTC).isoformat(),
                  "token_usage_complete": False}
        started = time.perf_counter()
        try:
            payload = EvidenceContextJudgeInput.model_validate(review["input"])
            result = await asyncio.wait_for(
                invoke_runtime(runtime, AgentRole.VERIFIER, payload, Judgment, prompt),
                timeout=timeout_seconds,
            )
            output = Judgment.model_validate(result.output.model_dump())
            record.update(
                outcome="returned_output", output=output.model_dump(mode="json"),
                overall_grade=overall({k: getattr(output, k).grade for k in CHECKS}),
                metrics=result.metrics.model_dump(mode="json"), token_usage_complete=True,
            )
        except RuntimeInvocationError as error:
            record.update(
                outcome="model_error", error_type=type(error.__cause__ or error).__name__,
                metrics=error.metrics.model_dump(mode="json"),
                token_usage_complete=error.usage_complete,
            )
            stop = "model_error"
        except TimeoutError:
            record.update(outcome="timeout", error_type="TimeoutError")
            stop = "timeout_usage_unknown"
        except Exception as error:
            record.update(outcome="error", error_type=type(error).__name__)
            stop = "unexpected_error_usage_unknown"
        record["wall_time_seconds"] = time.perf_counter() - started
        records.append(record)
        write_json(folder / f"{review['review_id']}.json", record)
        write_json(folder / "summary.json", summarize(records, len(reviews), stop))
        print(f"{len(records)}/{len(reviews)} {review['review_id']} "
              f"{record.get('overall_grade', record['outcome'])}", flush=True)
        if stop:
            break
    summary = summarize(records, len(reviews), stop)
    write_json(folder / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-run", type=Path, required=True,
        help="Completed Verifier run directory printed by run_baseline.py.",
    )
    parser.add_argument("--token-budget", type=int, default=600_000)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.token_budget < 1:
        parser.error("The judge token budget must be positive.")
    lock = json.loads((FOLDER / "judge-config.json").read_text())
    prompt = (FOLDER / "rubric-v1.2.md").read_text().strip()
    if (sha256(prompt.encode()) != lock["prompt_sha256"]
            or sha256((FOLDER / "run_judge.py").read_bytes()) != lock["runner_sha256"]):
        parser.error("The judge changed; review and update judge-config.json before scoring.")
    if not math.isfinite(lock["timeout_seconds"]) or lock["timeout_seconds"] <= 0:
        parser.error("The locked timeout must be positive and finite.")
    reviews, source = prepare_reviews(args.source_run)
    installed = importlib.metadata.version("strands-agents")
    plan = {
        "mode": "execute" if args.execute else "preview",
        "operation": "score_saved_verifier_explanations",
        "source_run": source["source_run"], "planned_judge_calls": len(reviews),
        "judge_trials_per_saved_review": 1, "new_verifier_calls": 0,
        "judge_lock": lock, "judge_batch_token_budget": args.token_budget,
        "strands_version": installed,
        "ready_to_execute": installed == lock["strands_version"],
        "scorer_sha256": sha256(Path(__file__).read_bytes()),
        "runtime_sha256": sha256((ROOT / "src/evidence_synthesis/strands_runtime.py").read_bytes()),
        "reference_verdicts_sent_to_judge": False,
        "human_explanation_grades_sent_to_judge": False,
        "review_policy": (
            "Inspect every flag and one passing review per case, "
            "selected by sorting sha256(review_id)."
        ),
    }
    if plan["runtime_sha256"] != lock["runtime_sha256"]:
        parser.error("The runtime differs from the validated judge run.")
    print(json.dumps(plan, indent=2), flush=True)
    if not args.execute:
        return
    if not plan["ready_to_execute"]:
        parser.error("Use the validated Strands version.")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    folder = ROOT / "runs" / f"verifier-scorecard-{stamp}-{uuid.uuid4().hex[:8]}"
    folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    write_json(folder / "manifest.json", plan)
    write_json(folder / "source.json", source)
    write_json(folder / "reviews.json", reviews)
    (folder / "system-prompt.txt").write_text(prompt + "\n")
    summary = asyncio.run(score_reviews(
        reviews,
        StrandsRuntime(StrandsSettings(
            region=lock["region"], verifier_model_id=lock["model_id"],
            verifier_max_tokens=lock["max_tokens"],
        )),
        prompt, folder, timeout_seconds=lock["timeout_seconds"], token_budget=args.token_budget,
    ))
    print(json.dumps({"output_dir": str(folder), "summary": summary}, indent=2))
    if summary["run_status"] != "complete":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
