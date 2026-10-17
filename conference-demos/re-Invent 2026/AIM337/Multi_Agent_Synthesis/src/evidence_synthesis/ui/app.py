# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import time
import uuid
from functools import partial
from pathlib import Path
from typing import Any

import streamlit as st

from evidence_synthesis.models import (
    AnalysisResult,
    EvidenceExtractorResult,
    RunRequest,
    StageSummary,
    VerificationResult,
)
from evidence_synthesis.ui.artifact_progress import (
    ActiveRun,
    ArtifactReader,
    FileArtifactReader,
    artifact_order,
    start_run,
    state_stage_statuses,
)
from evidence_synthesis.ui.aws import (
    AwsSettings,
    create_demo_clients,
    discover_settings,
)
from evidence_synthesis.ui.inputs import (
    SCENARIOS,
    UploadedSource,
    agentcore_request,
    content_fingerprint,
    load_scenario,
    local_request,
    write_uploaded_sources,
)
from evidence_synthesis.ui.log_progress import LocalLogReader, RunLogReader
from evidence_synthesis.ui.render import (
    render_analysis,
    render_contradictions,
    render_evidence_extractor,
    render_pipeline,
    render_state,
    render_verification,
)
from evidence_synthesis.ui.runners import run_deterministic

BACKEND_AGENTCORE = "AgentCore"
BACKEND_DETERMINISTIC = "Offline deterministic"
CUSTOM_SCENARIO = "custom"


def main() -> None:
    st.set_page_config(
        page_title="Four Agents, One Answer",
        page_icon=None,
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    _initialize_state()
    running = st.session_state.get("active_run") is not None
    presenter_mode = st.sidebar.toggle(
        "Presenter mode", value=True, key="presenter-mode", disabled=running,
    )
    _render_header()
    preparation_message = st.session_state.pop("preparation_message", None)
    if preparation_message:
        st.success(str(preparation_message))

    setup = st.sidebar.expander("Presenter setup") if presenter_mode else st.sidebar
    with setup:
        backend = st.radio(
            "Execution target",
            [BACKEND_AGENTCORE, BACKEND_DETERMINISTIC],
            index=0,
            key="execution-target",
            disabled=running,
        )
        scenario_keys = [*SCENARIOS, CUSTOM_SCENARIO]
        scenario_key = st.selectbox(
            "Scenario",
            scenario_keys,
            index=scenario_keys.index("coastal-resilience") if presenter_mode else 0,
            format_func=_scenario_label,
            key="presenter-scenario" if presenter_mode else "developer-scenario",
            disabled=running,
        )

    scenario_request: RunRequest | None = None
    source_folder: Path | None = None
    uploads: list[Any] = []
    if scenario_key == CUSTOM_SCENARIO:
        default_question = (
            "What conclusion is supported by the supplied evidence?"
        )
        uploads = st.file_uploader(
            "Upload source files",
            type=["json", "csv", "txt", "md", "pdf"],
            accept_multiple_files=True,
            disabled=running,
            help=(
                "Upload up to 20 source files. The Evidence Extractor will describe "
                "their contents and identify relevant context; no metadata file is needed."
            ),
        )
        _render_uploaded_preview(uploads)
    else:
        scenario, scenario_request = load_scenario(scenario_key)
        source_folder = scenario.folder
        default_question = scenario_request.question
        if not presenter_mode:
            st.caption(scenario.description)
            _render_source_preview(scenario_request)

    if presenter_mode and scenario_request is not None:
        question = default_question
        description = (
            "Illustrative demo data, not a real neighborhood assessment."
            if scenario_key == "coastal-resilience" else SCENARIOS[scenario_key].description
        )
        st.caption(f"{SCENARIOS[scenario_key].label} · {description}")
        st.subheader(question)
        for start in range(0, len(scenario_request.sources), 4):
            sources = scenario_request.sources[start:start + 4]
            for column, source in zip(st.columns(len(sources)), sources, strict=True):
                with column.container(border=True):
                    st.markdown(f"**{Path(source.uri).name if source.uri else source.source_id}**")
                    st.caption(
                        Path(source.uri).suffix.lstrip(".").upper() if source.uri else "Inline"
                    )
        st.caption("Source descriptions and relevant context appear in Evidence after extraction.")
    else:
        question = st.text_area(
            "Research question",
            value=default_question,
            height=90,
            key=f"question-{scenario_key}",
            disabled=running,
        )

    if backend == BACKEND_DETERMINISTIC:
        st.info("Offline simulation · hand-coded results, not live model inference.")
    elif presenter_mode:
        st.caption("Live inference · Amazon Bedrock via AgentCore")

    max_evidence, aws_values = _configuration(
        scenario_request, presenter_mode=presenter_mode, disabled=running,
    )
    fingerprint = _fingerprint(
        backend=backend,
        scenario_key=scenario_key,
        question=question,
        max_evidence=max_evidence,
        uploads=uploads,
        aws_values=tuple(aws_values.values()),
        source_folder=source_folder,
    )
    prepared = st.session_state.get("prepared")
    prepared_is_current = bool(
        prepared and prepared["fingerprint"] == fingerprint
    )

    if presenter_mode:
        with setup:
            st.caption("Prepare before going on stage to avoid uploads during the demo.")
            prepare_clicked = st.button(
                "Prepare sources", key="prepare-sources", disabled=running or prepared_is_current,
            )
            st.caption("Turn off Presenter mode for advanced settings and raw artifacts.")
        run_clicked = st.button(
            "Run synthesis",
            type="primary",
            width="stretch",
            key="run-pipeline",
            disabled=running,
        )
    else:
        prepare_column, run_column = st.columns([1, 1])
        with prepare_column:
            prepare_clicked = st.button(
                "Prepare sources", type="secondary", width="stretch", key="prepare-sources",
                disabled=running,
            )
        with run_column:
            run_clicked = st.button(
                "Run analysis", type="primary", width="stretch",
                disabled=running or not prepared_is_current, key="run-pipeline",
            )

    if prepare_clicked or (presenter_mode and run_clicked and not prepared_is_current):
        try:
            with st.spinner("Preparing sources…"):
                prepared = _prepare(
                    backend=backend,
                    scenario_key=scenario_key,
                    scenario_folder=source_folder,
                    uploads=uploads,
                    question=question,
                    max_evidence=max_evidence,
                    fingerprint=fingerprint,
                    **aws_values,
                )
            st.session_state["prepared"] = prepared
            prepared_is_current = True
            if prepare_clicked:
                st.session_state["preparation_message"] = (
                    f"Prepared {len(prepared['request'].sources)} sources."
                )
                st.rerun()
        except Exception as error:
            st.session_state.pop("prepared", None)
            st.session_state["current_state"] = None
            st.error(str(error).strip() or type(error).__name__)
            prepared = None
            prepared_is_current = False

    if prepared and prepared_is_current:
        request: RunRequest = prepared["request"]
        if not presenter_mode:
            st.download_button(
                "Download generated manifest",
                data=request.model_dump_json(indent=2),
                file_name="evidence-synthesis-manifest.json",
                mime="application/json",
            )
        st.caption(
            f"{len(request.sources)} sources ready · {prepared['backend']}"
        )
    elif prepared:
        st.warning(
            "Inputs changed. Run synthesis will prepare the new sources."
            if presenter_mode else "Inputs changed after preparation. Prepare sources again."
        )
    elif presenter_mode:
        st.caption("Sources will be prepared automatically when you run.")

    if run_clicked and prepared_is_current and prepared and not running:
        try:
            _start_prepared_run(prepared)
            st.rerun()
        except Exception as error:
            st.session_state["run_error"] = str(error).strip() or type(error).__name__

    current_state = st.session_state.get("current_state")
    if st.session_state.get("active_run") is not None:
        _render_active_run(presenter_mode=presenter_mode)
    elif current_state is not None and prepared_is_current:
        render_pipeline(state_stage_statuses(current_state))
        st.divider()
        slots = _result_slots(presenter_mode=presenter_mode)
        render_state(current_state, slots=slots, presenter_mode=presenter_mode)
    else:
        render_pipeline()
        if st.session_state.get("run_error"):
            st.error(st.session_state["run_error"])
            if st.session_state.get("saved_outputs"):
                slots = _result_slots(presenter_mode=presenter_mode)
                _render_saved_outputs(st.session_state["saved_outputs"], slots)


def _configuration(
    request: RunRequest | None, *, presenter_mode: bool, disabled: bool = False,
) -> tuple[int, dict[str, str]]:
    defaults = {
        "region": os.getenv("EVIDENCE_SYNTHESIS_REGION", "us-east-1"),
        "runtime_arn": os.getenv("EVIDENCE_SYNTHESIS_RUNTIME_ARN", ""),
        "qualifier": os.getenv("EVIDENCE_SYNTHESIS_ENDPOINT_NAME", ""),
        "input_bucket": os.getenv("EVIDENCE_SYNTHESIS_INPUT_BUCKET", ""),
        "results_bucket": os.getenv("EVIDENCE_SYNTHESIS_RESULTS_BUCKET", ""),
        "invoker_role_arn": os.getenv("EVIDENCE_SYNTHESIS_INVOKER_ROLE_ARN", ""),
    }
    values = {**defaults, **st.session_state.get("aws_overrides", {})}
    max_evidence = request.max_evidence if request else 20
    if not presenter_mode:
        with st.expander("Advanced configuration"):
            max_evidence = st.slider(
                "Maximum evidence records", 1, 100, max_evidence, disabled=disabled,
            )
            labels = {
                "region": "AWS region",
                "runtime_arn": "AgentCore runtime ARN override",
                "qualifier": "Endpoint qualifier override",
                "input_bucket": "Input bucket override",
                "results_bucket": "Results bucket override",
                "invoker_role_arn": "Invoker role ARN override",
            }
            values = {
                key: st.text_input(label, value=values[key], disabled=disabled)
                for key, label in labels.items()
            }
        st.session_state["aws_overrides"] = values
    return max_evidence, values


def _initialize_state() -> None:
    st.session_state.setdefault("prepared", None)
    st.session_state.setdefault("current_state", None)
    st.session_state.setdefault("preparation_message", None)
    st.session_state.setdefault("active_run", None)
    st.session_state.setdefault("run_error", None)
    st.session_state.setdefault("saved_outputs", {})


def _render_header() -> None:
    st.markdown(
        """
        <style>
        .pipeline-stage {
            min-height: 104px;
            padding: 1rem 1.1rem;
            border: 1px solid rgba(148, 163, 184, 0.28);
            border-left-width: 5px;
            border-radius: 0.65rem;
            background: rgba(30, 41, 59, 0.58);
        }
        .pipeline-stage-heading {
            display: flex;
            align-items: center;
            gap: 0.55rem;
            font-weight: 600;
            letter-spacing: 0.01em;
        }
        .pipeline-stage-description {
            margin-top: 0.65rem;
            min-height: 3em;
            color: #cbd5e1;
            font-size: 0.95rem;
            line-height: 1.5;
        }
        .pipeline-stage-complete {
            border-left-color: #22c55e;
        }
        .pipeline-stage-running, .pipeline-stage-warning {
            border-color: #eab308;
            background: rgba(234, 179, 8, 0.14);
        }
        .pipeline-stage-failed {
            border-color: #ef4444;
            background: rgba(239, 68, 68, 0.12);
        }
        .pipeline-stage-status {
            margin-top: 0.75rem;
            color: #cbd5e1;
            font-size: 0.9rem;
        }
        .pipeline-stage-complete .pipeline-stage-status {
            color: #86efac;
        }
        .pipeline-stage-running .pipeline-stage-status,
        .pipeline-stage-warning .pipeline-stage-status {
            color: #fde047;
        }
        .pipeline-stage-failed .pipeline-stage-status {
            color: #fca5a5;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.title("Four Agents, One Answer")
    st.markdown(
        "Find the evidence. Explain what it suggests. Challenge the claims. "
        "Write an answer you can trace."
    )


def _scenario_label(key: str) -> str:
    if key == CUSTOM_SCENARIO:
        return "Custom upload"
    return SCENARIOS[key].label


def _render_source_preview(request: RunRequest) -> None:
    st.dataframe(
        [
            {
                "Source": source.source_id,
                "File": Path(source.uri).name if source.uri else "inline",
                "Format": Path(source.uri).suffix.lstrip(".") if source.uri else source.media_type,
                "Location": source.uri or "inline",
            }
            for source in request.sources
        ],
        width="stretch",
        hide_index=True,
    )


def _render_uploaded_preview(uploads: list[Any]) -> None:
    if not uploads:
        st.info("Select source files to prepare a custom run.")
        return
    st.dataframe(
        [
            {
                "File": upload.name,
                "Size MB": round(upload.size / (1024 * 1024), 2),
            }
            for upload in uploads
        ],
        width="stretch",
        hide_index=True,
    )


def _fingerprint(
    *,
    backend: str,
    scenario_key: str,
    question: str,
    max_evidence: int,
    uploads: list[Any],
    aws_values: tuple[str, ...],
    source_folder: Path | None = None,
) -> str:
    parts = [
        backend,
        scenario_key,
        question,
        str(max_evidence),
        *aws_values,
        content_fingerprint(
            source_folder,
            [UploadedSource(upload.name, upload.getvalue()) for upload in uploads],
        ),
    ]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def _prepare(
    *,
    backend: str,
    scenario_key: str,
    scenario_folder: Path | None,
    uploads: list[Any],
    question: str,
    max_evidence: int,
    fingerprint: str,
    region: str,
    runtime_arn: str,
    qualifier: str,
    input_bucket: str,
    invoker_role_arn: str,
    results_bucket: str = "",
) -> dict[str, Any]:
    _remove_previous_upload_directory()
    if scenario_key == CUSTOM_SCENARIO:
        folder = Path(tempfile.mkdtemp(prefix="evidence-synthesis-ui-"))
        st.session_state["upload_directory"] = str(folder)
        write_uploaded_sources(
            [
                UploadedSource(name=upload.name, data=upload.getvalue())
                for upload in uploads
            ],
            folder,
        )
    else:
        assert scenario_folder is not None
        folder = scenario_folder

    settings: AwsSettings | None = None
    if backend == BACKEND_AGENTCORE:
        settings = discover_settings(
            region=region,
            runtime_arn=runtime_arn,
            qualifier=qualifier,
            input_bucket=input_bucket,
            invoker_role_arn=invoker_role_arn,
            results_bucket=results_bucket,
        )
        clients = create_demo_clients(settings)
        batch_id = uuid.uuid4().hex
        request = agentcore_request(
            folder,
            question=question,
            max_evidence=max_evidence,
            bucket=settings.input_bucket,
            prefix=f"sources/ui/{batch_id}",
            s3_client=clients.s3,
        )
    else:
        request = local_request(
            folder,
            question=question,
            max_evidence=max_evidence,
        )

    st.session_state["current_state"] = None
    st.session_state["run_error"] = None
    st.session_state["saved_outputs"] = {}
    return {
        "backend": backend,
        "fingerprint": fingerprint,
        "request": request,
        "base_dir": folder,
        "aws_settings": settings,
    }


def _remove_previous_upload_directory() -> None:
    value = st.session_state.pop("upload_directory", None)
    if not value:
        return
    path = Path(value)
    if path.name.startswith("evidence-synthesis-ui-"):
        shutil.rmtree(path, ignore_errors=True)


def _start_prepared_run(prepared: dict[str, Any]) -> None:
    request: RunRequest = prepared["request"].model_copy(update={"run_id": uuid.uuid4().hex})
    assert request.run_id is not None
    backend = str(prepared["backend"])
    st.session_state["current_state"] = None
    st.session_state["run_error"] = None
    st.session_state["saved_outputs"] = {}
    reader: ArtifactReader
    logs: RunLogReader
    if backend == BACKEND_AGENTCORE:
        settings: AwsSettings = prepared["aws_settings"]
        clients = create_demo_clients(settings)
        reader = clients.artifact_reader(settings, request.run_id)
        logs = clients.log_reader(settings, request.run_id)
        invoke = partial(
            clients.runtime.invoke,
            request,
            runtime_arn=settings.runtime_arn,
            qualifier=settings.qualifier,
            session_id=f"evidence-synthesis-ui-{request.run_id}",
        )
    else:
        reader = FileArtifactReader(Path("runs"), request.run_id)
        logs = LocalLogReader(request.run_id)
        invoke = partial(
            run_deterministic, request, base_dir=prepared["base_dir"], output_dir=Path("runs"),
        )
    st.session_state["active_run"] = start_run(request, reader, invoke, logs=logs)


@st.fragment(run_every=2)
def _render_active_run(*, presenter_mode: bool) -> None:
    run: ActiveRun = st.session_state["active_run"]
    run.poll()
    st.session_state["saved_outputs"] = dict(run.reader.outputs)
    if run.future.done():
        try:
            st.session_state["current_state"] = run.result()
        except Exception as error:
            detail = str(error).strip() or type(error).__name__
            st.session_state["run_error"] = (
                f"Could not receive the final response: {detail}. "
                "Any saved stage results are shown below; the backend may still be processing."
            )
        st.session_state["active_run"] = None
        # A full rerun restores the controls after the request finishes.
        st.rerun()

    elapsed = int(time.monotonic() - run.started_at)
    st.info(f"Synthesis is running · {elapsed // 60}:{elapsed % 60:02d} elapsed")
    st.caption(
        "Yellow: running · Green: complete · Red: failed. "
        "Status follows the latest available log message; log delivery can lag."
    )
    render_pipeline(run.statuses())
    if run.warning:
        st.warning(run.warning)
    if run.log_warning:
        st.warning(run.log_warning)
    slots = _result_slots(presenter_mode=presenter_mode)
    with slots["answer"].container():
        st.info("The final answer will appear after the run and its safety checks finish.")
    _render_saved_outputs(run.reader.outputs, slots)
    with slots["metrics"].container():
        st.caption(f"Run ID: {run.request.run_id}")


def _render_saved_outputs(outputs: dict[str, Any], slots: dict[str, Any]) -> None:
    latest: dict[
        str, EvidenceExtractorResult | AnalysisResult | VerificationResult | StageSummary
    ] = {}
    for name in sorted(outputs, key=artifact_order):
        value = outputs[name]
        if isinstance(value, StageSummary):
            stage = (
                "evidence_extractor" if name == "evidence_extractor"
                else "analyst" if name.startswith("analysis")
                else "verifier" if name.startswith("verification-")
                else None
            )
            if stage:
                latest[stage] = value
        elif isinstance(value, EvidenceExtractorResult):
            latest["evidence_extractor"] = value
        elif isinstance(value, AnalysisResult):
            latest["analyst"] = value
        elif isinstance(value, VerificationResult):
            latest["verifier"] = value
    for stage, value in latest.items():
        with slots[stage].container():
            if isinstance(value, StageSummary):
                title = {
                    "evidence_extractor": "Evidence found in the supplied files",
                    "analyst": "Claims drafted",
                    "verifier": "Claims reviewed",
                }[stage]
                st.subheader(title)
                st.caption(f"{value.item_count} items · details appear when the run finishes.")
            elif isinstance(value, EvidenceExtractorResult):
                render_evidence_extractor(value)
            elif isinstance(value, AnalysisResult):
                render_analysis(value)
            elif isinstance(value, VerificationResult):
                render_verification(value)
        if isinstance(value, VerificationResult):
            with slots["contradictions"].container():
                render_contradictions(value)


def _result_slots(*, presenter_mode: bool = False) -> dict[str, Any]:
    if presenter_mode:
        slots: dict[str, Any] = {}
        tabs = st.tabs(["Answer", "Evidence", "Claims", "Review"])
        names = ["answer", "evidence_extractor", "analyst", "verifier"]
        for name, tab in zip(names, tabs, strict=True):
            with tab:
                slots[name] = st.empty()
                if name == "verifier":
                    slots["contradictions"] = st.empty()
        with st.expander("Run details"):
            slots["metrics"] = st.empty()
            slots["raw"] = st.empty()
        return slots
    tabs = st.tabs(
        [
            "Final answer",
            "Evidence",
            "Claims",
            "Verification",
            "Contradictions",
            "Pipeline",
            "Raw state",
        ]
    )
    names = [
        "answer",
        "evidence_extractor",
        "analyst",
        "verifier",
        "contradictions",
        "metrics",
        "raw",
    ]
    slots = {}
    for name, tab in zip(names, tabs, strict=True):
        with tab:
            slots[name] = st.empty()
    return slots


if __name__ == "__main__":
    main()
