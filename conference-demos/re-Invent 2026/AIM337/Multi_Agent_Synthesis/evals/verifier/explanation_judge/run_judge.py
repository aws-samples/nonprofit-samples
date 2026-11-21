# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
"""Preview or calibrate an explanation judge against frozen human grades."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import math
import sys
import time
import tomllib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parents[3]
FOLDER = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from evidence_synthesis.config import StrandsSettings  # noqa: E402
from evidence_synthesis.models import AgentRole, EvidenceRecord, VerificationStatus  # noqa: E402
from evidence_synthesis.runtime import (  # noqa: E402
    AgentRuntime,
    RuntimeInvocationError,
    invoke_runtime,
)
from evidence_synthesis.strands_runtime import StrandsRuntime  # noqa: E402

Grade = Literal["pass", "fail", "needs_review"]
CHECKS = ("grounded", "complete", "supports_verdict")
RUBRIC_FILES = {"1.2": "rubric-v1.2.md"}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JudgeInput(StrictModel):
    question: str
    claim: str
    evidence: list[EvidenceRecord] = Field(min_length=1)
    verifier_verdict: VerificationStatus
    verifier_reason: str
    reason_requirements: list[str] = Field(min_length=1)


class OriginalClaim(StrictModel):
    claim_id: str = Field(min_length=1)
    statement: str = Field(min_length=1)


class ContextJudgeInput(JudgeInput):
    claim_id: str = Field(min_length=1)
    original_claims: list[OriginalClaim] = Field(min_length=1)

    @model_validator(mode="after")
    def check_claim_context(self) -> Self:
        claims = {claim.claim_id: claim.statement for claim in self.original_claims}
        if len(claims) != len(self.original_claims):
            raise ValueError("Original claim IDs must be unique.")
        if claims.get(self.claim_id) != self.claim:
            raise ValueError("The target claim must match its original claim entry.")
        return self


class EvidenceContextJudgeInput(ContextJudgeInput):
    # The evaluator identifies known omissions; the explanation cannot declare them.
    unavailable_evidence_ids: list[str]

    @model_validator(mode="after")
    def check_evidence_availability(self) -> Self:
        unavailable = set(self.unavailable_evidence_ids)
        if any(not item.strip() for item in unavailable):
            raise ValueError("Unavailable evidence IDs must not be empty.")
        if len(unavailable) != len(self.unavailable_evidence_ids):
            raise ValueError("Unavailable evidence IDs must be unique.")
        if unavailable & {item.evidence_id for item in self.evidence}:
            raise ValueError("Evidence cannot be both supplied and unavailable.")
        return self


class Check(StrictModel):
    grade: Grade
    reason: str = Field(min_length=1, max_length=1200)


class Judgment(StrictModel):
    grounded: Check
    complete: Check
    supports_verdict: Check


class ReferenceGrades(StrictModel):
    grounded: Grade
    complete: Grade
    supports_verdict: Grade
    overall: Grade


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def overall(grades: dict[str, str]) -> Grade:
    if "fail" in grades.values():
        return "fail"
    return "needs_review" if "needs_review" in grades.values() else "pass"


def judge_input(example: dict[str, Any]) -> JudgeInput:
    # Only this allowlisted payload reaches the judge; reference grades stay outside.
    if "unavailable_evidence_ids" in example["input"]:
        return EvidenceContextJudgeInput.model_validate(example["input"])
    payload_type = ContextJudgeInput if "original_claims" in example["input"] else JudgeInput
    return payload_type.model_validate(example["input"])


def load_calibration(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text())
    if data["status"] != "approved" or not data["examples"]:
        raise ValueError("The dataset must contain human-approved examples.")
    if data["rubric_version"] not in RUBRIC_FILES:
        raise ValueError("Unknown rubric version.")
    ids = []
    for example in data["examples"]:
        example_id = example["example_id"]
        if len(example_id) != 4 or example_id[0] != "J" or not example_id[1:].isdigit():
            raise ValueError("Example IDs must use J followed by three digits.")
        ids.append(example_id)
        if example["human_review"]["status"] != "approved":
            raise ValueError("Every selected reference grade must be approved.")
        payload = judge_input(example)
        if not isinstance(payload, EvidenceContextJudgeInput):
            raise ValueError("Rubric v1.2 requires explicit evidence availability.")
        digest = sha256(json.dumps(payload.model_dump(mode="json"), sort_keys=True).encode())
        if digest != example["input_sha256"]:
            raise ValueError("Calibration input hash mismatch.")
        reference = ReferenceGrades.model_validate(example["expected"])
        if reference.overall != overall(reference.model_dump(exclude={"overall"})):
            raise ValueError("Inconsistent reference overall grade.")
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate calibration example IDs.")
    return data


def compare(example: dict[str, Any], output: Judgment | None) -> dict[str, Any]:
    expected = example["expected"]
    actual = {key: getattr(output, key).grade for key in CHECKS} if output else {}
    actual_overall = overall(actual) if output else "unavailable"
    return {
        "checks": {
            key: {"expected": expected[key], "actual": actual.get(key, "unavailable"),
                  "matches": expected[key] == actual.get(key)}
            for key in CHECKS
        },
        "expected_overall": expected["overall"],
        "actual_overall": actual_overall,
        "overall_matches": expected["overall"] == actual_overall,
        "all_checks_match": bool(output) and all(expected[key] == actual[key] for key in CHECKS),
    }


def summarize(
    records: list[dict[str, Any]], planned: int, stop_reason: str | None,
) -> dict[str, Any]:
    scores = [r["scores"] for r in records]
    return {
        "run_status": "complete" if len(records) == planned and stop_reason is None else "partial",
        "stop_reason": stop_reason,
        "planned": planned, "attempted": len(records), "unattempted": planned - len(records),
        "returned": sum(r["outcome"] == "returned_output" for r in records),
        "overall_matches": sum(s["overall_matches"] for s in scores),
        "all_checks_match": sum(s["all_checks_match"] for s in scores),
        "per_check_matches": {
            key: sum(s["checks"][key]["matches"] for s in scores) for key in CHECKS
        },
        "false_passes": sum(s["expected_overall"] == "fail" and s["actual_overall"] == "pass"
                           for s in scores),
        "false_fails": sum(s["expected_overall"] == "pass" and s["actual_overall"] == "fail"
                          for s in scores),
        "needs_review": sum(s["actual_overall"] == "needs_review" for s in scores),
        "needs_review_checks": sum(s["checks"][key]["actual"] == "needs_review"
                                  for s in scores for key in CHECKS),
        "recorded_tokens": sum(r.get("metrics", {}).get("total_tokens", 0) for r in records),
        "token_usage_complete": all(r["token_usage_complete"] for r in records),
        "scope": "Agreement on selected calibration examples; not held-out judge accuracy.",
        "agreement_denominator": "Attempted judgments; failed attempts are nonmatches.",
    }


def save_results(folder: Path, records: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    write_json(folder / "summary.json", summary)
    lines = [
        "# Explanation judge calibration", "",
        f"Status: **{summary['run_status']}**. "
        f"Returned {summary['returned']} of {summary['attempted']} attempted judgments "
        f"({summary['planned']} planned).", "",
        f"Overall matches: {summary['overall_matches']}/{summary['attempted']}. "
        f"All-three-check matches: {summary['all_checks_match']}/{summary['attempted']}.", "",
        "Human grades were withheld from the model. Repeated trials are not independent "
        "new examples. These are calibration results, not held-out accuracy.", "",
    ]
    for record in records:
        lines += [
            f"## {record['example_id']} / trial {record['trial']}", "",
            f"Outcome: {record['outcome']}.", "",
            "| Check | Human | Judge | Judge reason |", "| --- | --- | --- | --- |",
        ]
        for key, score in record["scores"]["checks"].items():
            reason = record.get("output", {}).get(key, {}).get("reason", "(No output.)")
            reason = reason.replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {key} | {score['expected']} | {score['actual']} | {reason} |")
        lines.append("")
    (folder / "REPORT.md").write_text("\n".join(lines) + "\n")


async def run_trials(
    examples: list[dict[str, Any]], runtime: AgentRuntime, prompt: str, folder: Path,
    *, trials: int, timeout_seconds: float, token_budget: int,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    planned = len(examples) * trials
    stop_reason = None
    for trial in range(1, trials + 1):
        for example in examples:
            if sum(r.get("metrics", {}).get("total_tokens", 0) for r in records) >= token_budget:
                stop_reason = "token_budget_reached"
                break
            payload = judge_input(example)
            record: dict[str, Any] = {
                "example_id": example["example_id"], "trial": trial,
                "started_at": datetime.now(UTC).isoformat(),
                "input": payload.model_dump(mode="json"), "token_usage_complete": False,
            }
            started = time.perf_counter()
            output = None
            try:
                # Reuse the transport and its Verifier model slot with a separate prompt/schema.
                result = await asyncio.wait_for(
                    invoke_runtime(runtime, AgentRole.VERIFIER, payload, Judgment, prompt),
                    timeout=timeout_seconds,
                )
                output = Judgment.model_validate(result.output.model_dump())
                record.update(
                    outcome="returned_output", output=output.model_dump(mode="json"),
                    metrics=result.metrics.model_dump(mode="json"), token_usage_complete=True,
                )
            except RuntimeInvocationError as error:
                record.update(
                    outcome="model_error", error_type=type(error.__cause__ or error).__name__,
                    metrics=error.metrics.model_dump(mode="json"),
                    token_usage_complete=error.usage_complete,
                )
                stop_reason = "model_error"
            except TimeoutError:
                record.update(outcome="timeout", error_type="TimeoutError")
                stop_reason = "timeout_usage_unknown"
            except Exception as error:
                record.update(outcome="error", error_type=type(error).__name__)
                stop_reason = "unexpected_error_usage_unknown"
            record["wall_time_seconds"] = time.perf_counter() - started
            # Reference grades are consulted only after the model returns.
            record["scores"] = compare(example, output)
            records.append(record)
            write_json(folder / f"{example['example_id']}-trial-{trial}.json", record)
            save_results(folder, records, summarize(records, planned, stop_reason))
            if stop_reason:
                break
        if stop_reason:
            break
    summary = summarize(records, planned, stop_reason)
    save_results(folder, records, summary)
    return summary


def main() -> None:
    config = json.loads((FOLDER / "judge-config.json").read_text())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", type=Path, default=FOLDER / "calibration-v1.2.json",
        help="Approved dataset; its rubric_version selects the matching rubric.",
    )
    parser.add_argument("--example", action="append", dest="example_ids")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument(
        "--model-id", default=config["model_id"],
    )
    parser.add_argument("--region", default=config["region"])
    parser.add_argument("--max-tokens", type=int, default=config["max_tokens"])
    parser.add_argument("--token-budget", type=int, default=100_000)
    parser.add_argument("--timeout-seconds", type=float, default=config["timeout_seconds"])
    parser.add_argument("--execute", action="store_true", help="Make paid Bedrock model calls.")
    args = parser.parse_args()
    if (min(args.trials, args.max_tokens, args.token_budget) < 1
            or not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0
            or not args.model_id.strip()):
        parser.error("Limits must be positive and finite, and the model ID must not be empty.")
    data_path = args.dataset
    data = load_calibration(data_path)
    examples = data["examples"]
    if args.example_ids:
        if set(args.example_ids) - {e["example_id"] for e in examples}:
            parser.error("Unknown example ID.")
        examples = [e for e in examples if e["example_id"] in args.example_ids]
    prompt = (FOLDER / RUBRIC_FILES[data["rubric_version"]]).read_text().strip()
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    pinned = next(item.split("==")[1] for item in project["project"]["optional-dependencies"]["aws"]
                  if item.startswith("strands-agents"))
    installed = importlib.metadata.version("strands-agents")
    plan = {
        "mode": "execute" if args.execute else "preview", "operation": "explanation_judge",
        "example_ids": [e["example_id"] for e in examples], "trials": args.trials,
        "planned_judgments": len(examples) * args.trials,
        "model_id": args.model_id, "region": args.region, "max_tokens": args.max_tokens,
        "token_budget": args.token_budget, "timeout_seconds": args.timeout_seconds,
        "dataset_file": data_path.name,
        "dataset_version": data.get("dataset_version", data.get("calibration_version")),
        "rubric_version": data["rubric_version"],
        "calibration_sha256": sha256(data_path.read_bytes()),
        "prompt_sha256": sha256(prompt.encode()),
        "runner_sha256": sha256(Path(__file__).read_bytes()),
        "runtime_sha256": sha256((ROOT / "src/evidence_synthesis/strands_runtime.py").read_bytes()),
        "strands_version": installed, "pinned_strands_version": pinned,
        "ready_to_execute": installed == pinned, "aws_access_checked": False,
        "reference_grades_sent_to_model": False, "harness_retries": 0,
        "scope": "Explanation grading only; no Verifier, pipeline, Guardrail, or UI run.",
    }
    print(json.dumps(plan, indent=2))
    if not args.execute:
        return
    if not plan["ready_to_execute"]:
        parser.error("Use the project's pinned Strands version.")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    folder = ROOT / "runs" / f"explanation-judge-{stamp}-{uuid.uuid4().hex[:8]}"
    folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    write_json(folder / "manifest.json", plan)
    write_json(folder / "calibration.json", data)
    (folder / "system-prompt.txt").write_text(prompt + "\n")
    settings = StrandsSettings(
        region=args.region, verifier_model_id=args.model_id, verifier_max_tokens=args.max_tokens,
    )
    summary = asyncio.run(run_trials(
        examples, StrandsRuntime(settings), prompt, folder, trials=args.trials,
        timeout_seconds=args.timeout_seconds, token_budget=args.token_budget,
    ))
    print(json.dumps({"output_dir": str(folder), "summary": summary}, indent=2))
    if summary["run_status"] != "complete":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
