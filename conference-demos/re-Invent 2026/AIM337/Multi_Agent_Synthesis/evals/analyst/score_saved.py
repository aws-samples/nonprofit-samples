# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
"""Apply the reviewed Analyst rubric to saved stage outputs; preview by default."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from run_stage import ROOT, digest, load_cases, write_json  # noqa: E402

from evidence_synthesis.config import StrandsSettings  # noqa: E402
from evidence_synthesis.models import AgentRole, AnalysisInput, AnalysisResult  # noqa: E402
from evidence_synthesis.runtime import RuntimeInvocationError  # noqa: E402
from evidence_synthesis.strands_runtime import StrandsRuntime  # noqa: E402
from evidence_synthesis.validation import ContractError, validate_analysis  # noqa: E402

FOLDER = Path(__file__).resolve().parent

Grade = Literal["pass", "fail", "needs_review"]
CHECKS = ("grounded", "citation_support", "coverage", "scope_uncertainty", "gap_handoff")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Requirements(StrictModel):
    required_facts: list[str] = Field(min_length=1)
    scope_requirements: list[str]
    unsupported_conclusions: list[str]
    citation_requirements: list[str]
    coverage_policy: str
    additional_claims_policy: str
    evidence_gaps_policy: str
    requires_evidence_gaps: bool


class JudgeInput(StrictModel):
    analysis_input: AnalysisInput
    analyst_output: AnalysisResult
    requirements: Requirements


class Check(StrictModel):
    grade: Grade
    reason: str = Field(min_length=1, max_length=1600)


class Judgment(StrictModel):
    grounded: Check
    citation_support: Check
    coverage: Check
    scope_uncertainty: Check
    gap_handoff: Check


def make_input(
    case: dict[str, Any], output: dict[str, Any], *, requires_gaps: bool,
) -> JudgeInput:
    expected = case["expected"]
    return JudgeInput(
        analysis_input=AnalysisInput.model_validate(case["input"]),
        analyst_output=AnalysisResult.model_validate(output),
        requirements=Requirements(
            required_facts=[fact["meaning"] for fact in expected["required_facts"]],
            **{key: expected[key] for key in (
                "scope_requirements", "unsupported_conclusions", "citation_requirements",
                "coverage_policy", "additional_claims_policy", "evidence_gaps_policy",
            )},
            requires_evidence_gaps=requires_gaps,
        ),
    )


def overall(grades: list[str]) -> Grade:
    if "fail" in grades:
        return "fail"
    return "needs_review" if "needs_review" in grades else "pass"


def mechanical_failures(payload: JudgeInput) -> list[str]:
    failures = []
    try:
        validate_analysis(payload.analyst_output, payload.analysis_input.evidence)
    except ContractError as error:
        failures.append(str(error))
    if not payload.analyst_output.claims:
        failures.append("empty_claims")
    if payload.requirements.requires_evidence_gaps and not any(
        gap.strip() for gap in payload.analyst_output.evidence_gaps
    ):
        failures.append("required_evidence_gaps_empty")
    return failures


def load_saved(folder: Path, config: dict) -> list[dict]:
    manifest = json.loads((folder / "manifest.json").read_text())
    if manifest.get("stage") != "analyst":
        raise ValueError("Choose an Analyst run produced by evals/run_stage.py")
    cases = {case["case_id"]: case for case in load_cases("analyst")}
    saved_cases = json.loads((folder / "cases.json").read_text())
    if any(case != cases.get(case["case_id"]) for case in saved_cases):
        raise ValueError("Saved references differ from the published dataset")
    selected_ids = {case["case_id"] for case in saved_cases}
    expected_hash = digest((FOLDER / "dataset.json").read_bytes())
    if manifest.get("dataset_sha256") != expected_hash:
        raise ValueError("Saved dataset hash differs from the published dataset")
    records = []
    for path in sorted(folder.glob("A[0-9][0-9][0-9]-trial-*.json")):
        row = json.loads(path.read_text())
        if row["case_id"] not in selected_ids:
            raise ValueError("Output case was not selected in this run")
        case = cases[row["case_id"]]
        if row["input"] != AnalysisInput.model_validate(case["input"]).model_dump(mode="json"):
            raise ValueError("Saved model input differs from its reference case")
        if "output" not in row:
            continue
        payload = make_input(
            case, row["output"],
            requires_gaps=case["case_id"] in config["cases_requiring_gap_handoff"],
        )
        records.append({
            "source_file": path.name, "source_sha256": digest(path.read_bytes()),
            "payload": payload,
        })
    if not records:
        raise ValueError("No returned Analyst outputs to score")
    return records


async def score_records(records: list[dict], runtime, prompt: str, folder: Path,
                        *, timeout: float, token_budget: int) -> dict:
    if not records or token_budget <= 0:
        raise ValueError("Provide saved outputs and a positive token budget")
    results = []
    stop = None
    for item in records:
        if sum(row.get("metrics", {}).get("total_tokens", 0) for row in results) >= token_budget:
            stop = "token_budget_reached"
            break
        payload = item["payload"]
        row = {
            "source_file": item["source_file"], "source_sha256": item["source_sha256"],
            "input": payload.model_dump(mode="json"),
            "mechanical_failures": mechanical_failures(payload), "token_usage_complete": False,
        }
        path = folder / item["source_file"]
        write_json(path, {**row, "outcome": "started"})
        try:
            result = await asyncio.wait_for(runtime.invoke_async(
                AgentRole.ANALYST, payload, Judgment, prompt,
            ), timeout=timeout)
            grade = overall([getattr(result.output, name).grade for name in CHECKS])
            row.update(
                outcome="returned_output", judgment=result.output.model_dump(mode="json"),
                llm_grade=grade, combined_grade="fail" if row["mechanical_failures"] else grade,
                metrics=result.metrics.model_dump(mode="json"), token_usage_complete=True,
            )
        except RuntimeInvocationError as error:
            row.update(
                outcome="model_error", error_type=type(error.__cause__ or error).__name__,
                metrics=error.metrics.model_dump(mode="json"),
                token_usage_complete=error.usage_complete,
            )
            stop = "model_error"
        except TimeoutError:
            row.update(outcome="timeout")
            stop = "timeout_usage_unknown"
        except Exception as error:
            row.update(outcome="error", error_type=type(error).__name__)
            stop = "unexpected_error_usage_unknown"
        results.append(row)
        write_json(path, row)
        summary = {
            "status": "complete" if len(results) == len(records) and stop is None else "partial",
            "stop_reason": stop, "planned": len(records), "attempted": len(results),
            "unattempted": len(records) - len(results),
            "recorded_tokens": sum(r.get("metrics", {}).get("total_tokens", 0) for r in results),
            "token_usage_complete": all(r["token_usage_complete"] for r in results),
            "llm_grades": {g: sum(r.get("llm_grade") == g for r in results)
                           for g in ("pass", "fail", "needs_review")},
            "combined_grades": {g: sum(r.get("combined_grade") == g for r in results)
                                for g in ("pass", "fail", "needs_review")},
            "scope": "Assisted grading only; no human labels or accuracy estimate created.",
        }
        write_json(folder / "summary.json", summary)
        if stop:
            break
    # A budget reached between calls still needs a final, accurate stop reason.
    summary["stop_reason"] = stop
    summary["status"] = "complete" if len(results) == len(records) and stop is None else "partial"
    write_json(folder / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--region")
    parser.add_argument("--model-id")
    parser.add_argument("--token-budget", type=int, default=100_000)
    parser.add_argument("--execute", action="store_true", help="Make paid judge-model calls.")
    args = parser.parse_args()
    if args.token_budget <= 0:
        parser.error("Token budget must be positive.")
    config = json.loads((FOLDER / "judge-settings.json").read_text())
    prompt = (FOLDER / config["rubric_file"]).read_text().strip()
    if digest(prompt.encode()) != config["prompt_sha256"]:
        parser.error("Reviewed rubric changed; version its settings before reuse.")
    records = load_saved(args.run_directory.resolve(), config)
    model = args.model_id or config["model_id"]
    region = args.region or config["region"]
    plan = {
        "execute": args.execute, "stage": "analyst_judge", "model_id": model, "region": region,
        "max_tokens": config["max_tokens"], "timeout_seconds": config["timeout_seconds"],
        "token_budget": args.token_budget, "planned_judge_invocations": len(records),
        "strands_version": importlib.metadata.version("strands-agents"),
        "rubric_sha256": config["prompt_sha256"],
        "scorer_sha256": digest(Path(__file__).read_bytes()),
        "source_manifest_sha256": digest((args.run_directory / "manifest.json").read_bytes()),
        "reference_grades_sent_to_judge": False,
    }
    print(json.dumps(plan, indent=2), flush=True)
    if not args.execute:
        return
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    folder = ROOT / "runs" / f"analyst-judge-{stamp}-{uuid.uuid4().hex[:8]}"
    folder.mkdir(mode=0o700, parents=True, exist_ok=False)
    write_json(folder / "manifest.json", plan)
    (folder / "rubric.md").write_text(prompt + "\n")
    settings = StrandsSettings(
        _env_file=None, region=region, analyst_model_id=model,
        analyst_max_tokens=config["max_tokens"],
    )
    summary = asyncio.run(score_records(
        records, StrandsRuntime(settings), prompt, folder,
        timeout=config["timeout_seconds"], token_budget=args.token_budget,
    ))
    print(json.dumps({"output_dir": str(folder), "summary": summary}, indent=2))
    if summary["status"] != "complete":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
