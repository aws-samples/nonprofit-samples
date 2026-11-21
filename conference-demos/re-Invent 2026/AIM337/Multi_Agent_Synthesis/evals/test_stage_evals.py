# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
"""Offline tests for public eval boundaries; no model calls."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_stage as runner  # noqa: E402
from analyst import score_saved as judge  # noqa: E402
from analyst.validate_dataset import validate_dataset as validate_analyst  # noqa: E402
from synthesizer.validate_dataset import validate_input  # noqa: E402

from evidence_synthesis.agents.analyst import AnalystAgent  # noqa: E402
from evidence_synthesis.agents.synthesizer import SynthesizerAgent  # noqa: E402
from evidence_synthesis.models import (  # noqa: E402
    AgentRole,
    AnalysisInput,
    AnalysisResult,
    Claim,
    InvocationMetrics,
    SynthesisInput,
    SynthesisResult,
    VerificationStatus,
)
from evidence_synthesis.runtime import RuntimeInvocationError, RuntimeResult  # noqa: E402


def metrics(tokens: int = 20) -> InvocationMetrics:
    return InvocationMetrics(
        role=AgentRole.ANALYST, model_id="scripted", latency_ms=1,
        input_tokens=tokens // 2, output_tokens=tokens - tokens // 2, total_tokens=tokens,
    )


def analysis(evidence_id: str = "E001") -> AnalysisResult:
    return AnalysisResult(claims=[Claim(
        claim_id="C1", statement="Illustrative output for harness tests.",
        evidence_ids=[evidence_id], confidence=0.8,
    )])


@pytest.mark.parametrize("stage,count", [("analyst", 8), ("synthesizer", 6)])
def test_published_references_validate(stage, count):
    assert len(runner.load_cases(stage)) == count


@pytest.mark.parametrize("stage", ["analyst", "synthesizer"])
def test_preview_never_constructs_a_runtime(stage, monkeypatch, capsys):
    factory = Mock(side_effect=AssertionError("Preview must not invoke models"))
    monkeypatch.setattr(runner, "StrandsRuntime", factory)
    monkeypatch.setenv("ANALYST_MODEL_ID", "unintended-environment-model")
    monkeypatch.setattr(sys, "argv", ["run_stage.py", stage, "--trials", "2"])
    runner.main()
    plan = json.loads(capsys.readouterr().out)
    assert plan["execute"] is False
    assert plan["planned_agent_invocations"] == len(runner.load_cases(stage)) * 2
    assert plan["model_id"] != "unintended-environment-model"
    factory.assert_not_called()


@pytest.mark.parametrize("option,value", [
    ("--trials", "0"), ("--token-budget", "-1"), ("--timeout-seconds", "nan"),
    ("--timeout-seconds", "inf"), ("--max-tokens", "0"), ("--case", "../bad"),
])
def test_invalid_cli_arguments_cannot_start_calls(option, value, monkeypatch):
    factory = Mock(side_effect=AssertionError("No model calls"))
    monkeypatch.setattr(runner, "StrandsRuntime", factory)
    monkeypatch.setattr(sys, "argv", ["run_stage.py", "analyst", option, value, "--execute"])
    with pytest.raises(SystemExit):
        runner.main()
    factory.assert_not_called()


@pytest.mark.parametrize("stage", ["analyst", "synthesizer"])
async def test_actual_agent_payload_excludes_scoring_references(stage, tmp_path):
    case = runner.load_cases(stage)[0]

    async def scripted(role, payload, output_type, prompt):
        if stage == "analyst":
            output = analysis()
        else:
            output = output_type(answer="The reported total is USD 4.2 million [E001].",
                                 limitations=[])
        return RuntimeResult(output=output, metrics=metrics())

    recorder = runner.RecordingRuntime(SimpleNamespace(invoke_async=scripted))
    agent = AnalystAgent(recorder) if stage == "analyst" else SynthesizerAgent(recorder)
    summary = await runner.run_cases(
        stage, [case], agent, tmp_path, trials=1, timeout_seconds=1,
        token_budget=100, recorder=recorder,
    )
    assert summary["status"] == "complete"
    row = json.loads((tmp_path / f"{case['case_id']}-trial-1.json").read_text())
    model_input = row["actual_call"]["payload"]
    assert "expected" not in model_input and "review" not in model_input
    assert "case_id" not in model_input
    if stage == "analyst":
        assert model_input == AnalysisInput.model_validate(case["input"]).model_dump(mode="json")
    else:
        assert set(model_input) == {"question", "claims", "contradictions", "evidence_gaps"}
        assert "status" in model_input["claims"][0]
    assert row["semantic_grade"] == "pending"
    assert row["checks"]["semantic_grade"] == "pending"


async def test_invalid_contract_is_preserved_without_a_semantic_pass(tmp_path):
    case = runner.load_cases("analyst")[0]
    call = AsyncMock(return_value=RuntimeResult(output=analysis("E999"), metrics=metrics()))
    summary = await runner.run_cases(
        "analyst", [case], SimpleNamespace(analyze=call), tmp_path,
        trials=2, timeout_seconds=1, token_budget=100,
    )
    assert summary["attempted"] == 2 and summary["valid_contracts"] == 0
    assert call.await_count == 2
    row = json.loads((tmp_path / "A001-trial-1.json").read_text())
    assert row["output"]["claims"][0]["evidence_ids"] == ["E999"]
    assert row["checks"]["normalized_output"] is None
    assert row["semantic_grade"] == "pending"


def test_empty_claims_are_flagged_separately_from_contract_validity():
    payload = AnalysisInput.model_validate(runner.load_cases("analyst")[0]["input"])
    checks = runner.inspect_output("analyst", payload, AnalysisResult(claims=[]))
    assert checks["contract_valid"] and checks["empty_claims"]
    assert checks["semantic_grade"] == "pending"


async def test_budget_is_checked_between_calls(tmp_path):
    case = runner.load_cases("analyst")[0]
    call = AsyncMock(return_value=RuntimeResult(output=analysis(), metrics=metrics(30)))
    summary = await runner.run_cases(
        "analyst", [case], SimpleNamespace(analyze=call), tmp_path,
        trials=3, timeout_seconds=1, token_budget=20,
    )
    assert summary["stop_reason"] == "token_budget_reached"
    assert summary["recorded_tokens"] == 30
    assert summary["attempted"] == 1 and summary["unattempted"] == 2


async def test_failed_call_retains_known_usage_and_stops_without_retry(tmp_path):
    case = runner.load_cases("analyst")[0]
    call = AsyncMock(side_effect=RuntimeInvocationError(metrics(17), False))
    summary = await runner.run_cases(
        "analyst", [case], SimpleNamespace(analyze=call), tmp_path,
        trials=3, timeout_seconds=1, token_budget=100,
    )
    assert call.await_count == 1
    assert summary["recorded_tokens"] == 17
    assert not summary["token_usage_complete"]
    assert summary["stop_reason"] == "model_error"


async def test_timeout_is_not_counted_as_zero_cost_success(tmp_path):
    async def slow(_):
        await asyncio.sleep(1)

    summary = await runner.run_cases(
        "analyst", runner.load_cases("analyst")[:1], SimpleNamespace(analyze=slow), tmp_path,
        trials=2, timeout_seconds=0.001, token_budget=100,
    )
    assert summary["stop_reason"] == "timeout_usage_unknown"
    assert summary["attempted"] == 1 and not summary["token_usage_complete"]


def test_synthesis_normalization_retains_raw_output():
    payload = SynthesisInput.model_validate(runner.load_cases("synthesizer")[0]["input"])
    output = SynthesisResult(answer="USD 4.2 million [E001].", partial=True, terminal_reason="test")
    checks = runner.inspect_output("synthesizer", payload, output)
    assert checks["contract_valid"]
    assert checks["normalized_output"]["partial"] is False
    assert checks["normalized_output"]["citations"]
    assert output.partial and output.terminal_reason == "test" and not output.citations


@pytest.mark.parametrize("change", ["partial", "open", "all_rejected"])
def test_synthesizer_dataset_refuses_uncovered_paths(change):
    payload = SynthesisInput.model_validate(runner.load_cases("synthesizer")[0]["input"])
    if change == "partial":
        payload.partial = True
    elif change == "open":
        payload.verification.citation_closure_complete = False
    else:
        for claim in payload.verification.claims:
            claim.status = VerificationStatus.REJECTED
    with pytest.raises(ValueError):
        validate_input(payload)


def test_analyst_reference_mutation_is_rejected(tmp_path):
    folder = runner.FOLDER / "analyst"
    (tmp_path / "versions").mkdir()
    (tmp_path / "versions/freeze-v1.0.json").write_bytes(
        (folder / "versions/freeze-v1.0.json").read_bytes()
    )
    changed = json.loads((folder / "dataset.json").read_text())
    changed["cases"][0]["expected"]["coverage_policy"] = "Anything passes"
    runner.write_json(tmp_path / "dataset.json", changed)
    with pytest.raises(ValueError, match="Frozen dataset changed"):
        validate_analyst(tmp_path)


def test_judge_input_and_mechanical_checks_are_separate():
    case = runner.load_cases("analyst")[4]
    payload = judge.make_input(case, analysis().model_dump(), requires_gaps=True)
    assert set(payload.model_dump()) == {"analysis_input", "analyst_output", "requirements"}
    assert "required_evidence_gaps_empty" in judge.mechanical_failures(payload)
    assert judge.overall(["pass", "needs_review"]) == "needs_review"
    assert judge.overall(["fail", "needs_review"]) == "fail"


async def test_judge_reports_mechanical_failure_even_when_model_passes(tmp_path):
    case = runner.load_cases("analyst")[4]
    payload = judge.make_input(case, analysis().model_dump(), requires_gaps=True)
    judgment = judge.Judgment(**{
        name: {"grade": "pass", "reason": "Scripted check."} for name in judge.CHECKS
    })
    runtime = SimpleNamespace(invoke_async=AsyncMock(return_value=RuntimeResult(
        output=judgment, metrics=metrics(),
    )))
    summary = await judge.score_records(
        [{"source_file": "A005-trial-1.json", "source_sha256": "test", "payload": payload}],
        runtime, "Reviewed rubric", tmp_path, timeout=1, token_budget=100,
    )
    row = json.loads((tmp_path / "A005-trial-1.json").read_text())
    assert row["llm_grade"] == "pass" and row["combined_grade"] == "fail"
    assert summary["combined_grades"]["fail"] == 1


def saved_run(folder):
    case = runner.load_cases("analyst")[0]
    runner.write_json(folder / "manifest.json", {
        "stage": "analyst",
        "dataset_sha256": runner.digest((runner.FOLDER / "analyst/dataset.json").read_bytes()),
    })
    runner.write_json(folder / "cases.json", [case])
    runner.write_json(folder / "A001-trial-1.json", {
        "case_id": "A001", "trial": 1,
        "input": AnalysisInput.model_validate(case["input"]).model_dump(mode="json"),
        "output": analysis().model_dump(mode="json"),
        "human_grade": "fail", "reviewer_reason": "This must not go to the judge",
    })


def test_saved_judge_preview_withholds_human_annotations(tmp_path, monkeypatch, capsys):
    saved_run(tmp_path)
    config = json.loads((judge.FOLDER / "judge-settings.json").read_text())
    records = judge.load_saved(tmp_path, config)
    payload = records[0]["payload"].model_dump_json()
    assert "human_grade" not in payload and "reviewer_reason" not in payload
    factory = Mock(side_effect=AssertionError("Preview must not construct a model runtime"))
    monkeypatch.setattr(judge, "StrandsRuntime", factory)
    monkeypatch.setattr(sys, "argv", ["score_saved.py", str(tmp_path)])
    judge.main()
    assert json.loads(capsys.readouterr().out)["planned_judge_invocations"] == 1
    factory.assert_not_called()


@pytest.mark.parametrize("target", ["input", "expected", "dataset_hash"])
def test_saved_judge_rejects_changed_references(tmp_path, target):
    saved_run(tmp_path)
    config = json.loads((judge.FOLDER / "judge-settings.json").read_text())
    if target == "input":
        path = tmp_path / "A001-trial-1.json"
        data = json.loads(path.read_text())
        data["input"]["question"] = "A different question"
    elif target == "expected":
        path = tmp_path / "cases.json"
        data = json.loads(path.read_text())
        data[0]["expected"]["coverage_policy"] = "Pass all answers"
    else:
        path = tmp_path / "manifest.json"
        data = json.loads(path.read_text())
        data["dataset_sha256"] = "changed"
    runner.write_json(path, data)
    with pytest.raises(ValueError):
        judge.load_saved(tmp_path, config)


async def test_judge_failed_call_preserves_usage_and_stops(tmp_path):
    case = runner.load_cases("analyst")[0]
    payload = judge.make_input(case, analysis().model_dump(), requires_gaps=False)
    record = {"source_file": "A001-trial-1.json", "source_sha256": "test", "payload": payload}
    runtime = SimpleNamespace(invoke_async=AsyncMock(
        side_effect=RuntimeInvocationError(metrics(13), False),
    ))
    summary = await judge.score_records(
        [record, record], runtime, "Reviewed rubric", tmp_path, timeout=1, token_budget=100,
    )
    assert runtime.invoke_async.await_count == 1
    assert summary["recorded_tokens"] == 13 and not summary["token_usage_complete"]
    assert summary["stop_reason"] == "model_error"
    assert sum(summary["llm_grades"].values()) == 0
