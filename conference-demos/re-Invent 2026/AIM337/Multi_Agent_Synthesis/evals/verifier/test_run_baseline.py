# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
"""Exercise grading and execution boundaries with fake model responses only."""

import asyncio
import copy
import json
from pathlib import Path
from typing import cast

import pytest
import run_baseline as baseline

from evidence_synthesis.agents.verifier import VerifierAgent
from evidence_synthesis.models import (
    AgentRole,
    InvocationMetrics,
    VerificationInput,
    VerificationResult,
    VerifiedClaim,
)
from evidence_synthesis.runtime import RuntimeInvocationError, RuntimeResult


@pytest.fixture
def case() -> dict:
    data = json.loads((Path(__file__).parent / "dataset.json").read_text())
    return copy.deepcopy(data["cases"][0])


def response(case: dict) -> VerificationResult:
    payload = VerificationInput.model_validate(case["input"])
    return VerificationResult(
        claims=[
            VerifiedClaim(
                claim=claim, status=expected["status"],
                reason="Fixture response, not an evaluated model explanation.",
                independent_source_count=1,
            )
            for claim, expected in zip(
                payload.analysis.claims, case["expected"]["judgments"], strict=True,
            )
        ],
        citation_closure_complete=True,
    )


def metrics() -> InvocationMetrics:
    return InvocationMetrics(
        role=AgentRole.VERIFIER, model_id="test-only", latency_ms=1,
        input_tokens=17, output_tokens=3, total_tokens=20,
    )


class FakeVerifier:
    def __init__(self, case: dict, fail: bool = False) -> None:
        self.case = case
        self.fail = fail
        self.payloads: list[dict] = []

    async def verify(self, payload: VerificationInput) -> RuntimeResult[VerificationResult]:
        self.payloads.append(payload.model_dump(mode="json"))
        if self.fail:
            raise RuntimeInvocationError(metrics(), usage_complete=False)
        return RuntimeResult(output=response(self.case), metrics=metrics())


def test_label_agreement_does_not_claim_explanations_are_correct(case: dict) -> None:
    output = response(case)
    output.claims[0].reason = ""
    scores = baseline.score_labels(case, output)
    assert scores["all_labels_match"]
    assert all(row["explanation_review"] == "pending" for row in scores["claims"])


def test_accepting_an_unsupported_claim_is_a_mismatch(case: dict) -> None:
    output = response(case)
    output.claims[1] = output.claims[1].model_copy(update={"status": output.claims[0].status})
    scores = baseline.score_labels(case, output)
    assert not scores["all_labels_match"]
    assert scores["claims"][1]["expected"] == "rejected"
    assert scores["claims"][1]["actual"] == "verified"


def test_changed_claim_invalidates_contract_and_cannot_score_as_correct(case: dict) -> None:
    output = response(case)
    output.claims[0].claim = output.claims[0].claim.model_copy(update={"statement": "Changed"})
    scores = baseline.score_labels(case, output)
    assert scores["contract_error"] == "verifier_changed_claim"
    assert not any(row["matches"] for row in scores["claims"])


def test_missing_claim_invalidates_contract(case: dict) -> None:
    output = response(case)
    output.claims.pop()
    assert baseline.score_labels(case, output)["contract_error"] == "review_claim_coverage_mismatch"


def test_unknown_case_selection_fails(case: dict) -> None:
    with pytest.raises(ValueError, match="Unknown cases"):
        baseline.select_cases({"cases": [case]}, ["DOES-NOT-EXIST"], False)


def test_approved_only_filters_pending_case(case: dict) -> None:
    pending = copy.deepcopy(case)
    pending["case_id"] = "PENDING"
    pending["review"]["status"] = "pending"
    assert baseline.select_cases({"cases": [case, pending]}, None, True) == [case]


@pytest.mark.asyncio
async def test_only_typed_input_reaches_agent_and_all_trials_are_recorded(
    case: dict, tmp_path: Path,
) -> None:
    agent = FakeVerifier(case)
    summary = await baseline.run_trials(
        [case], cast(VerifierAgent, agent), tmp_path,
        trials=2, timeout_seconds=1, token_budget=200,
    )
    assert agent.payloads == [case["input"], case["input"]]
    assert all("expected" not in payload and "review" not in payload for payload in agent.payloads)
    assert summary["run_status"] == "complete"
    assert summary["claims_attempted"] == 4
    assert summary["label_accuracy_on_attempted_claims"] == 1
    assert summary["explanation_quality"].startswith("not_scored")
    assert (tmp_path / "V001-trial-2.json").is_file()


@pytest.mark.asyncio
async def test_model_failure_counts_as_nonmatch_and_stops_batch(
    case: dict, tmp_path: Path,
) -> None:
    agent = FakeVerifier(case, fail=True)
    summary = await baseline.run_trials(
        [case], cast(VerifierAgent, agent), tmp_path,
        trials=2, timeout_seconds=1, token_budget=200,
    )
    assert len(agent.payloads) == 1
    assert summary["run_status"] == "partial"
    assert summary["labels_matching"] == 0
    assert summary["claims_attempted"] == 2
    assert summary["trials_unattempted"] == 1
    assert not summary["token_usage_complete"]


@pytest.mark.asyncio
async def test_budget_stops_before_another_invocation(case: dict, tmp_path: Path) -> None:
    agent = FakeVerifier(case)
    summary = await baseline.run_trials(
        [case], cast(VerifierAgent, agent), tmp_path,
        trials=3, timeout_seconds=1, token_budget=20,
    )
    assert len(agent.payloads) == 1
    assert summary["stop_reason"] == "evaluation_token_budget_reached"
    assert summary["trials_unattempted"] == 2


@pytest.mark.asyncio
async def test_timeout_records_unknown_usage_and_stops(case: dict, tmp_path: Path) -> None:
    class SlowVerifier:
        async def verify(self, payload: VerificationInput) -> RuntimeResult[VerificationResult]:
            await asyncio.sleep(1)
            return RuntimeResult(output=response(case), metrics=metrics())

    summary = await baseline.run_trials(
        [case], cast(VerifierAgent, SlowVerifier()), tmp_path,
        trials=2, timeout_seconds=0.001, token_budget=200,
    )
    assert summary["stop_reason"] == "timeout_usage_unknown"
    assert summary["trials_attempted"] == 1
    assert not summary["token_usage_complete"]


def test_preview_does_not_construct_model_runtime(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Preview constructed a model runtime")

    monkeypatch.setattr(baseline, "StrandsRuntime", forbidden)
    monkeypatch.setattr(baseline.sys, "argv", ["run_baseline.py", "--case", "V001"])
    baseline.main()
    assert json.loads(capsys.readouterr().out)["mode"] == "preview"


def test_execute_refuses_pending_labels_before_constructing_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Unreviewed case reached the runtime")

    monkeypatch.setattr(baseline, "StrandsRuntime", forbidden)
    dataset = json.loads((Path(__file__).parent / "dataset.json").read_text())
    pending = copy.deepcopy(dataset["cases"][0])
    pending["review"]["status"] = "pending"
    monkeypatch.setattr(baseline, "select_cases", lambda *args: [pending])
    monkeypatch.setattr(baseline.sys, "argv", ["run_baseline.py", "--execute"])
    with pytest.raises(SystemExit) as error:
        baseline.main()
    assert error.value.code == 2


def test_prompt_preview_records_selected_prompt_without_constructing_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Preview constructed a model runtime")

    original = VerifierAgent.system_prompt
    prompt = "A test-only candidate prompt."
    path = tmp_path / "candidate.txt"
    path.write_text(prompt + "\n")
    monkeypatch.setattr(baseline, "StrandsRuntime", forbidden)
    monkeypatch.setattr(
        baseline.sys, "argv", ["run_baseline.py", "--prompt-file", str(path)],
    )
    baseline.main()
    plan = json.loads(capsys.readouterr().out)
    assert plan["prompt_sha256"] == baseline.sha256(prompt.encode())
    assert plan["application_prompt_sha256"] == baseline.sha256(original.encode())
    assert plan["prompt_source"] == str(path)
    assert VerifierAgent.system_prompt == original


def test_empty_prompt_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "empty.txt"
    path.write_text(" \n")
    monkeypatch.setattr(
        baseline.sys, "argv", ["run_baseline.py", "--prompt-file", str(path)],
    )
    with pytest.raises(SystemExit) as error:
        baseline.main()
    assert error.value.code == 2


@pytest.mark.asyncio
async def test_candidate_prompt_reaches_runtime_without_changing_default_or_payload(
    case: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    class RecordingRuntime:
        async def invoke_async(self, role, payload, output_type, system_prompt):
            calls.append((role, payload.model_dump(mode="json"), output_type, system_prompt))
            return RuntimeResult(output=response(case), metrics=metrics())

    original = VerifierAgent.system_prompt
    candidate = "A test-only candidate prompt."
    monkeypatch.setattr(baseline, "StrandsRuntime", lambda settings: RecordingRuntime())
    agent = baseline.create_verifier(baseline.StrandsSettings(), candidate)
    await agent.verify(VerificationInput.model_validate(case["input"]))
    assert calls == [(AgentRole.VERIFIER, case["input"], VerificationResult, candidate)]
    assert VerifierAgent.system_prompt == original


@pytest.mark.asyncio
async def test_trial_keeps_a_separate_snapshot_of_diagnostics(case: dict, tmp_path: Path) -> None:
    agent = FakeVerifier(case)
    agent.last_diagnostics = {
        "agent_result": {"stop_reason": "tool_use", "structured_output_present": True},
    }
    summary = await baseline.run_trials(
        [case], cast(VerifierAgent, agent), tmp_path,
        trials=1, timeout_seconds=1, token_budget=200,
    )
    agent.last_diagnostics["agent_result"]["stop_reason"] = "changed_after_run"
    record = json.loads((tmp_path / "V001-trial-1.json").read_text())
    assert record["diagnostics"]["agent_result"]["stop_reason"] == "tool_use"
    assert summary["trials_with_valid_contract"] == 1


def test_diagnostic_hook_preserves_tool_arguments_and_results() -> None:
    from types import SimpleNamespace

    from diagnostics import observed_agent

    observation = {"events": []}
    observed = observed_agent(observation)
    arguments = {"contradictions": [{"claim_ids": ["C3"]}]}
    result = {"status": "error", "content": [{"text": "At least two claim IDs are required"}]}
    event = SimpleNamespace(
        tool_use={"name": "VerificationResult", "input": arguments},
        result=result, exception=None, retry=False,
    )
    original_arguments = copy.deepcopy(arguments)
    original_result = copy.deepcopy(result)
    observed.after_tool(None, event)
    assert arguments == original_arguments
    assert result == original_result
    assert not event.retry
    arguments["contradictions"].clear()
    result["status"] = "changed_after_event"
    saved = observation["events"][0]
    assert saved["input_contradictions"] == [{"claim_ids": ["C3"]}]
    assert saved["result"]["status"] == "error"


def test_model_override_preserves_application_settings_and_does_not_call_runtime(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Preview constructed a model runtime")

    original = baseline.StrandsSettings().model_dump()
    monkeypatch.setattr(baseline, "StrandsRuntime", forbidden)
    monkeypatch.setattr(
        baseline.sys, "argv",
        ["run_baseline.py", "--model-id", "us.anthropic.claude-opus-5"],
    )
    baseline.main()
    plan = json.loads(capsys.readouterr().out)
    assert plan["model_id"] == "us.anthropic.claude-opus-5"
    assert plan["model_source"] == "cli_override"
    assert plan["max_tokens_per_response"] == original["verifier_max_tokens"]
    assert baseline.StrandsSettings().model_dump() == original


def test_empty_model_override_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(baseline.sys, "argv", ["run_baseline.py", "--model-id", " "])
    with pytest.raises(SystemExit) as error:
        baseline.main()
    assert error.value.code == 2
