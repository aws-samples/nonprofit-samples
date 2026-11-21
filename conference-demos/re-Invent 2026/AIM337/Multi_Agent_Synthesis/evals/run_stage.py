# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
"""Evaluate the current Analyst or Synthesizer against frozen development cases."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import math
import re
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from analyst.validate_dataset import validate_dataset as validate_analyst_dataset
from synthesizer.validate_dataset import validate_dataset as validate_synthesizer_dataset

from evidence_synthesis.agents.analyst import AnalystAgent
from evidence_synthesis.agents.synthesizer import SynthesizerAgent
from evidence_synthesis.config import StrandsSettings
from evidence_synthesis.models import AnalysisInput, SynthesisInput
from evidence_synthesis.runtime import RuntimeInvocationError
from evidence_synthesis.strands_runtime import StrandsRuntime
from evidence_synthesis.validation import ContractError, validate_analysis, validate_synthesis

ROOT = Path(__file__).resolve().parents[1]
FOLDER = Path(__file__).resolve().parent
DATASETS = {"analyst": "dataset.json", "synthesizer": "dataset-v1.json"}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def load_cases(stage: str) -> list[dict]:
    validator = (
        validate_analyst_dataset if stage == "analyst" else validate_synthesizer_dataset
    )
    validator()
    cases = json.loads((FOLDER / stage / DATASETS[stage]).read_text())["cases"]
    identifiers = [case["case_id"] for case in cases]
    if len(set(identifiers)) != len(identifiers) or any(
        not re.fullmatch(r"[AS]\d{3}", name) for name in identifiers
    ):
        raise ValueError("Invalid or duplicate case IDs")
    return cases


class RecordingRuntime:
    """Save what the agent actually sends, without adding reference answers."""

    def __init__(self, runtime: StrandsRuntime) -> None:
        self.runtime = runtime
        self.call: dict | None = None

    async def invoke_async(self, role, payload, output_type, system_prompt):
        self.call = {
            "role": role.value,
            "payload": payload.model_dump(mode="json"),
            "output_schema": output_type.model_json_schema(),
        }
        return await self.runtime.invoke_async(role, payload, output_type, system_prompt)


def inspect_output(stage: str, payload, output) -> dict:
    normalized = output.model_copy(deep=True)
    error = None
    try:
        if stage == "analyst":
            validate_analysis(normalized, payload.evidence)
        else:
            validate_synthesis(normalized, payload.verification, payload.evidence)
            normalized.partial = False
            normalized.terminal_reason = None
    except ContractError as problem:
        error = str(problem)
    return {
        "contract_valid": error is None,
        "contract_error": error,
        "empty_claims": not output.claims if stage == "analyst" else None,
        "normalized_output": normalized.model_dump(mode="json") if error is None else None,
        "semantic_grade": "pending",
    }


def save_results(folder: Path, cases: list[dict], records: list[dict], summary: dict) -> None:
    write_json(folder / "summary.json", summary)
    references = {case["case_id"]: case for case in cases}
    lines = [
        "# Review these stage outputs", "",
        "Review actual output against the input evidence and the reference meanings.",
        "Contract validity is separate from factual quality. All semantic grades are pending.",
        "Use Pass, Fail, or Needs review; preserve ambiguity and explain each decision.", "",
    ]
    for row in records:
        lines += [
            f"## {row['case_id']} / trial {row['trial']}", "",
            f"Outcome: {row['outcome']}", "", "### Output", "", "```json",
            json.dumps(row.get("output"), indent=2, ensure_ascii=False), "```", "",
            "### Reference requirements", "", "```json",
            json.dumps(references[row["case_id"]]["expected"], indent=2, ensure_ascii=False),
            "```", "", "**Semantic review:** Pending.", "",
        ]
    (folder / "REVIEW.md").write_text("\n".join(lines) + "\n")


async def run_cases(
    stage: str, cases: list[dict], agent, folder: Path, *,
    trials: int, timeout_seconds: float, token_budget: int,
    recorder: RecordingRuntime | None = None,
) -> dict:
    records: list[dict] = []
    stop = None
    planned = len(cases) * trials
    for trial in range(1, trials + 1):
        for case in cases:
            used = sum(row.get("metrics", {}).get("total_tokens", 0) for row in records)
            if used >= token_budget:
                stop = "token_budget_reached"
                break
            payload_type = AnalysisInput if stage == "analyst" else SynthesisInput
            payload = payload_type.model_validate(case["input"])
            row = {
                "case_id": case["case_id"], "trial": trial,
                "started_at": datetime.now(UTC).isoformat(),
                "input": payload.model_dump(mode="json"),
                "token_usage_complete": False, "semantic_grade": "pending",
            }
            path = folder / f"{case['case_id']}-trial-{trial}.json"
            write_json(path, {**row, "outcome": "started"})
            if recorder is not None:
                recorder.call = None
            started = time.monotonic()
            try:
                operation = agent.analyze if stage == "analyst" else agent.synthesize
                result = await asyncio.wait_for(operation(payload), timeout=timeout_seconds)
                row.update(
                    outcome="returned_output", output=result.output.model_dump(mode="json"),
                    metrics=result.metrics.model_dump(mode="json"), token_usage_complete=True,
                    checks=inspect_output(stage, payload, result.output),
                )
                if not row["checks"]["contract_valid"]:
                    row["outcome"] = "invalid_contract"
            except RuntimeInvocationError as error:
                row.update(
                    outcome="model_error", error_type=type(error.__cause__ or error).__name__,
                    metrics=error.metrics.model_dump(mode="json"),
                    token_usage_complete=error.usage_complete,
                )
                stop = "model_error"
            except TimeoutError:
                row.update(outcome="timeout", error_type="TimeoutError")
                stop = "timeout_usage_unknown"
            except Exception as error:
                row.update(outcome="error", error_type=type(error).__name__)
                stop = "unexpected_error_usage_unknown"
            row["wall_seconds"] = time.monotonic() - started
            if recorder is not None:
                row["actual_call"] = recorder.call
            records.append(row)
            write_json(path, row)
            summary = summarize(records, planned, stop)
            save_results(folder, cases, records, summary)
            print(json.dumps({
                "case_id": case["case_id"], "trial": trial, "outcome": row["outcome"],
            }), flush=True)
            if stop:
                break
        if stop:
            break
    summary = summarize(records, planned, stop)
    save_results(folder, cases, records, summary)
    return summary


def summarize(records: list[dict], planned: int, stop: str | None) -> dict:
    return {
        "status": "complete" if len(records) == planned and stop is None else "partial",
        "stop_reason": stop, "planned": planned, "attempted": len(records),
        "unattempted": planned - len(records),
        "valid_contracts": sum(r.get("checks", {}).get("contract_valid", False) for r in records),
        "empty_claim_outputs": sum(
            r.get("checks", {}).get("empty_claims") is True for r in records
        ),
        "recorded_tokens": sum(r.get("metrics", {}).get("total_tokens", 0) for r in records),
        "token_usage_complete": all(r["token_usage_complete"] for r in records),
        "semantic_quality": "pending human review; not inferred from contract checks",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=tuple(DATASETS))
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--model-id")
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=300)
    parser.add_argument("--token-budget", type=int, default=100_000)
    parser.add_argument("--execute", action="store_true", help="Make paid Bedrock calls.")
    args = parser.parse_args()
    if (
        min(args.trials, args.token_budget, args.max_tokens or 1) < 1
        or args.max_tokens == 0
        or not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0
    ):
        parser.error("Trials and limits must be positive and finite.")
    cases = load_cases(args.stage)
    if args.case_ids:
        if set(args.case_ids) - {case["case_id"] for case in cases}:
            parser.error("Unknown case ID.")
        cases = [case for case in cases if case["case_id"] in args.case_ids]
    model_field, token_field = f"{args.stage}_model_id", f"{args.stage}_max_tokens"
    settings = StrandsSettings(
        _env_file=None, region=args.region,
        **{
            model_field: args.model_id or StrandsSettings.model_fields[model_field].default,
            token_field: args.max_tokens or StrandsSettings.model_fields[token_field].default,
        },
    )
    agent_type = AnalystAgent if args.stage == "analyst" else SynthesizerAgent
    prompt = (
        args.prompt_file.read_text().strip() if args.prompt_file else agent_type.system_prompt
    )
    if not prompt:
        parser.error("Prompt must not be empty.")
    plan = {
        "stage": args.stage, "execute": args.execute,
        "case_ids": [case["case_id"] for case in cases], "trials": args.trials,
        "planned_agent_invocations": len(cases) * args.trials,
        "max_model_turns_per_invocation": 2,
        "model_id": getattr(settings, model_field), "max_tokens": getattr(settings, token_field),
        "region": args.region, "timeout_seconds": args.timeout_seconds,
        "token_budget": args.token_budget,
        "strands_version": importlib.metadata.version("strands-agents"),
        "prompt_sha256": digest(prompt.encode()),
        "dataset_sha256": digest((FOLDER / args.stage / DATASETS[args.stage]).read_bytes()),
        "reference_expectations_sent_to_model": False,
        "scope": "Isolated stage; no upstream agents, safety gate, or automatic semantic score.",
        "budget_policy": "Checked between calls; final call may exceed the remaining budget.",
    }
    print(json.dumps(plan, indent=2), flush=True)
    if not args.execute:
        return
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    folder = ROOT / "runs" / f"{args.stage}-eval-{stamp}-{uuid.uuid4().hex[:8]}"
    folder.mkdir(mode=0o700, parents=True, exist_ok=False)
    # Each new run records current code; historical experiments remain frozen.
    sources = [
        *sorted((ROOT / "src/evidence_synthesis").rglob("*.py")),
        Path(__file__), FOLDER / args.stage / "validate_dataset.py",
        ROOT / "uv.lock", ROOT / "pyproject.toml",
    ]
    plan["source_sha256"] = {}
    for path in sources:
        relative = str(path.relative_to(ROOT))
        plan["source_sha256"][relative] = digest(path.read_bytes())
        target = folder / "snapshots" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
    write_json(folder / "manifest.json", plan)
    write_json(folder / "cases.json", cases)
    (folder / "system-prompt.txt").write_text(prompt + "\n")
    recorder = RecordingRuntime(StrandsRuntime(settings))
    agent = agent_type(recorder)
    agent.system_prompt = prompt
    summary = asyncio.run(run_cases(
        args.stage, cases, agent, folder, trials=args.trials,
        timeout_seconds=args.timeout_seconds, token_budget=args.token_budget, recorder=recorder,
    ))
    print(json.dumps({"output_dir": str(folder), "summary": summary}, indent=2))
    if summary["status"] != "complete" or summary["valid_contracts"] != summary["attempted"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
