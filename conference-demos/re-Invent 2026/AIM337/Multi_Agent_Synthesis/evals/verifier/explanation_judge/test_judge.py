# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
"""Check grading boundaries and failure accounting without model calls."""

import asyncio
import copy
import json
from pathlib import Path

import pytest
import run_judge as judge
from pydantic import ValidationError

from evidence_synthesis.models import AgentRole, InvocationMetrics
from evidence_synthesis.runtime import RuntimeInvocationError, RuntimeResult


@pytest.fixture
def examples() -> list[dict]:
    return judge.load_calibration(judge.FOLDER / "calibration-v1.2.json")["examples"]


def judgment(example: dict) -> judge.Judgment:
    return judge.Judgment.model_validate({
        key: {"grade": example["expected"][key], "reason": "Fake response for testing only."}
        for key in judge.CHECKS
    })


def metrics() -> InvocationMetrics:
    return InvocationMetrics(
        role=AgentRole.VERIFIER, model_id="test-only", latency_ms=1,
        input_tokens=8, output_tokens=4, total_tokens=12,
    )


class FakeRuntime:
    def __init__(self, responses: list) -> None:
        self.responses = list(responses)
        self.payloads: list[dict] = []

    def invoke(self, role, payload, output_type, system_prompt):
        self.payloads.append(payload.model_dump(mode="json"))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return RuntimeResult(output=value, metrics=metrics())


def test_human_grades_and_metadata_never_enter_judge_input(examples: list[dict]) -> None:
    example = copy.deepcopy(examples[0])
    before = judge.judge_input(example).model_dump_json()
    example["expected"] = {"private": "HUMAN_GRADE_SENTINEL"}
    example["human_review"] = {"private": "HUMAN_REVIEW_SENTINEL"}
    example["origin"] = {"private": "MODEL_ID_SENTINEL"}
    example["preparation_note"] = "CONTROL_TYPE_AND_REVIEW_HINT_SENTINEL"
    example["example_id"] = "IDENTITY_SENTINEL"
    assert judge.judge_input(example).model_dump_json() == before


def test_input_rejects_accidental_reference_fields(examples: list[dict]) -> None:
    example = copy.deepcopy(examples[0])
    example["input"]["human_grade"] = "pass"
    with pytest.raises(ValidationError):
        judge.judge_input(example)


@pytest.mark.parametrize(
    "field", ["verifier_reason", "verifier_verdict", "human_grade", "confidence"],
)
def test_original_claims_reject_reviews_grades_and_other_fields(field: str) -> None:
    data = json.loads((judge.FOLDER / "calibration-v1.2.json").read_text())
    example = data["examples"][0]
    example["input"]["original_claims"][1][field] = "MUST_NOT_REACH_MODEL"
    with pytest.raises(ValidationError):
        judge.judge_input(example)


@pytest.mark.parametrize(
    "mutation", ["duplicate", "changed_target", "unknown_target", "missing_list"],
)
def test_inconsistent_original_claim_context_is_rejected(mutation: str) -> None:
    data = json.loads((judge.FOLDER / "calibration-v1.2.json").read_text())
    example = data["examples"][0]
    payload = example["input"]
    if mutation == "duplicate":
        payload["original_claims"].append(payload["original_claims"][0])
    elif mutation == "changed_target":
        payload["claim"] = "A different claim."
    elif mutation == "unknown_target":
        payload["claim_id"] = "C999"
    else:
        payload.pop("original_claims")
    with pytest.raises(ValidationError):
        judge.judge_input(example)


@pytest.mark.parametrize("wrong_version", ["1.0", "1.1", "2.0"])
def test_rubric_and_context_version_must_match(
    tmp_path: Path, wrong_version: str,
) -> None:
    data = json.loads((judge.FOLDER / "calibration-v1.2.json").read_text())
    data["rubric_version"] = wrong_version
    path = tmp_path / "wrong-version.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        judge.load_calibration(path)


@pytest.mark.parametrize("unavailable", [["E001"], ["E002", "E002"], [""], [" "]])
def test_invalid_evidence_availability_is_rejected(unavailable: list[str]) -> None:
    data = judge.load_calibration(judge.FOLDER / "validation-v1.2.json")
    example = copy.deepcopy(data["examples"][-1])
    example["input"]["unavailable_evidence_ids"] = unavailable
    with pytest.raises(ValidationError):
        judge.judge_input(example)


@pytest.mark.asyncio
async def test_known_missing_evidence_reaches_judge_without_review_notes(tmp_path: Path) -> None:
    data = judge.load_calibration(judge.FOLDER / "validation-v1.2.json")
    example = copy.deepcopy(data["examples"][-1])
    example["preparation_note"] = "DO_NOT_SEND_THIS_NOTE"
    example["human_review"]["notes"] = "DO_NOT_SEND_THIS_GRADE"
    runtime = FakeRuntime([judgment(example)])
    await judge.run_trials(
        [example], runtime, "Test prompt", tmp_path,
        trials=1, timeout_seconds=1, token_budget=100,
    )
    assert runtime.payloads[0]["unavailable_evidence_ids"] == ["E002"]
    assert runtime.payloads[0] == example["input"]
    assert "DO_NOT_SEND" not in json.dumps(runtime.payloads)


@pytest.mark.parametrize("mutation", ["pending", "duplicate", "changed_input", "bad_overall"])
def test_invalid_calibration_is_rejected(tmp_path: Path, mutation: str) -> None:
    data = json.loads((judge.FOLDER / "calibration-v1.2.json").read_text())
    if mutation == "pending":
        data["examples"][0]["human_review"]["status"] = "pending"
    elif mutation == "duplicate":
        data["examples"][1]["example_id"] = data["examples"][0]["example_id"]
    elif mutation == "changed_input":
        data["examples"][0]["input"]["claim"] = "Changed after approval."
    else:
        data["examples"][0]["expected"]["overall"] = "fail"
    path = tmp_path / "data.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        judge.load_calibration(path)


@pytest.mark.parametrize(
    ("grades", "expected"),
    [
        (["pass", "pass", "pass"], "pass"),
        (["fail", "pass", "pass"], "fail"),
        (["needs_review", "pass", "pass"], "needs_review"),
        (["needs_review", "fail", "pass"], "fail"),
    ],
)
def test_overall_preserves_failures_and_review_requests(grades: list[str], expected: str) -> None:
    assert judge.overall(dict(zip(judge.CHECKS, grades, strict=True))) == expected


def test_correct_verdict_does_not_make_bad_explanation_pass(examples: list[dict]) -> None:
    example = examples[-1]
    assert example["origin"]["reference_verdict"] == example["input"]["verifier_verdict"]
    output = judgment(example)
    assert output.grounded.grade == "fail"
    score = judge.compare(example, output)
    assert score["actual_overall"] == "fail" and score["all_checks_match"]


def test_false_passes_are_visible(examples: list[dict]) -> None:
    example = examples[-1]
    output = judgment(example)
    output.grounded.grade = "pass"
    record = {
        "outcome": "returned_output", "token_usage_complete": True,
        "scores": judge.compare(example, output),
    }
    summary = judge.summarize([record], planned=1, stop_reason=None)
    assert summary["false_passes"] == 1
    assert summary["overall_matches"] == 0
    assert summary["per_check_matches"] == {
        "grounded": 0, "complete": 1, "supports_verdict": 1,
    }


@pytest.mark.asyncio
async def test_success_sends_only_input_and_saves_scores(
    examples: list[dict], tmp_path: Path,
) -> None:
    runtime = FakeRuntime([judgment(examples[0])])
    summary = await judge.run_trials(
        examples[:1], runtime, "Test prompt", tmp_path,
        trials=1, timeout_seconds=1, token_budget=100,
    )
    assert runtime.payloads == [examples[0]["input"]]
    assert summary["all_checks_match"] == summary["overall_matches"] == 1
    assert summary["run_status"] == "complete"
    record = json.loads((tmp_path / "J001-trial-1.json").read_text())
    assert record["scores"]["overall_matches"]
    assert "Fake response for testing only." in (tmp_path / "REPORT.md").read_text()


@pytest.mark.asyncio
async def test_last_call_failure_is_partial_and_preserves_known_usage(
    examples: list[dict], tmp_path: Path,
) -> None:
    runtime = FakeRuntime([
        judgment(examples[0]), RuntimeInvocationError(metrics(), usage_complete=False),
    ])
    summary = await judge.run_trials(
        examples[:2], runtime, "Test prompt", tmp_path,
        trials=1, timeout_seconds=1, token_budget=100,
    )
    assert summary["attempted"] == 2 and summary["returned"] == 1
    assert summary["run_status"] == "partial"
    assert summary["overall_matches"] == 1
    assert summary["recorded_tokens"] == 24
    assert not summary["token_usage_complete"]


@pytest.mark.asyncio
async def test_budget_stops_further_calls(examples: list[dict], tmp_path: Path) -> None:
    runtime = FakeRuntime([judgment(examples[0])])
    summary = await judge.run_trials(
        examples[:2], runtime, "Test prompt", tmp_path,
        trials=1, timeout_seconds=1, token_budget=10,
    )
    assert len(runtime.payloads) == 1
    assert summary["recorded_tokens"] == 12
    assert summary["stop_reason"] == "token_budget_reached"
    assert summary["unattempted"] == 1


@pytest.mark.asyncio
async def test_timeout_cancels_and_stops_without_retry(
    examples: list[dict], tmp_path: Path,
) -> None:
    cancelled = asyncio.Event()

    class SlowRuntime(FakeRuntime):
        async def invoke_async(self, *args):
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()

    summary = await judge.run_trials(
        examples[:2], SlowRuntime([]), "Test prompt", tmp_path,
        trials=1, timeout_seconds=0.01, token_budget=100,
    )
    assert cancelled.is_set()
    assert summary["attempted"] == 1 and summary["unattempted"] == 1
    assert summary["stop_reason"] == "timeout_usage_unknown"
    assert not summary["token_usage_complete"]


def test_preview_does_not_construct_model_client(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("Preview must not construct a model client.")

    monkeypatch.setattr(judge, "StrandsRuntime", forbidden)
    monkeypatch.setattr("sys.argv", ["run_judge.py"])
    judge.main()
    plan = json.loads(capsys.readouterr().out)
    assert plan["mode"] == "preview" and plan["planned_judgments"] == 24
    assert not plan["reference_grades_sent_to_model"]
    assert plan["rubric_version"] == "1.2"
    config = json.loads((judge.FOLDER / "judge-config.json").read_text())
    assert plan["model_id"] == config["model_id"]
    assert plan["prompt_sha256"] == config["prompt_sha256"]


def test_unapproved_dataset_cannot_reach_model_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    data = json.loads((judge.FOLDER / "calibration-v1.2.json").read_text())
    data["status"] = "pending_human_review"
    data["examples"][0]["expected"] = None
    path = tmp_path / "pending.json"
    path.write_text(json.dumps(data))

    def forbidden(*args, **kwargs):
        raise AssertionError("Unapproved data must not reach the model.")

    monkeypatch.setattr(judge, "StrandsRuntime", forbidden)
    monkeypatch.setattr("sys.argv", ["run_judge.py", "--dataset", str(path), "--execute"])
    with pytest.raises(ValueError, match="human-approved"):
        judge.main()
