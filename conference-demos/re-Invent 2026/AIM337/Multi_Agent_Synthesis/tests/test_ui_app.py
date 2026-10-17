# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
import json
import logging
import shutil
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

from evidence_synthesis.models import ResearchState, SynthesisResult, TerminalStatus
from evidence_synthesis.ui import aws, inputs, runners
from evidence_synthesis.ui.artifact_progress import FileArtifactReader
from evidence_synthesis.ui.log_progress import RunLogReader

APP_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "evidence_synthesis"
    / "ui"
    / "app.py"
)


def _run_and_wait(app: AppTest) -> None:
    app.button(key="run-pipeline").click().run(timeout=20)
    active = app.session_state["active_run"]
    if active is not None:
        active.future.exception(timeout=30)
        app.run(timeout=20)


def test_ui_defaults_to_presenter_mode_without_contacting_aws(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    discover = Mock(side_effect=AssertionError("AWS discovery must require an action"))
    monkeypatch.setattr(aws, "discover_settings", discover)
    app = AppTest.from_file(str(APP_PATH)).run(timeout=20)

    assert not app.exception
    assert app.toggle(key="presenter-mode").value
    assert app.radio(key="execution-target").value == "AgentCore"
    assert app.selectbox(key="presenter-scenario").value == "coastal-resilience"
    assert not app.button(key="run-pipeline").disabled
    assert app.button(key="run-pipeline").label == "Run synthesis"
    assert not app.file_uploader
    assert not app.text_area
    assert not app.text_input
    assert not app.get("download_button")
    assert "Advanced configuration" not in [item.label for item in app.expander]
    assert any("Evidence Extractor" in item.value for item in app.markdown)
    assert any("mapped-risk.pdf" in item.value for item in app.markdown)
    assert {"PDF", "CSV", "JSON", "MD"}.issubset({item.value for item in app.caption})
    assert any("Challenge unsupported claims" in item.value for item in app.markdown)
    discover.assert_not_called()


def test_developer_ui_defaults_to_agentcore_and_harris_county(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_REGION", "us-east-2")
    monkeypatch.delenv("EVIDENCE_SYNTHESIS_REGION", raising=False)
    app = AppTest.from_file(str(APP_PATH)).run(timeout=20)
    app.toggle(key="presenter-mode").set_value(False).run(timeout=20)

    assert not app.exception
    assert app.title[0].value == "Four Agents, One Answer"
    assert app.radio[0].value == "AgentCore"
    assert app.selectbox[0].value == "harris-county"
    region = next(
        field for field in app.text_input if field.label == "AWS region"
    )
    assert region.value == "us-east-1"
    assert app.button(key="run-pipeline").disabled


def test_ui_runs_deterministic_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PIPELINE_TOKEN_BUDGET", "200000")
    app = AppTest.from_file(str(APP_PATH)).run(timeout=20)
    app.toggle(key="presenter-mode").set_value(False).run(timeout=20)

    app.radio[0].set_value("Offline deterministic").run(timeout=20)
    app.button(key="prepare-sources").click().run(timeout=20)

    assert not app.button(key="run-pipeline").disabled
    assert app.get("download_button")[0].label == "Download generated manifest"

    _run_and_wait(app)

    assert not app.exception
    assert app.session_state["current_state"].status.value == "completed"
    assert [tab.label for tab in app.tabs] == [
        "Final answer",
        "Evidence",
        "Claims",
        "Verification",
        "Contradictions",
        "Pipeline",
        "Raw state",
    ]
    assert len(app.dataframe) >= 4


def test_ui_prepares_custom_upload_with_optional_metadata() -> None:
    app = AppTest.from_file(str(APP_PATH)).run(timeout=20)
    app.toggle(key="presenter-mode").set_value(False).run(timeout=20)
    app.radio[0].set_value("Offline deterministic").run(timeout=20)
    app.selectbox[0].set_value("custom").run(timeout=20)
    app.file_uploader[0].set_value(
        [
            ("notes.md", b"Observed evidence.", "text/markdown"),
            (
                "source-metadata.json",
                b'{"notes.md":{"title":"Field notes","methodology":"observations"}}',
                "application/json",
            ),
        ]
    ).run(timeout=20)

    app.button(key="prepare-sources").click().run(timeout=20)

    assert not app.exception
    assert not app.button(key="run-pipeline").disabled
    request = app.session_state["prepared"]["request"]
    assert len(request.sources) == 1
    assert request.sources[0].title == "Field notes"
    assert request.sources[0].methodology == "observations"


def test_presenter_uploads_need_no_metadata_and_replaced_content_invalidates_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    prepare = Mock(wraps=inputs.generate_local_from_folder)
    monkeypatch.setattr(inputs, "generate_local_from_folder", prepare)
    app = AppTest.from_file(str(APP_PATH)).run(timeout=20)
    app.radio(key="execution-target").set_value("Offline deterministic").run(timeout=20)
    app.selectbox(key="presenter-scenario").set_value("custom").run(timeout=20)
    app.file_uploader[0].set_value([
        ("notes.md", b"Observed high water.", "text/markdown"),
    ]).run(timeout=20)
    _run_and_wait(app)

    assert not app.exception
    first = app.session_state["current_state"]
    assert first.status is TerminalStatus.COMPLETED
    assert first.request.sources[0].metadata == {}
    assert first.evidence_extractor.source_profiles[0].origin == "mechanical"
    assert "high water" in first.evidence_extractor.records[0].excerpt

    app.file_uploader[0].set_value([
        ("notes.md", b"Observed mild water.", "text/markdown"),
    ]).run(timeout=20)
    assert not app.tabs
    _run_and_wait(app)

    assert not app.exception
    second = app.session_state["current_state"]
    assert second.status is TerminalStatus.COMPLETED
    assert second.run_id != first.run_id
    assert "mild water" in second.evidence_extractor.records[0].excerpt
    assert prepare.call_count == 2


def test_presenter_detects_changed_fixture_contents(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    scenario = inputs.SCENARIOS["coastal-resilience"]
    folder = tmp_path / "fixture"
    shutil.copytree(scenario.folder, folder)
    monkeypatch.setitem(inputs.SCENARIOS, scenario.key, replace(scenario, folder=folder))
    prepare = Mock(wraps=inputs.generate_local_from_folder)
    monkeypatch.setattr(inputs, "generate_local_from_folder", prepare)
    app = AppTest.from_file(str(APP_PATH)).run(timeout=20)
    app.radio(key="execution-target").set_value("Offline deterministic").run(timeout=20)
    _run_and_wait(app)
    original_fingerprint = app.session_state["prepared"]["fingerprint"]

    source = folder / "field-notes.md"
    original = source.read_text(encoding="utf-8")
    source.write_text(original.swapcase(), encoding="utf-8")
    app.run(timeout=20)
    assert not app.tabs
    _run_and_wait(app)

    assert not app.exception
    assert app.session_state["prepared"]["fingerprint"] != original_fingerprint
    assert prepare.call_count == 2


def test_presenter_runs_in_one_click_and_reuses_prepared_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    prepare = Mock(wraps=inputs.generate_local_from_folder)
    monkeypatch.setattr(inputs, "generate_local_from_folder", prepare)
    app = AppTest.from_file(str(APP_PATH)).run(timeout=20)
    app.radio(key="execution-target").set_value("Offline deterministic").run(timeout=20)
    _run_and_wait(app)

    assert not app.exception
    state = app.session_state["current_state"]
    assert state.status.value == "completed"
    assert [tab.label for tab in app.tabs] == ["Answer", "Evidence", "Claims", "Review"]
    assert any("Offline simulation" in item.value for item in app.info)
    assert "Run details" in [item.label for item in app.expander]
    assert "Full answer, citations, and limitations" in [item.label for item in app.expander]
    assert "Download generated manifest" not in [item.label for item in app.get("download_button")]
    assert state.verification is not None
    assert state.verification.contradictions == []
    assert not any("Key caveat:" in item.value for item in app.warning)
    assert any("Supported" in item.value for item in app.markdown)
    assert prepare.call_count == 1

    _run_and_wait(app)

    assert not app.exception
    assert prepare.call_count == 1
    assert app.session_state["current_state"].run_id != state.run_id


def test_presenter_can_prepare_backstage_without_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH)).run(timeout=20)
    app.radio(key="execution-target").set_value("Offline deterministic").run(timeout=20)
    app.button(key="prepare-sources").click().run(timeout=20)

    assert not app.exception
    assert app.session_state["prepared"] is not None
    assert app.session_state["current_state"] is None
    assert app.button(key="prepare-sources").disabled
    assert not list(tmp_path.glob("runs/*"))

    _run_and_wait(app)
    assert app.session_state["current_state"].status.value == "completed"


def test_presenter_reprepares_changed_scenario_and_hides_previous_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    prepare = Mock(wraps=inputs.generate_local_from_folder)
    monkeypatch.setattr(inputs, "generate_local_from_folder", prepare)
    app = AppTest.from_file(str(APP_PATH)).run(timeout=20)
    app.radio(key="execution-target").set_value("Offline deterministic").run(timeout=20)
    _run_and_wait(app)
    app.selectbox(key="presenter-scenario").set_value("harris-county").run(timeout=20)

    assert not app.tabs
    assert not app.button(key="run-pipeline").disabled
    _run_and_wait(app)

    assert not app.exception
    assert prepare.call_count == 2
    assert "Harris County" in app.session_state["current_state"].request.question


def test_presenter_preparation_failure_does_not_run_or_switch_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(aws, "discover_settings", Mock(side_effect=RuntimeError("AWS unavailable")))
    offline = Mock(side_effect=AssertionError("Must not silently switch to offline"))
    monkeypatch.setattr(runners, "run_deterministic", offline)
    app = AppTest.from_file(str(APP_PATH)).run(timeout=20)
    app.button(key="run-pipeline").click().run(timeout=20)

    assert not app.exception
    assert any("AWS unavailable" in item.value for item in app.error)
    assert app.radio(key="execution-target").value == "AgentCore"
    assert app.session_state["current_state"] is None
    assert "prepared" not in app.session_state
    offline.assert_not_called()


def test_developer_aws_overrides_survive_switch_to_presenter() -> None:
    app = AppTest.from_file(str(APP_PATH)).run(timeout=20)
    app.toggle(key="presenter-mode").set_value(False).run(timeout=20)
    region = next(field for field in app.text_input if field.label == "AWS region")
    region.set_value("us-west-2").run(timeout=20)
    app.toggle(key="presenter-mode").set_value(True).run(timeout=20)

    assert not app.text_input
    assert app.session_state["aws_overrides"]["region"] == "us-west-2"
    app.run(timeout=20)
    app.toggle(key="presenter-mode").set_value(False).run(timeout=20)
    region = next(field for field in app.text_input if field.label == "AWS region")
    assert region.value == "us-west-2"


@pytest.mark.parametrize("invocation_fails", [False, True])
def test_presenter_waits_for_the_agentcore_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, invocation_fails: bool,
) -> None:
    _, request = inputs.load_scenario("coastal-resilience")
    settings = aws.AwsSettings(
        region="us-east-1", runtime_arn="test-runtime", qualifier="live",
        input_bucket="test-input", invoker_role_arn="test-role",
    )
    synthesis = SynthesisResult(answer="An answer from the existing runtime.")
    state = ResearchState(
        run_id="test-run", request=request, status=TerminalStatus.COMPLETED,
        synthesis=synthesis,
    )
    def invoke_result(sent_request: object, **_: object) -> ResearchState:
        if invocation_fails:
            raise RuntimeError("Invocation failed")
        return state.model_copy(update={
            "run_id": sent_request.run_id, "request": sent_request,
        })

    invoke = Mock(side_effect=invoke_result)
    clients = SimpleNamespace(
        runtime=SimpleNamespace(invoke=invoke), s3=Mock(),
        artifact_reader=lambda settings, run_id: FileArtifactReader(tmp_path, run_id),
        log_reader=lambda settings, run_id: RunLogReader(run_id),
    )
    upload = Mock(return_value=request)
    offline = Mock(side_effect=AssertionError("No implicit offline fallback"))
    monkeypatch.setattr(aws, "discover_settings", Mock(return_value=settings))
    monkeypatch.setattr(aws, "create_demo_clients", Mock(return_value=clients))
    monkeypatch.setattr(inputs, "agentcore_request", upload)
    monkeypatch.setattr(runners, "run_deterministic", offline)

    app = AppTest.from_file(str(APP_PATH)).run(timeout=20)
    _run_and_wait(app)

    assert not app.exception
    upload.assert_called_once()
    invoke.assert_called_once()
    sent_request = invoke.call_args.args[0]
    assert sent_request.run_id is not None
    assert sent_request.model_dump(exclude={"run_id"}) == request.model_dump(exclude={"run_id"})
    assert invoke.call_args.kwargs["runtime_arn"] == settings.runtime_arn
    assert invoke.call_args.kwargs["qualifier"] == "live"
    offline.assert_not_called()
    if invocation_fails:
        assert app.session_state["current_state"] is None
        assert any("Invocation failed" in item.value for item in app.error)
    else:
        assert app.session_state["current_state"].run_id == sent_request.run_id
        assert any(synthesis.answer in item.value for item in app.markdown)


def test_ui_polls_saved_artifacts_while_one_invocation_is_pending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from evidence_synthesis.artifacts import FileArtifactStore
    from evidence_synthesis.models import EvidenceExtractorResult, EvidenceRecord

    monkeypatch.chdir(tmp_path)
    saved = threading.Event()
    release = threading.Event()
    extraction = EvidenceExtractorResult(records=[EvidenceRecord(
        evidence_id="E001", source_id="notes", title="Notes",
        excerpt="Evidence visible before the final response.",
        locator="notes.md#paragraph=1", relevance=1,
    )])

    def invoke(request: object, **_: object) -> ResearchState:
        FileArtifactStore(Path("runs")).save_stage(request.run_id, "evidence_extractor", extraction)
        logger = logging.getLogger("evidence_synthesis.orchestrator")
        for stage, status in [("evidence_extractor", "completed"), ("analyst", "started")]:
            logger.info(json.dumps({
                "event": "evidence_synthesis", "run_id": request.run_id,
                "stage": stage, "status": status, "attempt": 1,
            }))
        saved.set()
        release.wait(timeout=20)
        return ResearchState(
            run_id=request.run_id, request=request, status=TerminalStatus.COMPLETED,
            evidence_extractor=extraction, synthesis=SynthesisResult(answer="Final answer."),
        )

    operation = Mock(side_effect=invoke)
    monkeypatch.setattr(runners, "run_deterministic", operation)
    app = AppTest.from_file(str(APP_PATH)).run(timeout=20)
    app.radio(key="execution-target").set_value("Offline deterministic").run(timeout=20)
    try:
        app.button(key="run-pipeline").click().run(timeout=20)
        assert saved.wait(timeout=5)
        app.run(timeout=20)
        assert not app.exception
        assert app.session_state["current_state"] is None
        assert app.button(key="run-pipeline").disabled
        assert any(
            'class="pipeline-stage pipeline-stage-complete"' in item.value for item in app.markdown
        )
        assert any(
            'class="pipeline-stage pipeline-stage-running"' in item.value for item in app.markdown
        )
        assert any("Waiting for logs" in item.value for item in app.markdown)
        assert any("Evidence found in the supplied files" in item.value for item in app.subheader)
        app.run(timeout=20)
        operation.assert_called_once()
    finally:
        release.set()
    active = app.session_state["active_run"]
    active.future.result(timeout=5)
    app.run(timeout=20)
    assert not app.exception
    assert app.session_state["current_state"].synthesis.answer == "Final answer."
    assert not any(
        'class="pipeline-stage pipeline-stage-running"' in item.value for item in app.markdown
    )
    assert not app.button(key="run-pipeline").disabled
    operation.assert_called_once()
