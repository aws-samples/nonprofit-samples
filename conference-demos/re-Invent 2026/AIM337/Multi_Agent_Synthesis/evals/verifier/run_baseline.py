# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
"""Preview or run a Verifier baseline; explanations remain for human review."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import sys
import time
import tomllib
import uuid
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FOLDER = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from validate_dataset import validate_dataset  # noqa: E402

from evidence_synthesis.agents.verifier import VerifierAgent  # noqa: E402
from evidence_synthesis.config import StrandsSettings  # noqa: E402
from evidence_synthesis.models import VerificationInput, VerificationResult  # noqa: E402
from evidence_synthesis.runtime import RuntimeInvocationError  # noqa: E402
from evidence_synthesis.strands_runtime import StrandsRuntime  # noqa: E402
from evidence_synthesis.validation import ContractError, validate_verification  # noqa: E402


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def create_verifier(
    settings: StrandsSettings, system_prompt: str, diagnostics: bool = False,
) -> VerifierAgent:
    agent: VerifierAgent
    if diagnostics:
        from diagnostics import ObservedVerifier

        agent = ObservedVerifier(StrandsRuntime(settings))
    else:
        agent = VerifierAgent(StrandsRuntime(settings))
    # The override belongs to this evaluation instance, not the application class.
    agent.system_prompt = system_prompt
    return agent


def select_cases(
    dataset: dict[str, Any], requested: list[str] | None, approved_only: bool,
) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = dataset["cases"]
    if requested:
        unknown = set(requested) - {case["case_id"] for case in cases}
        if unknown:
            raise ValueError(f"Unknown cases: {sorted(unknown)}")
        cases = [case for case in cases if case["case_id"] in requested]
    if approved_only:
        cases = [case for case in cases if case["review"]["status"] == "approved"]
    if not cases:
        raise ValueError("No cases selected")
    return cases


def score_labels(
    case: dict[str, Any], output: VerificationResult | None,
) -> dict[str, Any]:
    """Score status labels and the existing structural contract, not explanation quality."""
    payload = VerificationInput.model_validate(case["input"])
    contract_error = "no_output" if output is None else None
    if output is not None:
        try:
            validate_verification(output, payload.analysis, payload.evidence)
        except ContractError as error:
            contract_error = str(error)
    actual = {
        item.claim.claim_id: item
        for item in output.claims
    } if output is not None and contract_error is None else {}
    claims = []
    for expected in case["expected"]["judgments"]:
        item = actual.get(expected["claim_id"])
        observed = item.status.value if item is not None else "invalid_or_missing_output"
        claims.append({
            "claim_id": expected["claim_id"],
            "expected": expected["status"],
            "actual": observed,
            "matches": observed == expected["status"],
            "actual_reason": item.reason if item is not None else None,
            "reference_rationale": expected["rationale"],
            "reason_requirements": expected["reason_requirements"],
            "explanation_review": "pending" if item is not None else "not_available",
        })
    return {
        "contract_valid": contract_error is None,
        "contract_error": contract_error,
        "all_labels_match": all(claim["matches"] for claim in claims),
        "claims": claims,
    }


def summarize(
    records: list[dict[str, Any]], planned: int, stop_reason: str | None,
) -> dict[str, Any]:
    claims = [claim for record in records for claim in record["scores"]["claims"]]
    matches = sum(claim["matches"] for claim in claims)
    confusion = Counter((claim["expected"], claim["actual"]) for claim in claims)
    return {
        "run_status": "complete" if len(records) == planned and stop_reason is None else "partial",
        "stop_reason": stop_reason,
        "trials_planned": planned,
        "trials_attempted": len(records),
        "trials_unattempted": planned - len(records),
        "trials_with_valid_contract": sum(r["scores"]["contract_valid"] for r in records),
        "trials_with_all_labels_matching": sum(r["scores"]["all_labels_match"] for r in records),
        "claims_attempted": len(claims),
        "labels_matching": matches,
        "label_accuracy_on_attempted_claims": matches / len(claims) if claims else None,
        "recorded_total_tokens": sum(r.get("metrics", {}).get("total_tokens", 0) for r in records),
        "token_usage_complete": all(r["token_usage_complete"] for r in records),
        "confusion_matrix": [
            {"expected": expected, "actual": actual, "count": count}
            for (expected, actual), count in sorted(confusion.items())
        ],
        "explanation_quality": "not_scored; human review required",
        "cost": "not_calculated; token totals are not a monetary cost estimate",
    }


def write_report(folder: Path, summary: dict[str, Any], records: list[dict[str, Any]]) -> None:
    write_json(folder / "summary.json", summary)
    lines = [
        "# Verifier baseline - manual explanation review", "",
        f"Run status: **{summary['run_status']}**. "
        f"Attempted {summary['trials_attempted']} of {summary['trials_planned']} trials.", "",
        f"Matched {summary['labels_matching']} of {summary['claims_attempted']} attempted "
        "claim labels. Invalid or failed outputs count as nonmatches. "
        "Unattempted trials are reported separately.", "",
        "**This is label agreement, not an overall quality or explanation score.** "
        "Read the actual reasons alongside the reference requirements.", "",
    ]
    for record in records:
        lines += [
            f"## {record['case_id']} / trial {record['trial']}", "",
            f"Outcome: `{record['outcome']}`. "
            f"Contract error: `{record['scores']['contract_error']}`.", "",
        ]
        for claim in record["scores"]["claims"]:
            lines += [
                f"### {claim['claim_id']}: expected {claim['expected']}, actual {claim['actual']}",
                "", "**Actual reason:**", "", claim["actual_reason"] or "(No valid output.)",
                "", "**Reference rationale:**", "", claim["reference_rationale"],
                "", "**Check these meanings, not exact words:** "
                + "; ".join(claim["reason_requirements"]),
                "", "**Human explanation review:** Pending.", "",
            ]
    (folder / "REVIEW_RESULTS.md").write_text("\n".join(lines) + "\n")


async def run_trials(
    cases: list[dict[str, Any]], agent: VerifierAgent, output_dir: Path,
    *, trials: int, timeout_seconds: float, token_budget: int,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    planned = len(cases) * trials
    stop_reason = None
    for trial in range(1, trials + 1):
        for case in cases:
            used = sum(r.get("metrics", {}).get("total_tokens", 0) for r in records)
            if used >= token_budget:
                stop_reason = "evaluation_token_budget_reached"
                break
            record: dict[str, Any] = {
                "case_id": case["case_id"], "trial": trial,
                "started_at": datetime.now(UTC).isoformat(),
                "input_sha256": sha256(json.dumps(case["input"], sort_keys=True).encode()),
                "token_usage_complete": False,
            }
            started = time.perf_counter()
            output = None
            try:
                # The typed payload excludes all reference labels and reviewer notes.
                payload = VerificationInput.model_validate(case["input"])
                result = await asyncio.wait_for(agent.verify(payload), timeout=timeout_seconds)
                output = result.output
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
            diagnostics = getattr(agent, "last_diagnostics", None)
            if diagnostics is not None:
                record["diagnostics"] = deepcopy(diagnostics)
            record["scores"] = score_labels(case, output)
            if output is not None and not record["scores"]["contract_valid"]:
                record["outcome"] = "invalid_contract"
            records.append(record)
            write_json(output_dir / f"{case['case_id']}-trial-{trial}.json", record)
            write_report(output_dir, summarize(records, planned, stop_reason), records)
            if stop_reason:
                break
        if stop_reason:
            break
    summary = summarize(records, planned, stop_reason)
    write_report(output_dir, summary, records)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--approved-only", action="store_true")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--timeout-seconds", type=float, default=300)
    parser.add_argument("--token-budget", type=int, default=200_000)
    parser.add_argument("--region", help="Override the evaluation region without changing the app.")
    parser.add_argument(
        "--model-id", help="Override only the model used by this Verifier evaluation.",
    )
    parser.add_argument(
        "--prompt-file", type=Path, help="Use an evaluation-only UTF-8 system prompt.",
    )
    parser.add_argument(
        "--diagnostics", action="store_true", help="Record SDK stops and tool-validation results.",
    )
    parser.add_argument("--execute", action="store_true", help="Make paid Bedrock model calls.")
    args = parser.parse_args()
    if args.trials < 1 or args.timeout_seconds <= 0 or args.token_budget < 1:
        parser.error("Trials, timeout, and token budget must be positive.")
    validate_dataset(FOLDER)
    dataset_bytes = (FOLDER / "dataset.json").read_bytes()
    dataset = json.loads(dataset_bytes)
    cases = select_cases(dataset, args.case_ids, args.approved_only)
    if Path.cwd().resolve() != ROOT:
        parser.error("Run from the repository root so the application reads its normal .env file.")
    settings = StrandsSettings()
    if args.region:
        settings = settings.model_copy(update={"region": args.region})
    if args.model_id is not None:
        model_id = args.model_id.strip()
        if not model_id:
            parser.error("The model ID must not be empty.")
        settings = settings.model_copy(update={"verifier_model_id": model_id})
    system_prompt = VerifierAgent.system_prompt
    if args.prompt_file:
        try:
            system_prompt = args.prompt_file.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            parser.error("Cannot read the prompt file as UTF-8 text.")
        if not system_prompt:
            parser.error("The prompt file must not be empty.")
    pending = [case["case_id"] for case in cases if case["review"]["status"] != "approved"]
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    pinned = next(
        item.split("==", 1)[1] for item in project["project"]["optional-dependencies"]["aws"]
        if item.startswith("strands-agents")
    )
    installed = importlib.metadata.version("strands-agents")
    source_files = [
        "agents/verifier.py", "strands_runtime.py", "runtime.py",
        "models.py", "config.py", "validation.py",
    ]
    plan = {
        "mode": "execute" if args.execute else "preview",
        "case_ids": [case["case_id"] for case in cases],
        "pending_case_ids": pending, "trials_per_case": args.trials,
        "planned_verifier_invocations": len(cases) * args.trials,
        "ready_to_execute": not pending and installed == pinned,
        "aws_access_checked": False,
        "model_id": settings.verifier_model_id,
        "model_source": "cli_override" if args.model_id is not None else "application_settings",
        "region": settings.region, "max_tokens_per_response": settings.verifier_max_tokens,
        "timeout_seconds_per_trial": args.timeout_seconds,
        "evaluation_token_budget": args.token_budget,
        "dataset_version": dataset["dataset_version"], "dataset_sha256": sha256(dataset_bytes),
        "prompt_sha256": sha256(system_prompt.encode()),
        "prompt_source": str(args.prompt_file) if args.prompt_file else "application_default",
        "application_prompt_sha256": sha256(VerifierAgent.system_prompt.encode()),
        "evaluation_runner_sha256": sha256(Path(__file__).read_bytes()),
        "diagnostics_enabled": args.diagnostics,
        "diagnostics_sha256": sha256((FOLDER / "diagnostics.py").read_bytes())
        if args.diagnostics else None,
        "strands_version": installed, "pinned_strands_version": pinned,
        "source_sha256": {
            name: sha256((ROOT / "src/evidence_synthesis" / name).read_bytes())
            for name in source_files
        },
        "inference_settings": "Application defaults; temperature/top_p are not explicitly set.",
        "scope": "Verifier stage only; no full orchestrator, Guardrail, or UI evaluation.",
        "retry_policy": "No harness retries; a failed model call stops the batch.",
        "grading": "Exact verdict labels and contract checks; explanations reviewed by a human.",
    }
    print(json.dumps(plan, indent=2))
    if not args.execute:
        return
    if not plan["ready_to_execute"]:
        parser.error("Selected cases must be approved and Strands must match the project pin.")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = ROOT / "runs" / f"verifier-baseline-{stamp}-{uuid.uuid4().hex[:8]}"
    output_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
    write_json(output_dir / "manifest.json", plan)
    write_json(output_dir / "cases.json", cases)
    (output_dir / "system-prompt.txt").write_text(system_prompt + "\n")
    agent = create_verifier(settings, system_prompt, args.diagnostics)
    summary = asyncio.run(run_trials(
        cases, agent, output_dir, trials=args.trials,
        timeout_seconds=args.timeout_seconds, token_budget=args.token_budget,
    ))
    print(json.dumps({"output_dir": str(output_dir), "summary": summary}, indent=2))
    if summary["run_status"] != "complete" or any(
        row["actual"] == "invalid_or_missing_output" for row in summary["confusion_matrix"]
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
