# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
import pytest
from streamlit.testing.v1 import AppTest


def _render_source_description_example(extracted: bool) -> None:
    from evidence_synthesis.models import EvidenceExtractorResult, SourceProfile, SourceReference
    from evidence_synthesis.ui.render import render_evidence_extractor

    render_evidence_extractor(EvidenceExtractorResult(records=[], source_profiles=[SourceProfile(
        source_id="measurements", title="Hourly measurements" if extracted else "measurements.csv",
        description="Hourly observations." if extracted else "No model description available.",
        references=[SourceReference(
            locator="measurements.csv#row=2", excerpt="Measurements were collected hourly.",
        )] if extracted else [],
        origin="extracted" if extracted else "mechanical",
    )]))


@pytest.mark.parametrize("extracted", [False, True])
def test_source_descriptions_distinguish_inference_from_file_details(extracted: bool) -> None:
    app = AppTest.from_function(
        _render_source_description_example, args=(extracted,),
    ).run(timeout=20)

    assert not app.exception
    captions = "\n".join(item.value for item in app.caption)
    assert "not independent evidence" in captions
    assert ("Model-derived description" if extracted else "File information only") in captions
    if extracted:
        assert app.code[0].value == "Measurements were collected hourly."
        assert "measurements.csv#row=2" in captions
    else:
        assert not app.code


def _render_review_example() -> None:
    from evidence_synthesis.models import (
        AnalysisResult,
        Claim,
        VerificationResult,
        VerificationStatus,
        VerifiedClaim,
    )
    from evidence_synthesis.ui.render import render_analysis, render_verification

    claims = [
        Claim(
            claim_id=f"claim-{status.value}", statement=f"Statement {status.value}",
            evidence_ids=["evidence-1"], confidence=0.8, methodology="observations",
        )
        for status in VerificationStatus
    ]
    render_analysis(AnalysisResult(claims=claims))
    render_verification(
        VerificationResult(
            claims=[
                VerifiedClaim(
                    claim=claim, status=status, reason="Review explanation",
                    independent_source_count=1,
                )
                for claim, status in zip(claims, VerificationStatus, strict=True)
            ],
            citation_closure_complete=False,
        )
    )


def test_review_uses_plain_language_and_keeps_scores_in_details() -> None:
    app = AppTest.from_function(_render_review_example).run(timeout=20)

    assert not app.exception
    assert "Confidence" not in app.dataframe[0].value.columns
    assert "Confidence" in app.dataframe[1].value.columns
    assert app.dataframe[2].value["Status"].tolist() == [
        "Supported", "Needs qualification", "Unsupported",
    ]
    assert [item.label for item in app.expander] == [
        "Claim details and confidence scores", "Citation and review details",
    ]


def _render_preview_example(safety_action: str, partial: bool) -> None:
    import streamlit as st

    from evidence_synthesis.models import (
        Claim,
        SynthesisResult,
        VerificationResult,
        VerificationStatus,
        VerifiedClaim,
    )
    from evidence_synthesis.ui.render import render_synthesis

    result = SynthesisResult(
        answer="Original answer opening.\n\nThe complete answer remains unchanged.",
        limitations=["The supplied evidence is limited."],
        safety_action=safety_action, partial=partial,
        terminal_reason="Example partial result" if partial else None,
    )
    verification = VerificationResult(
        claims=[
            VerifiedClaim(
                claim=Claim(
                    claim_id=status.value, statement=f"{status.value} claim text",
                    evidence_ids=["evidence-1"], confidence=0.8, methodology="observations",
                ),
                status=status, reason="Review explanation", independent_source_count=1,
            )
            for status in VerificationStatus
        ],
        citation_closure_complete=False,
    )
    original = result.model_dump_json()
    render_synthesis(result, presenter_mode=True, verification=verification)
    st.session_state["unchanged"] = result.model_dump_json() == original


@pytest.mark.parametrize("partial", [False, True])
def test_preview_preserves_answer_and_excludes_unsupported_claims(partial: bool) -> None:
    app = AppTest.from_function(_render_preview_example, args=("NONE", partial)).run(timeout=20)

    assert not app.exception
    text = "\n".join(item.value for item in app.markdown)
    assert "Original answer opening." in text
    assert "The complete answer remains unchanged." in text
    assert "verified claim text" in text
    assert "qualified claim text" in text
    assert "rejected claim text" not in text
    assert app.session_state["unchanged"]
    assert any("Key caveat:" in item.value for item in app.warning)
    if partial:
        assert any("Example partial result" in item.value for item in app.warning)


def test_preview_does_not_reintroduce_claims_after_safety_intervention() -> None:
    app = AppTest.from_function(
        _render_preview_example, args=("GUARDRAIL_INTERVENED", True),
    ).run(timeout=20)

    assert not app.exception
    text = "\n".join(item.value for item in app.markdown)
    assert "Original answer opening." in text
    assert "claim text" not in text
