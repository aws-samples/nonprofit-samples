# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
"""Check saved-output grading without making model calls."""

import copy
import json
from pathlib import Path

import pytest
import score_saved as scorer

from evidence_synthesis.models import AgentRole, InvocationMetrics
from evidence_synthesis.runtime import RuntimeInvocationError, RuntimeResult
from evidence_synthesis.validation import ContractError


@pytest.fixture
def saved_run(tmp_path: Path) -> Path:
    data = json.loads((scorer.FOLDER.parent / "dataset.json").read_text())
    case = copy.deepcopy(data["cases"][0])
    case["name"] = "PRIVATE_CASE_NAME"
    case["expected"]["judgments"][0]["rationale"] = "PRIVATE_REFERENCE_REASON"
    extra = copy.deepcopy(case["input"]["evidence"][0])
    extra.update(evidence_id="E002", source_id="supplementary", excerpt="Extra original evidence.")
    case["input"]["evidence"].append(extra)
    output = {
        "claims": [
            {"claim": claim, "status": ref["status"],
             "reason": "Saved explanation mentions E002.", "independent_source_count": 1}
            for claim, ref in zip(
                case["input"]["analysis"]["claims"], case["expected"]["judgments"], strict=True,
            )
        ],
        "contradictions": [], "evidence_gaps": [], "retry_queries": [],
        "citation_closure_complete": True,
    }
    record = {
        "case_id": "V001", "trial": 1,
        "input_sha256": scorer.sha256(json.dumps(case["input"], sort_keys=True).encode()),
        "outcome": "returned_output", "output": output, "metrics": {"total_tokens": 12},
        "wall_time_seconds": 1.0, "token_usage_complete": True,
        "scores": {
            "contract_valid": True,
            "claims": [
                {"claim_id": ref["claim_id"], "matches": True,
                 "actual_reason": item["reason"], "reason_requirements": ref["reason_requirements"]}
                for item, ref in zip(output["claims"], case["expected"]["judgments"], strict=True)
            ],
        },
    }
    scorer.write_json(tmp_path / "cases.json", [case])
    scorer.write_json(tmp_path / "manifest.json", {
        "case_ids": ["V001"], "trials_per_case": 1,
        "prompt_sha256": scorer.sha256(b"Test Verifier prompt"),
    })
    scorer.write_json(tmp_path / "summary.json", {
        "run_status": "complete", "trials_attempted": 1, "trials_with_valid_contract": 1,
        "claims_attempted": 2, "labels_matching": 2,
    })
    scorer.write_json(tmp_path / "V001-trial-1.json", record)
    (tmp_path / "system-prompt.txt").write_text("Test Verifier prompt\n")
    return tmp_path


def test_preparation_keeps_evidence_but_excludes_private_reference_fields(saved_run: Path) -> None:
    reviews, source = scorer.prepare_reviews(saved_run)
    assert len(reviews) == 2
    for review in reviews:
        payload = review["input"]
        assert [e["evidence_id"] for e in payload["evidence"]] == ["E001", "E002"]
        assert payload["unavailable_evidence_ids"] == []
        assert len(payload["original_claims"]) == 2
        assert "PRIVATE_" not in json.dumps(payload)
        assert "reference_verdict" not in payload and "expected" not in review
        assert payload["verifier_reason"] == "Saved explanation mentions E002."
    assert source["source_hashes"]["cases.json"]


@pytest.mark.parametrize("mutation", [
    "input_hash", "changed_claim", "unapproved", "incomplete", "invalid_record",
])
def test_inconsistent_saved_runs_are_rejected(saved_run: Path, mutation: str) -> None:
    path = saved_run / "V001-trial-1.json"
    record = json.loads(path.read_text())
    if mutation == "input_hash":
        record["input_sha256"] = "wrong"
    elif mutation == "changed_claim":
        record["output"]["claims"][0]["claim"]["confidence"] = 0.9
    elif mutation == "invalid_record":
        record["scores"]["contract_valid"] = False
    elif mutation == "unapproved":
        cases = json.loads((saved_run / "cases.json").read_text())
        cases[0]["review"]["status"] = "pending"
        scorer.write_json(saved_run / "cases.json", cases)
    else:
        summary = json.loads((saved_run / "summary.json").read_text())
        summary["run_status"] = "partial"
        scorer.write_json(saved_run / "summary.json", summary)
    scorer.write_json(path, record)
    with pytest.raises((ValueError, ContractError)):
        scorer.prepare_reviews(saved_run)


class FakeRuntime:
    def __init__(self, fail_at: int | None = None) -> None:
        self.payloads: list[dict] = []
        self.fail_at = fail_at

    def invoke(self, role, payload, output_type, system_prompt):
        self.payloads.append(payload.model_dump(mode="json"))
        metrics = InvocationMetrics(
            role=AgentRole.VERIFIER, model_id="test", latency_ms=1,
            input_tokens=8, output_tokens=4, total_tokens=12,
        )
        if len(self.payloads) == self.fail_at:
            raise RuntimeInvocationError(metrics, usage_complete=False)
        output = scorer.Judgment.model_validate({
            key: {"grade": "pass", "reason": "Test-only judge response."} for key in scorer.CHECKS
        })
        return RuntimeResult(output=output, metrics=metrics)


@pytest.mark.asyncio
async def test_scoring_creates_judge_grades_without_fabricated_references(saved_run: Path) -> None:
    reviews, _ = scorer.prepare_reviews(saved_run)
    output = saved_run / "graded"
    output.mkdir()
    runtime = FakeRuntime()
    summary = await scorer.score_reviews(
        reviews, runtime, "Test rubric", output, timeout_seconds=1, token_budget=100,
    )
    assert summary["returned"] == 2 and summary["explanation_grades"] == {"pass": 2}
    assert runtime.payloads == [r["input"] for r in reviews]
    record = json.loads((output / "R0001.json").read_text())
    assert "expected" not in record and record["overall_grade"] == "pass"
    assert record["input"]["verifier_reason"] == "Saved explanation mentions E002."


@pytest.mark.asyncio
async def test_model_failure_stops_without_retry_and_retains_usage(saved_run: Path) -> None:
    reviews, _ = scorer.prepare_reviews(saved_run)
    output = saved_run / "graded"
    output.mkdir()
    runtime = FakeRuntime(fail_at=1)
    summary = await scorer.score_reviews(
        reviews, runtime, "Test rubric", output, timeout_seconds=1, token_budget=100,
    )
    assert len(runtime.payloads) == 1
    assert summary["run_status"] == "partial" and summary["unattempted"] == 1
    assert summary["judge_recorded_tokens"] == 12
    assert not summary["judge_token_usage_complete"]
    assert summary["review_queue"] == ["R0001"]


@pytest.mark.asyncio
async def test_budget_stops_before_another_judgment(saved_run: Path) -> None:
    reviews, _ = scorer.prepare_reviews(saved_run)
    output = saved_run / "graded"
    output.mkdir()
    runtime = FakeRuntime()
    summary = await scorer.score_reviews(
        reviews, runtime, "Test rubric", output, timeout_seconds=1, token_budget=10,
    )
    assert len(runtime.payloads) == 1 and summary["unattempted"] == 1
    assert summary["stop_reason"] == "token_budget_reached"


def test_preview_never_constructs_model_client(
    saved_run: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("Preview must not construct a client.")

    monkeypatch.setattr(scorer, "StrandsRuntime", forbidden)
    monkeypatch.setattr("sys.argv", ["score_saved.py", "--source-run", str(saved_run)])
    scorer.main()
    plan = json.loads(capsys.readouterr().out)
    assert plan["mode"] == "preview" and plan["planned_judge_calls"] == 2
    assert plan["new_verifier_calls"] == 0


def test_saved_run_path_is_required(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("Missing source must not construct a client.")

    monkeypatch.setattr(scorer, "StrandsRuntime", forbidden)
    monkeypatch.setattr("sys.argv", ["score_saved.py"])
    with pytest.raises(SystemExit) as error:
        scorer.main()
    assert error.value.code == 2
    assert "--source-run" in capsys.readouterr().err
