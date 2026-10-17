# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

from html import escape
from typing import Any

import streamlit as st

from evidence_synthesis.models import (
    AnalysisResult,
    EvidenceExtractorResult,
    ResearchState,
    SourceProfile,
    SynthesisResult,
    VerificationResult,
    VerificationStatus,
)

STAGE_LABELS = {
    "evidence_extractor": "Evidence Extractor",
    "analyst": "Analyst",
    "verifier": "Verifier",
    "synthesizer": "Synthesizer",
}
STAGE_DESCRIPTIONS = {
    "evidence_extractor": "Identify the sources and extract relevant evidence.",
    "analyst": "Explain what the evidence suggests.",
    "verifier": "Challenge unsupported claims and missing links.",
    "synthesizer": "Write a cited answer, including caveats.",
}
VERIFICATION_LABELS = {
    VerificationStatus.VERIFIED: "Supported",
    VerificationStatus.QUALIFIED: "Needs qualification",
    VerificationStatus.REJECTED: "Unsupported",
}


def stage_status_class(status: str) -> str:
    if not status or status.startswith(("Waiting", "Not reached")):
        return "waiting"
    if status.startswith(("Running", "Revising", "Preparing")):
        return "running"
    if status.startswith(("Failed", "Timed out", "Blocked")):
        return "failed"
    if status.startswith("Partial"):
        return "warning"
    return "complete"


def render_pipeline(statuses: dict[str, str] | None = None) -> None:
    columns = st.columns(4)
    for column, stage in zip(columns, STAGE_LABELS, strict=True):
        status = statuses.get(stage, "") if statuses else ""
        state_class = stage_status_class(status)
        with column:
            st.markdown(
                (
                    f'<div class="pipeline-stage pipeline-stage-{state_class}">'
                    '<div class="pipeline-stage-heading">'
                    f'<span>{escape(STAGE_LABELS[stage])}</span>'
                    "</div>"
                    '<div class="pipeline-stage-description">'
                    f'{escape(STAGE_DESCRIPTIONS[stage])}</div>'
                    f'<div class="pipeline-stage-status">{escape(status)}</div>'
                    "</div>"
                ),
                unsafe_allow_html=True,
            )


def render_evidence_extractor(result: EvidenceExtractorResult) -> None:
    if result.source_profiles:
        render_source_profiles(result.source_profiles)
    st.subheader("Evidence found in the supplied files")
    rows = [
        {
            "Evidence": record.evidence_id,
            "Source": record.source_id,
            "Title": record.title,
            "Relevance": record.relevance,
            "Excerpt": record.excerpt,
            "Locator": record.locator,
            "Legacy methodology (unverified hint)": record.methodology,
            "Legacy context (unverified hints)": str(record.metadata),
        }
        for record in result.records
    ]
    st.dataframe(
        [{key: row[key] for key in ("Evidence", "Title", "Excerpt")} for row in rows],
        width="stretch", hide_index=True,
    )
    with st.expander("Evidence details"):
        st.dataframe(rows, width="stretch", hide_index=True)
    if result.gaps:
        st.warning("\n".join(result.gaps))


def render_source_profiles(profiles: list[SourceProfile]) -> None:
    st.subheader("Understanding the sources")
    st.caption(
        "Model descriptions are derived interpretations, not independent evidence. "
        "Supporting passages are checked against the supplied files."
    )
    for profile in profiles:
        with st.container(border=True):
            st.markdown(f"**{profile.title}**")
            st.caption(
                f"{profile.source_id} · "
                + ("Model-derived description" if profile.origin == "extracted"
                   else "File information only; titles may include caller-supplied names")
            )
            st.write(profile.description)
            if profile.references or profile.context:
                with st.expander("Relevant context and supporting passages"):
                    for reference in profile.references:
                        st.caption(reference.locator)
                        st.code(reference.excerpt, language=None)
                    for context in profile.context:
                        st.write(context.note)
                        for reference in context.references:
                            st.caption(reference.locator)
                            st.code(reference.excerpt, language=None)


def render_analysis(result: AnalysisResult) -> None:
    st.subheader("Claims produced by Analyst")
    rows = [
        {
            "Claim": claim.claim_id,
            "Confidence": claim.confidence,
            "Topic": claim.topic,
            "Stance": claim.stance or "",
            "Evidence": ", ".join(claim.evidence_ids),
            "Statement": claim.statement,
        }
        for claim in result.claims
    ]
    st.dataframe(
        [{key: row[key] for key in ("Claim", "Statement", "Evidence")} for row in rows],
        width="stretch", hide_index=True,
    )
    with st.expander("Claim details and confidence scores"):
        st.caption("Scores are agent-provided estimates, not calibrated probabilities.")
        st.dataframe(rows, width="stretch", hide_index=True)
    if result.evidence_gaps:
        st.warning("\n".join(result.evidence_gaps))


def render_verification(result: VerificationResult) -> None:
    st.subheader("How well do the sources support each claim?")
    st.caption("This checks support in the supplied evidence, not real-world truth.")
    rows = [
        {
            "Claim": verified.claim.claim_id,
            "Status": VERIFICATION_LABELS[verified.status],
            "Referenced sources": verified.independent_source_count,
            "Reason": verified.reason,
            "Statement": verified.claim.statement,
        }
        for verified in result.claims
    ]
    st.dataframe(rows, width="stretch", hide_index=True)
    with st.expander("Citation and review details"):
        st.markdown(
            f"**Citation closure:** "
            f"{'Complete' if result.citation_closure_complete else 'Incomplete'}"
        )
        if result.revision_feedback:
            st.markdown("**Requested Analyst revision**")
            st.info("\n".join(result.revision_feedback))
    if result.evidence_gaps:
        st.warning("\n".join(result.evidence_gaps))


def render_contradictions(result: VerificationResult) -> None:
    st.subheader("Contradictions and methodological differences")
    if not result.contradictions:
        st.info("No contradictions were reported.")
        return
    for contradiction in result.contradictions:
        with st.container(border=True):
            st.markdown(f"**{contradiction.topic}**")
            st.write(contradiction.explanation)
            st.caption(contradiction.methodology_note)
            st.write(f"Resolution: {contradiction.resolution}")


def render_synthesis(
    result: SynthesisResult,
    *,
    presenter_mode: bool = False,
    verification: VerificationResult | None = None,
) -> None:
    if presenter_mode:
        _render_answer_preview(result, verification)
        with st.expander("Full answer, citations, and limitations"):
            render_synthesis(result)
        return
    st.subheader("Final synthesis")
    if result.partial:
        st.warning(result.terminal_reason or "The synthesis is partial.")
    st.markdown(result.answer)
    if result.citations:
        st.subheader("Citations")
        st.dataframe(
            [
                {
                    "Evidence": citation.evidence_id,
                    "Source": citation.source_id,
                    "Title": citation.title,
                    "Locator": citation.locator,
                }
                for citation in result.citations
            ],
            width="stretch",
            hide_index=True,
        )
    if result.limitations:
        st.subheader("Limitations")
        for limitation in result.limitations:
            st.write(f"- {limitation}")


def _render_answer_preview(
    result: SynthesisResult, verification: VerificationResult | None,
) -> None:
    st.subheader("Answer at a glance")
    st.caption(
        "A short view of the existing results. Expand the full answer for all findings and caveats."
    )
    if result.partial:
        st.warning(result.terminal_reason or "The synthesis is partial.")
    opening = result.answer.strip().split("\n\n", maxsplit=1)[0]
    if not opening.startswith("Evidence synthesis for:"):
        st.markdown(opening)
    if verification is not None and result.safety_action == "NONE":
        usable = [
            item for item in verification.claims if item.status is not VerificationStatus.REJECTED
        ]
        if usable:
            st.markdown("**First three supported or qualified claims**")
            for item in usable[:3]:
                citations = " ".join(f"[{evidence_id}]" for evidence_id in item.claim.evidence_ids)
                st.markdown(
                    f"- **{VERIFICATION_LABELS[item.status]}:** {item.claim.statement} {citations}"
                )
        else:
            st.info("No claims survived review.")
    limitations = list(result.limitations)
    if verification is not None and result.safety_action == "NONE":
        limitations.extend(verification.evidence_gaps)
        limitations.extend(item.explanation for item in verification.contradictions)
    if limitations:
        st.warning(f"Key caveat: {limitations[0]}")


def render_terminal_banner(state: ResearchState, *, presenter_mode: bool = False) -> None:
    message = (
        f"Run {state.run_id}: {state.status.value}. "
        f"Tokens: {state.total_tokens:,}. "
        f"Verification cycles: {state.verification_cycles}."
    )
    if presenter_mode:
        message = {
            "completed": "Synthesis complete · answer and evidence ready to explore.",
            "blocked": "Synthesis blocked by the safety policy.",
            "partial": "Partial result · the pipeline did not complete.",
            "failed": "The pipeline could not complete.",
        }[state.status.value]
    if state.status.value == "completed":
        st.success(message)
    elif state.status.value == "blocked":
        st.error(message)
    else:
        st.warning(message)
    if state.terminal_reason:
        st.caption(state.terminal_reason)
    if state.summary_only:
        st.info("Recovered the final answer. Rerun to view the detailed evidence.")
    if not state.token_usage_complete:
        st.warning("Token usage is incomplete for this run.")


def render_metrics(state: ResearchState) -> None:
    st.subheader("Pipeline metrics")
    rows = [
        {
            "Role": STAGE_LABELS[metric.role.value],
            "Model": metric.model_id,
            "Latency ms": round(metric.latency_ms, 1),
            "Input tokens": metric.input_tokens,
            "Output tokens": metric.output_tokens,
            "Total tokens": metric.total_tokens,
            "Attempts": metric.attempts,
        }
        for metric in state.metrics
    ]
    if rows:
        st.bar_chart(rows, x="Role", y="Total tokens")
        st.dataframe(rows, width="stretch", hide_index=True)
    st.subheader("Event timeline")
    st.dataframe(
        [
            {
                "Timestamp": event.timestamp.isoformat(),
                "Stage": event.stage,
                "Status": event.status,
                "Detail": event.detail or "",
            }
            for event in state.events
        ],
        width="stretch",
        hide_index=True,
    )


def render_state(
    state: ResearchState,
    *,
    slots: dict[str, Any],
    presenter_mode: bool = False,
) -> None:
    with slots["answer"].container():
        render_terminal_banner(state, presenter_mode=presenter_mode)
        if state.synthesis is not None:
            render_synthesis(
                state.synthesis, presenter_mode=presenter_mode, verification=state.verification,
            )
    if state.evidence_extractor is not None:
        with slots["evidence_extractor"].container():
            render_evidence_extractor(state.evidence_extractor)
    if state.analysis is not None:
        with slots["analyst"].container():
            render_analysis(state.analysis)
    if state.verification is not None:
        with slots["verifier"].container():
            render_verification(state.verification)
        with slots["contradictions"].container():
            render_contradictions(state.verification)
    with slots["metrics"].container():
        render_metrics(state)
    with slots["raw"].container():
        st.subheader("Raw run state")
        st.download_button(
            "Download state.json",
            data=state.model_dump_json(indent=2),
            file_name=f"{state.run_id}-state.json",
            mime="application/json",
        )
        if state.synthesis is not None:
            st.download_button(
                "Download answer.md",
                data=state.synthesis.answer + "\n",
                file_name=f"{state.run_id}-answer.md",
                mime="text/markdown",
            )
        with st.expander("Show JSON"):
            st.json(state.model_dump(mode="json"))
